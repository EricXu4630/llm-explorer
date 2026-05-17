"""
Anthropic provider — Messages API with native server-side tools, MCP, Skills, and Files API memory.

FILESYSTEM / BASH:
  code_execution_20250825 gives the model a full Linux bash+Python container on Anthropic's servers.
  The model can write todo.md, dump outputs to files, read them back — exactly the Manus pattern.
  Container persists within a session via container_id (saved to sessions/).
  Container expires after 30 days — harness saves container_id to reuse across sessions.
  After expiry: container is gone, need a new one.

SKILLS (native API — progressive disclosure on Anthropic's servers):
  Custom skills from GitHub are uploaded via /v1/skills and referenced by skill_id
  in the container parameter. Anthropic's hosted VM handles the file system and
  progressive disclosure: metadata only at startup, full SKILL.md on trigger.
  No local sandbox or VM needed — Anthropic hosts it.

  Required beta: skills-2025-10-02, code-execution-2025-08-25
  API shape: container={skills:[{type:"custom", skill_id:"..."}]}

MEMORY (Files API — persistent across sessions):
  agents_memory.txt is uploaded to Anthropic's Files API and referenced by file_id
  in every request as a document block (with prompt caching).
  When the model uses the memory_20250818 tool to write, harness updates the local
  file and re-uploads to Files API — new file_id for next request.
  No VM needed — just file upload + document block reference.

  Required beta: files-api-2025-04-14
  API shape: messages[0].content.append({type:"document", source:{type:"file", file_id:"..."}})

SERVER-SIDE tools (Anthropic executes, no client loop):
  web_search_20260209   — web search (NOTE: _20260209 auto-injects code_execution;
                          add allowed_callers=["direct"] when code_execution is also explicit)
  code_execution_20250825 — Python + Bash sandbox (also required for Skills)
  web_fetch_20260209    — URL fetcher

CLIENT-SIDE tools (harness executes):
  memory_20250818       — model uses this to write persistent key-value memory

MCP connector (beta mcp-client-2025-11-20):
  Pass mcp_servers array; Anthropic calls MCP servers on your behalf. No client execution.
  Server must be public HTTPS (SSE or Streamable HTTP). Local STDIO won't work.
"""

import io
import json
import pathlib
import anthropic
from fastapi import WebSocket

from skills_manager import upload_skill, build_container, PRE_BUILT, SKILLS_BETA
from memory_manager import (
    execute_memory_command,
    upload_document, build_file_document_block, FILES_BETA,
)
from session_manager import get_container_id, set_container_id

SERVER_TOOLS = {
    "web_search":    {"type": "web_search_20260209",    "name": "web_search"},
    "code_execution":{"type": "code_execution_20250825","name": "code_execution"},
    "web_fetch":     {"type": "web_fetch_20260209",     "name": "web_fetch"},
}
MEMORY_TOOL = {"type": "memory_20250818", "name": "memory"}

MODELS = [
    "claude-sonnet-4-6",   # default — supports all server-side tools
    "claude-opus-4-7",
    "claude-haiku-4-5",    # ⚠️ does NOT support server-side tools
]

# _20260209 tools use internal code execution — conflict with explicit code_execution
DYNAMIC_TOOLS = {"web_search_20260209", "web_fetch_20260209"}


def _safe_dict(obj):
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_safe_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _safe_dict(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        try:
            return _safe_dict(obj.model_dump())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return _safe_dict({k: v for k, v in obj.__dict__.items() if not k.startswith("_")})
    return str(obj)


def _content_to_param(content_blocks) -> list:
    """Strip extra Pydantic fields — only include what the API accepts as input."""
    SERVER_TYPES = {
        "server_tool_use", "mcp_tool_use",
        "web_search_tool_result", "code_execution_tool_result",
        "bash_code_execution_tool_result", "web_fetch_tool_result",
        "mcp_tool_result",
    }
    result = []
    for block in (content_blocks or []):
        btype = block.get("type", "") if isinstance(block, dict) else str(getattr(block, "type", "") or "")

        if btype in SERVER_TYPES:
            continue
        elif btype == "text":
            text = block.get("text", "") if isinstance(block, dict) else (getattr(block, "text", "") or "")
            result.append({"type": "text", "text": text})
        elif btype == "tool_use":
            if isinstance(block, dict):
                result.append({"type": "tool_use", "id": block.get("id", ""), "name": block.get("name", ""), "input": block.get("input", {})})
            else:
                result.append({"type": "tool_use", "id": getattr(block, "id", ""), "name": getattr(block, "name", ""), "input": _safe_dict(getattr(block, "input", {}))})
        elif btype == "thinking":
            thinking = block.get("thinking", "") if isinstance(block, dict) else (getattr(block, "thinking", "") or "")
            result.append({"type": "thinking", "thinking": thinking})

    return result if result else [{"type": "text", "text": ""}]


AGENTS_MD = pathlib.Path(__file__).parent.parent / "AGENTS.md"
MEMORIES_AGENTS_MD = pathlib.Path(__file__).parent.parent / "memories" / "AGENTS.md"


def _build_system(use_memory: bool, skills: list) -> str:
    # Load AGENTS.md as base system instructions
    if AGENTS_MD.exists():
        base = AGENTS_MD.read_text(encoding="utf-8", errors="replace")
    else:
        base = (
            "You are a capable research assistant with access to powerful tools. "
            "Think step-by-step. Use tools proactively when they help answer the request."
        )

    # Inject skills content into system (for providers that don't use native Skills API)
    if skills:
        skill_text = "\n\n".join(f"### Skill: {s['name']}\n{s['content']}" for s in skills)
        base += f"\n\n## Active Skills\n{skill_text}"

    return base


class AnthropicProvider:
    DEFAULT_MODEL = MODELS[0]

    def __init__(self, api_key: str, model: str = None):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model or self.DEFAULT_MODEL

    async def chat(
        self,
        messages: list,
        tools_config: dict,
        mcp_servers: list,
        skills: list,
        ws: WebSocket,
    ):
        use_code_exec = tools_config.get("code_execution", False)
        use_memory = tools_config.get("memory", False)
        has_skills = bool(skills)

        # ── Resolve betas ──────────────────────────────────────────────────
        betas = []
        if mcp_servers:
            betas.append("mcp-client-2025-11-20")
        if has_skills:
            betas.extend(["code-execution-2025-08-25", SKILLS_BETA])
        if use_memory:
            betas.append(FILES_BETA)

        # ── Upload skills to native Skills API ─────────────────────────────
        # Each skill gets uploaded once (cached by content hash).
        # Anthropic handles progressive disclosure in their container.
        skill_ids = []
        for skill in skills:
            try:
                sid = await upload_skill(self.client, skill)
                skill_ids.append(sid)
                await ws.send_json({
                    "type": "info",
                    "message": f"Skill '{skill['name']}' uploaded → skill_id={sid} (native progressive disclosure)"
                })
            except Exception as e:
                # Fallback: inject into system prompt
                await ws.send_json({
                    "type": "info",
                    "message": f"Skill '{skill['name']}' upload failed ({e}), falling back to system prompt injection"
                })
                skill_ids = []  # disable native skills, fall back
                break

        # ── Build tools list ────────────────────────────────────────────────
        # _20260209 tools auto-inject code_execution — add allowed_callers=["direct"]
        # when code_execution is also explicitly enabled.
        dynamic_in_use = any(
            dict(v)["type"] in DYNAMIC_TOOLS
            for k, v in SERVER_TOOLS.items() if tools_config.get(k)
        )
        conflict_fix = use_code_exec and dynamic_in_use
        if conflict_fix:
            await ws.send_json({
                "type": "info",
                "message": (
                    "_20260209 tools (web_search/web_fetch) internally use code_execution. "
                    "Adding allowed_callers=['direct'] to disable their auto-injection so "
                    "the explicit code_execution tool can coexist."
                ),
            })

        tools = []
        for k, v in SERVER_TOOLS.items():
            if not tools_config.get(k):
                continue
            t = dict(v)
            if t["type"] in DYNAMIC_TOOLS and conflict_fix:
                t["allowed_callers"] = ["direct"]
            tools.append(t)

        # Skills require code_execution_20250825 in tools
        if skill_ids and not any(t.get("type") == "code_execution_20250825" for t in tools):
            tools.append(SERVER_TOOLS["code_execution"])

        if use_memory:
            tools.append(MEMORY_TOOL)

        # ── MCP connector ───────────────────────────────────────────────────
        mcp_defs = []
        for mcp in mcp_servers:
            entry = {"type": "url", "url": mcp["url"], "name": mcp["name"]}
            if mcp.get("token"):
                entry["authorization_token"] = mcp["token"]
            mcp_defs.append(entry)
            tools.append({"type": "mcp_toolset", "mcp_server_name": mcp["name"]})

        # ── Memory (memory_20250818 client-side filesystem tool) ────────────
        # The model uses view/create/str_replace/insert/delete/rename commands
        # against /memories/ — our harness maps these to a local ./memories/ dir.
        # No Files API needed for the memory tool itself.
        augmented_messages = list(messages)

        # ── Assemble request params ─────────────────────────────────────────
        params: dict = {
            "model": self.model,
            "max_tokens": 8192,
            "system": _build_system(use_memory, skills if not skill_ids else []),
            "messages": augmented_messages,
        }
        if tools:
            params["tools"] = tools
        if mcp_defs:
            params["mcp_servers"] = mcp_defs
        # Container: Anthropic's server-side bash/Python filesystem.
        # Persists within a session and across sessions (30-day expiry) via container_id.
        # API shape:
        #   string             → reuse existing container by ID
        #   {"skills":[...]}   → new container with skills
        #   {"id":"...", "skills":[...]} → reuse container AND mount skills
        # Note: container only needed when skills are present or we have a saved ID.
        # When code_execution is used without skills, the API auto-creates a container
        # and returns the id; we save it for the next session.
        # Container is ONLY valid when code_execution or skills are active.
        # Passing container to non-code-execution requests causes 400 errors.
        has_code_exec = any(t.get("type","").startswith("code_execution") for t in tools)
        saved_container_id = get_container_id("default", "anthropic")

        if has_code_exec or skill_ids:
            if skill_ids and saved_container_id:
                container_def = build_container(skill_ids)
                container_def["id"] = saved_container_id
                params["container"] = container_def
                await ws.send_json({"type": "info",
                    "message": f"Reusing container {saved_container_id[:20]}... + skills"})
            elif skill_ids:
                params["container"] = build_container(skill_ids)
            elif saved_container_id:
                params["container"] = saved_container_id  # string — reuse existing
                await ws.send_json({"type": "info",
                    "message": f"Reusing container {saved_container_id[:20]}... (files from previous session available)"})

        await ws.send_json({"type": "api_request", "payload": params})

        try:
            await self._run_loop(params, betas, use_memory, ws)
        except Exception as e:
            err_str = str(e)
            # Container expired — clear saved ID and retry without it
            if "container_expired" in err_str or '"container_expired"' in err_str:
                set_container_id("default", "anthropic", None)
                params.pop("container", None)
                await ws.send_json({"type": "info", "message": "Container expired — cleared, retrying without it."})
                try:
                    await self._run_loop(params, betas, use_memory, ws)
                    return
                except Exception as e2:
                    await ws.send_json({"type": "error", "message": str(e2)})
            else:
                await ws.send_json({"type": "error", "message": err_str})

    async def _run_loop(self, params: dict, betas: list, use_memory: bool, ws: WebSocket):
        """
        Agentic loop for Anthropic.
        - Server-side tools (web_search, code_execution, web_fetch): Anthropic handles internally.
        - MCP: Anthropic calls MCP servers server-side.
        - Skills: executed in Anthropic's hosted container.
        - Memory tool: harness executes (read/write to local memories/ directory).

        Two paths:
        - Streaming (default): tokens arrive progressively, no container_id in response.
        - Non-streaming (when code_execution active): waits for full response, gets container.id.
          streaming get_final_message() omits container info; non-streaming includes it.
        """
        full_text = ""
        current_messages = list(params["messages"])
        tools_list = params.get("tools") or []
        use_nonstream = any(t.get("type", "").startswith("code_execution") for t in tools_list)

        while True:
            loop_params = {**params, "messages": current_messages}

            if use_nonstream:
                # ── Non-streaming: captures container.id for cross-session file reuse ──
                if betas:
                    final = await self.client.beta.messages.create(**loop_params, betas=betas)
                else:
                    final = await self.client.messages.create(**loop_params)

                await ws.send_json({"type": "api_response", "payload": _safe_dict(final)})

                # Save container_id (30-day reuse window)
                ctr = getattr(final, "container", None)
                if ctr:
                    cid = getattr(ctr, "id", None)
                    if cid:
                        set_container_id("default", "anthropic", cid)
                        await ws.send_json({
                            "type": "info",
                            "message": f"Container {cid[:24]}... saved — files persist 30 days",
                        })

                # Emit content
                for block in (final.content or []):
                    btype = str(getattr(block, "type", "") or "")
                    if btype == "text":
                        txt = getattr(block, "text", "") or ""
                        if txt:
                            await ws.send_json({"type": "token", "content": txt})
                            full_text += txt
                    elif btype in ("tool_use", "server_tool_use", "mcp_tool_use"):
                        await ws.send_json({
                            "type": "tool_call",
                            "tool": getattr(block, "name", btype),
                            "input": _safe_dict(getattr(block, "input", {})),
                        })

                memory_tool_calls = [
                    b for b in (final.content or [])
                    if str(getattr(b, "type", "")) == "tool_use"
                ]
                stop_reason = getattr(final, "stop_reason", "end_turn")

            else:
                # ── Streaming: progressive token delivery ──
                if betas:
                    stream_ctx = self.client.beta.messages.stream(**loop_params, betas=betas)
                else:
                    stream_ctx = self.client.messages.stream(**loop_params)

                async with stream_ctx as stream:
                    async for event in stream:
                        etype = getattr(event, "type", "")

                        if etype == "content_block_delta":
                            delta = event.delta
                            if getattr(delta, "type", "") == "text_delta":
                                await ws.send_json({"type": "token", "content": delta.text})
                                full_text += delta.text

                        elif etype == "content_block_start":
                            # tool_use input is {} at this point; full input arrives after stream.
                            block = getattr(event, "content_block", None)
                            if block:
                                btype = str(getattr(block, "type", "") or "")
                                if btype in ("tool_use", "server_tool_use", "mcp_tool_use"):
                                    await ws.send_json({
                                        "type": "tool_call",
                                        "tool": getattr(block, "name", btype),
                                        "input": "...",
                                    })
                                elif btype == "mcp_tool_result":
                                    await ws.send_json({
                                        "type": "tool_result",
                                        "tool": "mcp",
                                        "output": str(getattr(block, "content", "")),
                                    })

                    final = await stream.get_final_message()

                await ws.send_json({"type": "api_response", "payload": _safe_dict(final)})

                # Re-emit tool calls with complete inputs from final.content
                memory_tool_calls = []
                for block in (final.content or []):
                    btype = str(getattr(block, "type", "") or "")
                    if btype == "tool_use":
                        inp = _safe_dict(getattr(block, "input", {})) or {}
                        memory_tool_calls.append(block)
                        await ws.send_json({
                            "type": "tool_call",
                            "tool": getattr(block, "name", "tool"),
                            "input": inp,
                        })
                    elif btype in ("server_tool_use", "mcp_tool_use"):
                        await ws.send_json({
                            "type": "tool_call",
                            "tool": getattr(block, "name", btype),
                            "input": _safe_dict(getattr(block, "input", {})),
                        })

                stop_reason = getattr(final, "stop_reason", "end_turn")

            # ── Common continuation logic ──
            if stop_reason == "pause_turn":
                await ws.send_json({"type": "info", "message": "Continuing (pause_turn)..."})
                current_messages = list(current_messages) + [
                    {"role": "assistant", "content": _content_to_param(final.content)}
                ]
                continue

            if stop_reason == "tool_use" and memory_tool_calls:
                tool_results = []
                for block in memory_tool_calls:
                    name = getattr(block, "name", "")
                    inp = getattr(block, "input", {})
                    if name == "memory" and use_memory:
                        result = execute_memory_command(inp)
                        await ws.send_json({"type": "tool_result", "tool": "memory", "output": result[:200]})
                    else:
                        result = json.dumps({"error": f"Unhandled client tool: {name}"})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": getattr(block, "id", ""),
                        "content": result,
                    })

                current_messages = list(current_messages) + [
                    {"role": "assistant", "content": _content_to_param(final.content)},
                    {"role": "user", "content": tool_results},
                ]
                continue

            break

        await ws.send_json({"type": "done", "message": full_text})

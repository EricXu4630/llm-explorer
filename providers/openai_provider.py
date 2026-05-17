"""
OpenAI provider — Responses API.

FILESYSTEM / BASH (Shell tool):
  OpenAI's Shell tool provides a hosted Debian 12 container with /mnt/data filesystem.
  The model writes todo.md, dumps tool outputs, reads files — same as Manus/Anthropic.
  Container persists while active (configurable expiry — default: 20 min after last use).
  Container ID saved to sessions/ and reused across API calls.

  Shell tool config:
    type: "shell"
    environment: {type: "hosted"}   — OpenAI runs the container

  NOTE: Shell tool is different from code_interpreter.
  - code_interpreter: Python only, sandboxed, generates outputs
  - shell: Full bash, /mnt/data filesystem, files persist across turns

CONVERSATION PERSISTENCE:
  - previous_response_id: chains responses (30-day TTL by default)
  - Conversations API: no TTL — indefinite persistence
  - We save previous_response_id to sessions/ for cross-session resumption

NATIVE SERVER-SIDE TOOLS (OpenAI executes, no client loop):
  web_search_preview  — real-time web search
  code_interpreter    — Python execution (hosted, container: {type: "auto"})
  mcp (type:"mcp")    — calls your MCP server natively

FILES API:
  30-day default retention. Can be referenced across sessions within 30 days.
  (vs Gemini: 48h only; vs Anthropic: indefinite)
"""

import io
import json
import zipfile
import hashlib
import pathlib
from openai import AsyncOpenAI
from fastapi import WebSocket
from session_manager import get_container_id, set_container_id

_AGENTS_MD = pathlib.Path(__file__).parent.parent / "AGENTS.md"
_SKILL_CACHE = pathlib.Path(__file__).parent.parent / "openai_skill_ids.json"


def _load_skill_cache() -> dict:
    if _SKILL_CACHE.exists():
        try: return json.loads(_SKILL_CACHE.read_text())
        except Exception: pass
    return {}

def _save_skill_cache(cache: dict):
    _SKILL_CACHE.write_text(json.dumps(cache, indent=2))

def _skill_to_zip(skill: dict) -> bytes:
    name = skill["name"].lower().replace(" ", "-")
    skill_md = f"---\nname: {name}\ndescription: {skill['description']}\n---\n\n{skill['content']}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}/SKILL.md", skill_md)
    return buf.getvalue()

async def upload_skill_openai(client: AsyncOpenAI, skill: dict) -> str:
    """Upload a skill to OpenAI Skills API. Returns skill_id (cached)."""
    cache = _load_skill_cache()
    key = hashlib.sha1(f"{skill['name']}{skill['content'][:100]}".encode()).hexdigest()[:12]
    if key in cache:
        return cache[key]
    name = skill["name"].lower().replace(" ", "-")
    zip_bytes = _skill_to_zip(skill)
    result = await client.skills.create(
        files=[(f"{name}.zip", io.BytesIO(zip_bytes), "application/zip")]
    )
    skill_id = result.id
    cache[key] = skill_id
    _save_skill_cache(cache)
    return skill_id

# Tool definitions verified against https://developers.openai.com/api/docs/guides/tools (2026-05-17)
SERVER_TOOLS = {
    "web_search":       {"type": "web_search_preview"},
    "code_interpreter": {"type": "code_interpreter", "container": {"type": "auto"}},
    "image_generation": {"type": "image_generation"},  # Uses gpt-image-2 server-side
    # file_search requires vector_store_ids — skip if not provided
}

# Shell tool — OpenAI manages container; /mnt/data filesystem persists across turns.
# environment.type = "container_auto": OpenAI provisions + manages the container.
# "hosted" was the old name; current API uses "container_auto".
SHELL_TOOL = {
    "type": "shell",
    "environment": {"type": "container_auto"},
}

# Models verified against https://developers.openai.com/api/docs/models/all (2026-05-17)
MODELS = [
    "gpt-5.5",        # Flagship 2026 · 1M ctx · computer use · all tools
    "gpt-5.4",        # Affordable · coding + professional
    "gpt-5.4-mini",   # Strongest mini · coding + subagents
    "gpt-5.4-nano",   # Cheapest gpt-5.4 class
    "gpt-5",          # Reasoning model
    "gpt-4.1",        # Smartest non-reasoning
    "gpt-4o",         # General purpose (legacy)
    "o3",             # Reasoning · complex tasks
]


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


def _build_system(skills: list) -> str:
    base = _AGENTS_MD.read_text(encoding="utf-8", errors="replace") if _AGENTS_MD.exists() else (
        "You are a capable research and task agent. Think step-by-step. "
        "Use tools proactively when they help answer the request. "
        "You have a persistent /mnt/data filesystem — write todo.md for multi-step tasks, "
        "dump large outputs to files, read them back as needed."
    )
    if not skills:
        return base
    details = "\n\n".join(f"### {s['name']}\n{s['content']}" for s in skills)
    return f"{base}\n\n## Active Skills\n{details}"


class OpenAIProvider:
    DEFAULT_MODEL = MODELS[0]

    def __init__(self, api_key: str, model: str = None):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model or self.DEFAULT_MODEL

    async def chat(
        self,
        messages: list,
        tools_config: dict,
        mcp_servers: list,
        skills: list,
        ws: WebSocket,
    ):
        system = _build_system(skills)
        tools = [v for k, v in SERVER_TOOLS.items() if tools_config.get(k)]

        # Shell tool: hosted container with /mnt/data filesystem.
        # If skills are loaded, mount them inside the shell environment — this is the
        # OpenAI native Skills API (not system-prompt injection).
        use_shell = tools_config.get("shell", False)
        if use_shell or skills:
            shell_tool = dict(SHELL_TOOL)
            if skills:
                mounted = []
                for skill in skills:
                    try:
                        sid = await upload_skill_openai(self.client, skill)
                        mounted.append({"type": "skill_reference", "skill_id": sid})
                        await ws.send_json({"type": "info",
                            "message": f"OpenAI Skill '{skill['name']}' → skill_id={sid[:20]}... (mounted in shell)"})
                    except Exception as e:
                        await ws.send_json({"type": "info",
                            "message": f"Skill upload failed ({e}), falling back to system prompt"})
                if mounted:
                    shell_tool["environment"] = {**shell_tool["environment"], "skills": mounted}
            tools.append(shell_tool)

        # MCP servers — OpenAI calls them server-side
        for mcp in mcp_servers:
            entry = {
                "type": "mcp",
                "server_label": mcp["name"],
                "server_url": mcp["url"],
                "require_approval": "never",
            }
            if mcp.get("token"):
                entry["headers"] = {"Authorization": f"Bearer {mcp['token']}"}
            tools.append(entry)

        input_msgs = [{"role": "system", "content": system}] + messages

        params = {"model": self.model, "input": input_msgs, "store": True}
        if tools:
            params["tools"] = tools

        # Reuse previous container for shell tool persistence
        saved_container = get_container_id("default", "openai")
        if use_shell and saved_container:
            params["container"] = saved_container
            await ws.send_json({
                "type": "info",
                "message": f"Reusing OpenAI container {saved_container[:20]}... (files from previous session available)",
            })

        # Chain previous response for conversation continuity
        prev_response_id = get_container_id("default", "openai_response_id")
        if prev_response_id:
            params["previous_response_id"] = prev_response_id
            await ws.send_json({
                "type": "info",
                "message": f"Continuing from previous_response_id={prev_response_id[:20]}...",
            })

        await ws.send_json({"type": "api_request", "payload": params})

        full_text = ""

        try:
            async with self.client.responses.stream(**params) as stream:
                async for event in stream:
                    etype = getattr(event, "type", "")

                    if etype == "response.output_text.delta":
                        delta = getattr(event, "delta", "")
                        if delta:
                            await ws.send_json({"type": "token", "content": delta})
                            full_text += delta

                    elif etype == "response.web_search_call.in_progress":
                        await ws.send_json({"type": "tool_call", "tool": "web_search", "input": {}})
                    elif etype == "response.web_search_call.searching":
                        query = getattr(event, "query", "")
                        await ws.send_json({"type": "tool_call", "tool": "web_search", "input": {"query": query}})
                    elif etype == "response.web_search_call.completed":
                        await ws.send_json({"type": "tool_result", "tool": "web_search", "output": "completed"})

                    elif etype == "response.code_interpreter_call.in_progress":
                        await ws.send_json({"type": "tool_call", "tool": "code_interpreter", "input": {}})
                    elif etype == "response.code_interpreter_call.code.delta":
                        code = getattr(event, "delta", "")
                        await ws.send_json({"type": "tool_call", "tool": "code_interpreter", "input": {"code": code}})
                    elif etype == "response.code_interpreter_call.completed":
                        await ws.send_json({"type": "tool_result", "tool": "code_interpreter", "output": "completed"})

                    elif etype in ("response.mcp_call.in_progress",):
                        name = getattr(event, "name", "mcp")
                        await ws.send_json({"type": "tool_call", "tool": f"mcp:{name}", "input": {}})
                    elif etype == "response.mcp_call.completed":
                        await ws.send_json({"type": "tool_result", "tool": "mcp", "output": "completed"})
                    elif etype == "response.mcp_call.failed":
                        err = getattr(event, "error", "MCP call failed")
                        await ws.send_json({"type": "info", "message": f"MCP error: {err}"})

                    # Shell tool events
                    elif etype == "response.shell_call.in_progress":
                        await ws.send_json({"type": "tool_call", "tool": "shell", "input": {}})
                    elif etype == "response.shell_call.completed":
                        await ws.send_json({"type": "tool_result", "tool": "shell", "output": "completed"})

                final = await stream.get_final_response()

            resp_dict = _safe_dict(final)
            await ws.send_json({"type": "api_response", "payload": resp_dict})

            # Save response_id for conversation continuity
            if hasattr(final, "id") and final.id:
                set_container_id("default", "openai_response_id", final.id)

            # Save container_id if shell was used
            if use_shell and hasattr(final, "container") and final.container:
                cid = getattr(final.container, "id", None) or (
                    final.container if isinstance(final.container, str) else None
                )
                if cid:
                    set_container_id("default", "openai", cid)
                    await ws.send_json({
                        "type": "info",
                        "message": f"OpenAI container {cid[:20]}... saved — /mnt/data persists",
                    })

            # Extract text and handle special output types from final response
            if not full_text and hasattr(final, "output"):
                for item in (final.output or []):
                    itype = getattr(item, "type", "")

                    if itype == "message":
                        for part in getattr(item, "content", []):
                            text = getattr(part, "text", "")
                            if text:
                                full_text += text
                                await ws.send_json({"type": "token", "content": text})

                    elif itype == "image_generation_call":
                        # Image is base64 in item.result — send as a tool_result event
                        b64 = getattr(item, "result", "") or ""
                        await ws.send_json({
                            "type": "tool_result",
                            "tool": "image_generation",
                            "output": f"data:image/png;base64,{b64[:40]}... ({len(b64)} bytes)",
                        })
                        # Put the image in the chat as an <img> tag via a special token
                        if b64:
                            img_html = f'<img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:4px;margin-top:8px;" alt="Generated image"/>'
                            full_text = img_html
                            await ws.send_json({"type": "image", "data": b64})

            await ws.send_json({"type": "done", "message": full_text or "(image generated — see above)"})

        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})

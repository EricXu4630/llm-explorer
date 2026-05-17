"""
Gemini provider — Google GenAI unified SDK.

FILESYSTEM / BASH:
  Gemini has NO persistent server-side container or filesystem.
  Code execution is stateless per call (Python only, 30s, no file persistence).
  Files API: 48-hour TTL only — NOT suitable for cross-session memory.

  This harness provides a BASH TOOL backed by workspace/ (local directory).
  The model can write todo.md, dump tool outputs, read files back.
  The workspace/ persists indefinitely (local filesystem).
  This is the harness-layer equivalent of what Anthropic/OpenAI do server-side.

NATIVE SERVER-SIDE TOOLS (Google executes, no client loop):
  google_search   — grounded web search via Google Search
  code_execution  — server-side Python (stateless, 30s)
  url_context     — server-side URL reader

FUNCTION CALLING LOOP (harness executes):
  bash            — harness-side bash in workspace/ directory
  Any MCP tools (if implemented as function_declarations)

MEMORY / CROSS-SESSION:
  - workspace/ directory (local, permanent): model writes files here via bash tool
  - memories/ directory (local, permanent): memory_20250818-style key-value store
  - NO native Files API persistence (48h only)
  - NO Conversations API equivalent
  - Context caching: 1h TTL only, cost-optimization, not memory
"""

import json
import pathlib
import asyncio
from google import genai

_AGENTS_MD = pathlib.Path(__file__).parent.parent / "AGENTS.md"
from google.genai import types
from fastapi import WebSocket

from workspace_executor import execute_bash, workspace_listing, WORKSPACE

MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]

# Bash tool declaration — harness-side filesystem for Gemini
BASH_FUNCTION = types.FunctionDeclaration(
    name="bash",
    description=(
        "Execute a bash command in the workspace directory. "
        "Use this to: write todo.md at task start, dump large outputs to files, "
        "read files back, create/update notes. "
        "Files persist across the entire session in the local workspace/. "
        "Always check what files exist first with: ls -la"
    ),
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "command": types.Schema(
                type="STRING",
                description="Bash command to run. Working directory is workspace/.",
            )
        },
        required=["command"],
    ),
)


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


def _build_system(skills: list, use_bash: bool) -> str:
    base = _AGENTS_MD.read_text(encoding="utf-8", errors="replace") if _AGENTS_MD.exists() else (
        "You are a capable research and task agent. Think step-by-step. "
        "Use tools proactively when they help answer the request."
    )
    if use_bash:
        ws = workspace_listing()
        base += (
            f"\n\nYou have a persistent workspace at workspace/. "
            f"ALWAYS start tasks by running: ls -la  to see existing files. "
            f"Write todo.md at the start of multi-step tasks. "
            f"Dump large outputs to files rather than keeping them in context. "
            f"Current workspace contents:\n{ws}"
        )
    if not skills:
        return base
    details = "\n\n".join(f"### {s['name']}\n{s['content']}" for s in skills)
    return f"{base}\n\n## Skills\n{details}"


def _to_contents(messages: list) -> list:
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=str(msg.get("content", "")))])
        )
    return contents


def _serialize_response(response) -> dict:
    result = {"candidates": [], "usage_metadata": None, "model_version": None}
    try:
        result["model_version"] = getattr(response, "model_version", None)
        for candidate in (response.candidates or []):
            parts = []
            for part in (candidate.content.parts or []):
                p = {}
                if part.text:
                    p["text"] = part.text[:500] + ("..." if len(part.text) > 500 else "")
                if getattr(part, "executable_code", None):
                    p["executable_code"] = {"code": part.executable_code.code}
                if getattr(part, "code_execution_result", None):
                    p["code_execution_result"] = {"output": part.code_execution_result.output}
                if getattr(part, "function_call", None):
                    p["function_call"] = {"name": part.function_call.name, "args": dict(part.function_call.args)}
                if getattr(part, "function_response", None):
                    p["function_response"] = {"name": part.function_response.name}
                parts.append(p)
            result["candidates"].append({
                "content": {"role": candidate.content.role, "parts": parts},
                "finish_reason": str(getattr(candidate, "finish_reason", "")),
            })
    except Exception as e:
        result["_error"] = str(e)
    if getattr(response, "usage_metadata", None):
        um = response.usage_metadata
        result["usage_metadata"] = {
            "prompt_token_count": getattr(um, "prompt_token_count", None),
            "candidates_token_count": getattr(um, "candidates_token_count", None),
        }
    return result


class GeminiProvider:
    DEFAULT_MODEL = MODELS[0]

    def __init__(self, api_key: str, model: str = None):
        self.sync_client = genai.Client(api_key=api_key)
        self.model = model or self.DEFAULT_MODEL

    async def chat(
        self,
        messages: list,
        tools_config: dict,
        mcp_servers: list,
        skills: list,
        ws: WebSocket,
    ):
        use_bash = tools_config.get("bash", False)
        system = _build_system(skills, use_bash)
        contents = _to_contents(messages)

        # Native server-side tools
        tool_objs = []
        if tools_config.get("google_search"):
            tool_objs.append(types.Tool(google_search=types.GoogleSearch()))
        if tools_config.get("code_execution"):
            tool_objs.append(types.Tool(code_execution=types.ToolCodeExecution()))
        if tools_config.get("url_context"):
            tool_objs.append(types.Tool(url_context=types.UrlContext()))

        # Client-side bash tool (harness executes)
        bash_function_tool = None
        if use_bash:
            bash_function_tool = types.Tool(function_declarations=[BASH_FUNCTION])
            tool_objs.append(bash_function_tool)

        if mcp_servers:
            await ws.send_json({
                "type": "info",
                "message": "Gemini: no native MCP connector. MCP servers ignored. Use Anthropic or OpenAI for native MCP.",
            })

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=tool_objs if tool_objs else None,
        )

        log_params = {
            "model": self.model,
            "contents": [_safe_dict(c) for c in contents],
            "config": {
                "system_instruction": system[:200] + "..." if len(system) > 200 else system,
                "tools": [str(t) for t in tool_objs],
            },
        }
        await ws.send_json({"type": "api_request", "payload": log_params})

        full_text = ""

        try:
            # Run the agentic loop — handles both server-side and client-side (bash) tools
            full_text = await self._run_loop(contents, config, ws)
            await ws.send_json({"type": "done", "message": full_text})

        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e)})

    async def _run_loop(self, contents: list, config, ws: WebSocket) -> str:
        """
        Agentic loop for Gemini.
        - Server-side tools (google_search, code_execution, url_context): Google handles internally.
        - Client-side tools (bash): harness executes, result sent back as function_response.
        """
        full_text = ""
        current_contents = list(contents)

        while True:
            def _sync_call():
                return self.sync_client.models.generate_content(
                    model=self.model,
                    contents=current_contents,
                    config=config,
                )

            response = await asyncio.to_thread(_sync_call)
            resp_dict = _serialize_response(response)
            await ws.send_json({"type": "api_response", "payload": resp_dict})

            if not response.candidates:
                break

            candidate = response.candidates[0]
            parts = candidate.content.parts or []

            # Collect text and tool calls from this response
            function_calls = []
            for part in parts:
                if part.text:
                    full_text += part.text
                    await ws.send_json({"type": "token", "content": part.text})

                if getattr(part, "executable_code", None):
                    await ws.send_json({
                        "type": "tool_call",
                        "tool": "code_execution",
                        "input": {"code": part.executable_code.code},
                    })
                if getattr(part, "code_execution_result", None):
                    await ws.send_json({
                        "type": "tool_result",
                        "tool": "code_execution",
                        "output": part.code_execution_result.output[:200],
                    })
                if getattr(part, "function_call", None):
                    function_calls.append(part.function_call)
                    await ws.send_json({
                        "type": "tool_call",
                        "tool": part.function_call.name,
                        "input": dict(part.function_call.args),
                    })

            # If no function calls, we're done
            if not function_calls:
                break

            # Execute client-side function calls (bash)
            function_responses = []
            for fc in function_calls:
                if fc.name == "bash":
                    cmd = fc.args.get("command", "")
                    result = execute_bash(cmd)
                    await ws.send_json({
                        "type": "tool_result",
                        "tool": "bash",
                        "output": result[:200],
                    })
                    function_responses.append(
                        types.Part.from_function_response(
                            name="bash",
                            response={"output": result},
                        )
                    )
                else:
                    function_responses.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"error": f"Unknown function: {fc.name}"},
                        )
                    )

            # Add model response + function responses to conversation
            current_contents.append(
                types.Content(role="model", parts=parts)
            )
            current_contents.append(
                types.Content(role="user", parts=function_responses)
            )

            # Log the continuation request
            log = {
                "model": self.model,
                "contents": f"[{len(current_contents)} content blocks]",
                "note": "Continuing after function calls",
            }
            await ws.send_json({"type": "api_request", "payload": log})

        return full_text

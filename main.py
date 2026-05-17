import os
import json
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

load_dotenv()

from providers.anthropic_provider import AnthropicProvider
from providers.openai_provider import OpenAIProvider
from providers.gemini_provider import GeminiProvider
from skills import load_skill_from_url

app = FastAPI(title="LLM Explorer")
app.mount("/static", StaticFiles(directory="static"), name="static")

PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}

API_KEY_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


@app.get("/")
async def index():
    return HTMLResponse(Path("static/index.html").read_text(encoding="utf-8"))


@app.get("/about")
async def about():
    return HTMLResponse(Path("static/about.html").read_text(encoding="utf-8"))


@app.get("/api/info")
async def info():
    """Return system info: memory paths, session state, file structure."""
    import pathlib, json
    root = pathlib.Path(__file__).parent
    memories_dir = root / "memories"
    workspace_dir = root / "workspace"
    sessions_dir = root / "sessions"

    memory_files = []
    for f in sorted(memories_dir.rglob("*")) if memories_dir.exists() else []:
        if f.is_file():
            memory_files.append({
                "path": str(f.relative_to(root)),
                "size": f.stat().st_size,
                "content_preview": f.read_text(encoding="utf-8", errors="replace")[:200],
            })

    workspace_files = []
    for f in sorted(workspace_dir.rglob("*")) if workspace_dir.exists() else []:
        if f.is_file():
            workspace_files.append({
                "path": str(f.relative_to(root)),
                "size": f.stat().st_size,
            })

    session_state = {}
    from session_manager import load_session
    try:
        session_state = load_session("default")
        session_state.pop("conversation", None)  # don't send full conversation
    except Exception:
        pass

    return {
        "root": str(root),
        "agents_md": str(root / "AGENTS.md"),
        "memory_dir": str(memories_dir),
        "workspace_dir": str(workspace_dir),
        "sessions_dir": str(sessions_dir),
        "memory_files": memory_files,
        "workspace_files": workspace_files,
        "session_state": session_state,
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as e:
                await ws.send_json({"type": "error", "message": f"Invalid JSON: {e}"})
                continue

            action = msg.get("type")

            if action == "chat":
                provider_name = msg.get("provider", "anthropic")
                provider_cls = PROVIDERS.get(provider_name)
                if not provider_cls:
                    await ws.send_json({"type": "error", "message": f"Unknown provider: {provider_name}"})
                    continue

                api_key = os.getenv(API_KEY_VARS[provider_name], "").strip()
                if not api_key:
                    await ws.send_json({
                        "type": "error",
                        "message": f"No API key for {provider_name}. Set {API_KEY_VARS[provider_name]} in .env",
                    })
                    continue

                provider = provider_cls(api_key=api_key, model=msg.get("model") or None)
                await provider.chat(
                    messages=msg.get("conversation", []),
                    tools_config=msg.get("tools", {}),
                    mcp_servers=msg.get("mcp_servers", []),
                    skills=msg.get("skills", []),
                    ws=ws,
                )

            elif action == "load_skill":
                try:
                    skill = await load_skill_from_url(msg["url"])
                    await ws.send_json({"type": "skill_loaded", "skill": skill})
                except Exception as e:
                    await ws.send_json({"type": "error", "message": f"Failed to load skill: {e}"})

    except WebSocketDisconnect:
        pass

"""
Session manager — persists conversation history and container IDs across restarts.

Saved to sessions/{session_id}.json:
  - conversation: [{role, content}] array
  - container_ids: {anthropic: "...", openai: "..."}  (Gemini has no container)
  - created_at, updated_at

This enables "come back and not start from scratch":
  - Conversation history reloaded on startup
  - Anthropic/OpenAI containers reused (30-day window) so files in /mnt/data persist
  - Gemini workspace/ is always local and already persists
"""

import json
import time
import pathlib

SESSIONS_DIR = pathlib.Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

DEFAULT_SESSION = "default"


def _session_path(session_id: str) -> pathlib.Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return SESSIONS_DIR / f"{safe}.json"


def load_session(session_id: str = DEFAULT_SESSION) -> dict:
    path = _session_path(session_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {
        "session_id": session_id,
        "conversation": [],
        "container_ids": {},
        "created_at": time.time(),
        "updated_at": time.time(),
    }


def save_session(data: dict, session_id: str = DEFAULT_SESSION):
    data["updated_at"] = time.time()
    path = _session_path(session_id)
    path.write_text(json.dumps(data, indent=2))


def get_container_id(session_id: str, provider: str) -> str | None:
    session = load_session(session_id)
    return session.get("container_ids", {}).get(provider)


def set_container_id(session_id: str, provider: str, container_id: str | None):
    session = load_session(session_id)
    cids = session.setdefault("container_ids", {})
    if container_id is None:
        cids.pop(provider, None)  # Remove key entirely rather than setting None
    else:
        cids[provider] = container_id
    save_session(session, session_id)


def list_sessions() -> list[dict]:
    sessions = []
    for path in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            sessions.append({
                "session_id": data.get("session_id", path.stem),
                "message_count": len(data.get("conversation", [])),
                "updated_at": data.get("updated_at", 0),
                "container_ids": data.get("container_ids", {}),
            })
        except Exception:
            pass
    return sorted(sessions, key=lambda s: s["updated_at"], reverse=True)

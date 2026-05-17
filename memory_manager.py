"""
Memory tool implementation — filesystem semantics for memory_20250818.

The real memory_20250818 tool uses a virtual filesystem rooted at /memories/.
Commands: view, create, str_replace, insert, delete, rename.

These are client-side: our harness executes them against a local `memories/`
directory. Return strings match the exact format Anthropic documents.

SEPARATE from Files API:
  - Files API: you upload files → model reads them as document blocks (read-only)
  - Memory tool: model reads/writes /memories/ directory (read-write, harness executes)

Files API for memory (AGENTS.md pattern):
  Upload a project context file (AGENTS.md, system prompt, etc.) once.
  Reference by file_id in every request as a document block.
  Model reads it passively. Harness re-uploads when content changes.
"""

import io
import os
import math
import shutil
import pathlib
import anthropic

# Local directory that backs the /memories/ virtual filesystem
MEMORIES_DIR = pathlib.Path(__file__).parent / "memories"
FILES_BETA = "files-api-2025-04-14"
FILES_ID_CACHE = pathlib.Path(__file__).parent / "files_api_ids.json"

MEMORIES_DIR.mkdir(exist_ok=True)


# ─── Path safety ──────────────────────────────────────────────────────────────

def _resolve(virtual_path: str) -> pathlib.Path:
    """Map /memories/... to the local memories directory. Rejects traversal."""
    # Strip leading /memories prefix
    stripped = virtual_path.strip()
    if stripped.startswith("/memories"):
        stripped = stripped[len("/memories"):]
    stripped = stripped.lstrip("/")

    resolved = (MEMORIES_DIR / stripped).resolve()
    # Security: must stay within MEMORIES_DIR
    try:
        resolved.relative_to(MEMORIES_DIR.resolve())
    except ValueError:
        raise ValueError(f"Path traversal attempt blocked: {virtual_path}")
    return resolved


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    for unit in ("K", "M", "G"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f}{unit}"
    return f"{n:.1f}T"


def _format_dir_listing(path: pathlib.Path) -> str:
    virtual = f"/memories/{path.relative_to(MEMORIES_DIR)}" if path != MEMORIES_DIR else "/memories"
    lines = [f"Here're the files and directories up to 2 levels deep in {virtual}, "
             f"excluding hidden items and node_modules:"]

    def _walk(p: pathlib.Path, depth: int):
        if depth > 2:
            return
        for child in sorted(p.iterdir()):
            if child.name.startswith(".") or child.name == "node_modules":
                continue
            size = _human_size(child.stat().st_size) if child.is_file() else "4.0K"
            vpath = f"/memories/{child.relative_to(MEMORIES_DIR)}"
            lines.append(f"{size}\t{vpath}")
            if child.is_dir():
                _walk(child, depth + 1)

    _walk(path, 1)
    return "\n".join(lines)


def _format_file_contents(path: pathlib.Path, view_range: list | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    all_lines = text.splitlines(keepends=True)

    if len(all_lines) > 999_999:
        return f"File {path} exceeds maximum line limit of 999,999 lines."

    if view_range:
        start, end = max(1, view_range[0]), min(len(all_lines), view_range[1])
        lines = all_lines[start - 1:end]
        offset = start
    else:
        lines = all_lines
        offset = 1

    virtual = f"/memories/{path.relative_to(MEMORIES_DIR)}"
    header = f"Here's the content of {virtual} with line numbers:"
    numbered = "".join(f"{(i + offset):6}\t{line}" for i, line in enumerate(lines))
    return f"{header}\n{numbered}"


# ─── Command handlers ─────────────────────────────────────────────────────────

def execute_memory_command(tool_input: dict) -> str:
    """Execute a memory tool call and return the result string."""
    command = tool_input.get("command", "")

    if command == "view":
        return _cmd_view(tool_input)
    elif command == "create":
        return _cmd_create(tool_input)
    elif command == "str_replace":
        return _cmd_str_replace(tool_input)
    elif command == "insert":
        return _cmd_insert(tool_input)
    elif command == "delete":
        return _cmd_delete(tool_input)
    elif command == "rename":
        return _cmd_rename(tool_input)
    else:
        return f"Unknown memory command: {command}. Valid commands: view, create, str_replace, insert, delete, rename."


def _cmd_view(inp: dict) -> str:
    try:
        path = _resolve(inp.get("path", "/memories"))
    except ValueError as e:
        return str(e)

    if not path.exists():
        return f"The path {inp.get('path')} does not exist. Please provide a valid path."

    if path.is_dir():
        return _format_dir_listing(path)
    else:
        return _format_file_contents(path, inp.get("view_range"))


def _cmd_create(inp: dict) -> str:
    vpath = inp.get("path", "")
    text = inp.get("file_text", "")
    try:
        path = _resolve(vpath)
    except ValueError as e:
        return str(e)

    if path.exists():
        return f"Error: File {vpath} already exists"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"File created successfully at: {vpath}"


def _cmd_str_replace(inp: dict) -> str:
    vpath = inp.get("path", "")
    old_str = inp.get("old_str", "")
    new_str = inp.get("new_str", "")
    try:
        path = _resolve(vpath)
    except ValueError as e:
        return str(e)

    if not path.exists() or path.is_dir():
        return f"Error: The path {vpath} does not exist. Please provide a valid path."

    content = path.read_text(encoding="utf-8")
    count = content.count(old_str)

    if count == 0:
        return f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {vpath}."
    if count > 1:
        lines = [str(i + 1) for i, line in enumerate(content.splitlines()) if old_str in line]
        return f"No replacement was performed. Multiple occurrences of old_str `{old_str}` in lines: {', '.join(lines)}. Please ensure it is unique"

    new_content = content.replace(old_str, new_str, 1)
    path.write_text(new_content, encoding="utf-8")

    # Show a snippet of the edited area
    snippet_lines = new_content.splitlines()
    replace_line = next((i for i, l in enumerate(snippet_lines) if new_str.splitlines()[0] in l), 0)
    start = max(0, replace_line - 2)
    end = min(len(snippet_lines), replace_line + 5)
    snippet = "\n".join(f"{(i + start + 1):6}\t{l}" for i, l in enumerate(snippet_lines[start:end]))
    return f"The memory file has been edited.\n{snippet}"


def _cmd_insert(inp: dict) -> str:
    vpath = inp.get("path", "")
    insert_line = inp.get("insert_line", 0)
    insert_text = inp.get("insert_text", "")
    try:
        path = _resolve(vpath)
    except ValueError as e:
        return str(e)

    if not path.exists() or path.is_dir():
        return f"Error: The path {vpath} does not exist"

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    n = len(lines)

    if not (0 <= insert_line <= n):
        return f"Error: Invalid `insert_line` parameter: {insert_line}. It should be within the range of lines of the file: [0, {n}]"

    if insert_text and not insert_text.endswith("\n"):
        insert_text += "\n"
    lines.insert(insert_line, insert_text)
    path.write_text("".join(lines), encoding="utf-8")
    return f"The file {vpath} has been edited."


def _cmd_delete(inp: dict) -> str:
    vpath = inp.get("path", "")
    try:
        path = _resolve(vpath)
    except ValueError as e:
        return str(e)

    if not path.exists():
        return f"Error: The path {vpath} does not exist"

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return f"Successfully deleted {vpath}"


def _cmd_rename(inp: dict) -> str:
    old_vpath = inp.get("old_path", "")
    new_vpath = inp.get("new_path", "")
    try:
        old_path = _resolve(old_vpath)
        new_path = _resolve(new_vpath)
    except ValueError as e:
        return str(e)

    if not old_path.exists():
        return f"Error: The path {old_vpath} does not exist"
    if new_path.exists():
        return f"Error: The destination {new_vpath} already exists"

    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    return f"Successfully renamed {old_vpath} to {new_vpath}"


# ─── Files API helpers (AGENTS.md pattern) ────────────────────────────────────

import json

def _load_file_ids() -> dict:
    if FILES_ID_CACHE.exists():
        try:
            return json.loads(FILES_ID_CACHE.read_text())
        except Exception:
            pass
    return {}


def _save_file_ids(ids: dict):
    FILES_ID_CACHE.write_text(json.dumps(ids, indent=2))


async def upload_document(client: anthropic.AsyncAnthropic, local_path: str, label: str) -> str:
    """
    Upload a local file to Files API and return file_id.
    Caches by label so the same file isn't re-uploaded unless content changes.
    """
    ids = _load_file_ids()
    path = pathlib.Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {local_path}")

    content = path.read_bytes()
    import hashlib
    content_hash = hashlib.sha1(content).hexdigest()[:12]
    cache_key = f"{label}:{content_hash}"

    if cache_key in ids:
        # Verify it still exists on Anthropic's side
        try:
            await client.beta.files.retrieve_metadata(ids[cache_key], betas=[FILES_BETA])
            return ids[cache_key]
        except Exception:
            pass

    mime = "text/plain" if path.suffix in (".txt", ".md", ".yaml", ".json") else "application/pdf"
    result = await client.beta.files.upload(
        file=(path.name, io.BytesIO(content), mime),
        betas=[FILES_BETA],
    )
    ids[cache_key] = result.id
    _save_file_ids(ids)
    return result.id


def build_file_document_block(file_id: str, title: str = None, context: str = None) -> dict:
    """Build a document content block referencing a Files API file_id."""
    block: dict = {
        "type": "document",
        "source": {"type": "file", "file_id": file_id},
        "cache_control": {"type": "ephemeral"},  # prompt cache this document
    }
    if title:
        block["title"] = title
    if context:
        block["context"] = context
    return block

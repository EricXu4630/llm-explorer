"""
Harness-side workspace executor — used for Gemini (no native container).

Cross-platform: uses Python file operations for common commands (ls, cat, echo, etc.)
on Windows where bash isn't available. Falls back to real subprocess when bash/WSL exists.

All operations sandboxed to workspace/ directory.
The model writes todo.md, dumps tool outputs, reads files — exactly the Manus pattern.
"""

import re
import os
import sys
import shutil
import subprocess
import pathlib
from fnmatch import fnmatch

WORKSPACE = pathlib.Path(__file__).parent / "workspace"
WORKSPACE.mkdir(exist_ok=True)

# Check if bash is available (Unix or WSL on Windows)
_BASH = None
for candidate in ["/bin/bash", "/usr/bin/bash", "bash", "wsl", "wsl.exe"]:
    try:
        subprocess.run([candidate, "--version"], capture_output=True, timeout=2)
        _BASH = candidate
        break
    except Exception:
        pass


def _resolve_path(path_str: str) -> pathlib.Path:
    """Map a path to the workspace directory safely."""
    p = path_str.strip().strip("'\"")
    # Strip /workspace or workspace prefix
    for prefix in ["/workspace/", "/workspace", "workspace/", "workspace"]:
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    p = p.lstrip("/").lstrip("\\")
    resolved = (WORKSPACE / p).resolve()
    # Security: must stay within WORKSPACE
    try:
        resolved.relative_to(WORKSPACE.resolve())
    except ValueError:
        raise ValueError(f"Path outside workspace: {path_str}")
    return resolved


def _cmd_ls(args: str) -> str:
    """List directory contents."""
    show_hidden = "-a" in args or "-la" in args or "-al" in args
    long_format = "-l" in args

    # Extract path from args (last non-flag token)
    tokens = [t for t in args.split() if not t.startswith("-")]
    try:
        target = _resolve_path(tokens[-1]) if tokens else WORKSPACE
    except Exception:
        target = WORKSPACE

    if not target.exists():
        return f"ls: {tokens[-1] if tokens else '.'}: No such file or directory"

    if target.is_file():
        items = [target]
    else:
        items = sorted(target.iterdir())

    if not show_hidden:
        items = [i for i in items if not i.name.startswith(".")]

    if not items:
        return "(empty)"

    if long_format:
        lines = [f"total {len(items)}"]
        for item in items:
            stat = item.stat()
            mode = "d" if item.is_dir() else "-"
            size = stat.st_size
            name = item.name + ("/" if item.is_dir() else "")
            lines.append(f"{mode}rwxr-xr-x  {size:8d}  {name}")
        return "\n".join(lines)
    else:
        return "  ".join(i.name + ("/" if i.is_dir() else "") for i in items)


def _cmd_cat(args: str) -> str:
    """Read file contents."""
    files = [t for t in args.split() if not t.startswith("-")]
    if not files:
        return "cat: missing file operand"
    try:
        path = _resolve_path(files[0])
        if not path.exists():
            return f"cat: {files[0]}: No such file or directory"
        if path.is_dir():
            return f"cat: {files[0]}: Is a directory"
        content = path.read_text(encoding="utf-8", errors="replace")
        return content if content else "(empty file)"
    except ValueError as e:
        return str(e)


def _cmd_echo_redirect(command: str) -> str:
    """Handle: echo "..." > file  or  echo "..." >> file"""
    # Append mode
    if ">>" in command:
        parts = command.split(">>", 1)
        content = parts[0].replace("echo", "", 1).strip().strip('"\'')
        file_part = parts[1].strip()
    elif ">" in command:
        parts = command.split(">", 1)
        content = parts[0].replace("echo", "", 1).strip().strip('"\'')
        file_part = parts[1].strip()
    else:
        return command.replace("echo", "", 1).strip().strip('"\'')

    try:
        path = _resolve_path(file_part)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if ">>" in command else "w"
        content = content.replace("\\n", "\n")
        with open(path, mode, encoding="utf-8") as f:
            f.write(content + "\n")
        return ""
    except ValueError as e:
        return str(e)


def _cmd_mkdir(args: str) -> str:
    """Create directory."""
    tokens = [t for t in args.split() if not t.startswith("-")]
    if not tokens:
        return "mkdir: missing operand"
    try:
        path = _resolve_path(tokens[-1])
        path.mkdir(parents=True, exist_ok=True)
        return ""
    except ValueError as e:
        return str(e)


def _cmd_touch(args: str) -> str:
    """Create empty file or update timestamp."""
    tokens = [t for t in args.split() if not t.startswith("-")]
    if not tokens:
        return "touch: missing file operand"
    try:
        path = _resolve_path(tokens[-1])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return ""
    except ValueError as e:
        return str(e)


def _cmd_rm(args: str) -> str:
    """Remove file or directory."""
    recursive = "-r" in args or "-rf" in args
    tokens = [t for t in args.split() if not t.startswith("-")]
    if not tokens:
        return "rm: missing operand"
    try:
        path = _resolve_path(tokens[-1])
        if not path.exists():
            return f"rm: {tokens[-1]}: No such file or directory"
        if path.is_dir() and recursive:
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()
        else:
            return f"rm: {tokens[-1]}: Is a directory (use -r)"
        return ""
    except ValueError as e:
        return str(e)


def _cmd_grep(args: str) -> str:
    """Search for pattern in file."""
    tokens = args.split()
    flags = [t for t in tokens if t.startswith("-")]
    non_flags = [t for t in tokens if not t.startswith("-")]
    if len(non_flags) < 2:
        return "grep: missing pattern or file"
    pattern, filename = non_flags[0], non_flags[1]
    try:
        path = _resolve_path(filename)
        if not path.exists():
            return f"grep: {filename}: No such file or directory"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        case_insensitive = "-i" in flags
        matches = []
        for i, line in enumerate(lines, 1):
            if case_insensitive:
                found = pattern.lower() in line.lower()
            else:
                found = pattern in line
            if found:
                matches.append(f"{i}:{line}" if "-n" in flags else line)
        return "\n".join(matches) if matches else "(no matches)"
    except ValueError as e:
        return str(e)


def _cmd_head(args: str) -> str:
    tokens = args.split()
    n = 10
    files = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "-n" and i + 1 < len(tokens):
            n = int(tokens[i + 1]); i += 2
        elif tokens[i].startswith("-") and tokens[i][1:].isdigit():
            n = int(tokens[i][1:]); i += 1
        else:
            files.append(tokens[i]); i += 1
    if not files:
        return "head: missing file operand"
    try:
        path = _resolve_path(files[0])
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[:n])
    except ValueError as e:
        return str(e)


def _cmd_tail(args: str) -> str:
    tokens = args.split()
    n = 10
    files = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "-n" and i + 1 < len(tokens):
            n = int(tokens[i + 1]); i += 2
        elif tokens[i].startswith("-") and tokens[i][1:].isdigit():
            n = int(tokens[i][1:]); i += 1
        else:
            files.append(tokens[i]); i += 1
    if not files:
        return "tail: missing file operand"
    try:
        path = _resolve_path(files[0])
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except ValueError as e:
        return str(e)


def _cmd_wc(args: str) -> str:
    tokens = args.split()
    files = [t for t in tokens if not t.startswith("-")]
    if not files:
        return "wc: missing file operand"
    try:
        path = _resolve_path(files[0])
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = len(text.splitlines())
        words = len(text.split())
        chars = len(text)
        if "-l" in tokens:
            return str(lines)
        return f"{lines} {words} {chars} {files[0]}"
    except ValueError as e:
        return str(e)


def _cmd_python(args: str) -> str:
    """Run a Python script in the workspace."""
    tokens = args.split()
    if not tokens:
        return "python: missing script"
    try:
        path = _resolve_path(tokens[0])
        if not path.exists():
            return f"python: {tokens[0]}: No such file or directory"
        result = subprocess.run(
            [sys.executable, str(path)] + tokens[1:],
            capture_output=True, text=True, cwd=str(WORKSPACE), timeout=30
        )
        out = result.stdout
        if result.stderr:
            out += f"\n[stderr] {result.stderr}"
        return out or "(no output)"
    except Exception as e:
        return str(e)


def _cmd_write_heredoc(command: str) -> str:
    """Handle: cat > file.txt << 'EOF' ... EOF  (simplified)"""
    # This is complex; just return an error message for now
    return "[write-heredoc: use echo or python instead for multi-line writes]"


def execute_bash(command: str, timeout: int = 30) -> str:
    """
    Execute a bash-like command in the workspace directory.
    On Unix/WSL: uses real bash. On Windows: Python-based emulation.
    """
    command = command.strip()
    if not command:
        return ""

    # Try real bash first (Unix or WSL)
    if _BASH:
        try:
            result = subprocess.run(
                [_BASH, "-c", command],
                capture_output=True, text=True,
                cwd=str(WORKSPACE), timeout=timeout,
                env={**os.environ, "HOME": str(WORKSPACE), "WORKSPACE": str(WORKSPACE)},
            )
            out = result.stdout
            if result.stderr:
                out += f"\n[stderr] {result.stderr}"
            return out.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT] Command exceeded {timeout}s"
        except Exception as e:
            pass  # Fall through to Python emulator

    # Python-based emulator (Windows / no bash)
    cmd = command.strip()

    # Multi-command with &&
    if " && " in cmd:
        parts = cmd.split(" && ")
        results = []
        for part in parts:
            r = execute_bash(part.strip())
            if r:
                results.append(r)
        return "\n".join(results)

    # Pipe: command | command  (limited support)
    if " | " in cmd and not cmd.startswith("echo"):
        parts = cmd.split(" | ", 1)
        left_result = execute_bash(parts[0].strip())
        # Only support: | head, | tail, | wc, | grep
        right = parts[1].strip()
        if right.startswith("head"):
            lines = left_result.splitlines()
            n = 10
            if "-n" in right:
                try:
                    n = int(right.split("-n")[1].strip().split()[0])
                except Exception:
                    pass
            return "\n".join(lines[:n])
        elif right.startswith("tail"):
            lines = left_result.splitlines()
            return "\n".join(lines[-10:])
        elif right.startswith("wc -l"):
            return str(len(left_result.splitlines()))
        elif right.startswith("grep"):
            pattern = right.replace("grep", "").strip().strip("'\"")
            matches = [l for l in left_result.splitlines() if pattern in l]
            return "\n".join(matches)
        return left_result  # just return left side if pipe not handled

    # Parse command
    first_token = cmd.split()[0] if cmd.split() else ""
    rest = cmd[len(first_token):].strip()

    if first_token in ("ls", "dir"):
        return _cmd_ls(rest)
    elif first_token == "cat":
        return _cmd_cat(rest)
    elif first_token == "echo":
        return _cmd_echo_redirect(cmd)
    elif first_token == "mkdir":
        return _cmd_mkdir(rest)
    elif first_token == "touch":
        return _cmd_touch(rest)
    elif first_token == "rm":
        return _cmd_rm(rest)
    elif first_token == "grep":
        return _cmd_grep(rest)
    elif first_token == "head":
        return _cmd_head(rest)
    elif first_token == "tail":
        return _cmd_tail(rest)
    elif first_token == "wc":
        return _cmd_wc(rest)
    elif first_token in ("python", "python3"):
        return _cmd_python(rest)
    elif first_token == "pwd":
        return str(WORKSPACE)
    elif first_token == "date":
        import datetime
        return datetime.datetime.now().isoformat()
    elif first_token in ("clear", "cls"):
        return ""
    elif first_token == "find":
        # find . -name "*.md" → list matching files
        parts = cmd.split()
        pattern = ""
        if "-name" in parts:
            idx = parts.index("-name")
            if idx + 1 < len(parts):
                pattern = parts[idx + 1].strip("'\"")
        results = []
        for p in sorted(WORKSPACE.rglob(pattern or "*")):
            rel = p.relative_to(WORKSPACE)
            results.append(str(rel))
        return "\n".join(results) or "(no files found)"
    else:
        return f"[emulator] Command not supported: {first_token}. Available: ls, cat, echo, mkdir, touch, rm, grep, head, tail, wc, python, find, pwd, date"


def workspace_listing() -> str:
    """Return a tree-like listing of the workspace."""
    lines = ["workspace/"]
    for p in sorted(WORKSPACE.rglob("*")):
        if p.is_file():
            rel = p.relative_to(WORKSPACE)
            size = p.stat().st_size
            lines.append(f"  {rel}  ({size} bytes)")
    if len(lines) == 1:
        lines.append("  (empty)")
    return "\n".join(lines)

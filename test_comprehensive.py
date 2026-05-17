"""
Comprehensive LLM Explorer test suite.
Tests all providers, all tools, all scenarios.
Run: python test_comprehensive.py [suite...]
Suites: anthropic openai gemini filesystem memory mcp agents_md
"""

import asyncio, io, json, pathlib, sys, time, websockets

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WS = "ws://127.0.0.1:8080/ws"
RESULTS = []


async def chat(provider, model, msg, tools=None, mcp=None, skills=None, timeout=60):
    events, text, error = [], "", None
    try:
        async with websockets.connect(WS) as ws:
            await ws.send(json.dumps({
                "type": "chat", "provider": provider, "model": model,
                "conversation": [{"role": "user", "content": msg}],
                "tools": tools or {}, "mcp_servers": mcp or [], "skills": skills or [],
            }))
            for _ in range(200):
                try:
                    d = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                    events.append(d)
                    if d["type"] == "token": text += d.get("content", "")
                    elif d["type"] == "error": error = d.get("message", "error"); break
                    elif d["type"] == "done": text = d.get("message", text); break
                except asyncio.TimeoutError:
                    error = f"Timeout after {timeout}s"; break
    except Exception as e:
        error = str(e)
    return events, text, error


def chk(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    status = "✅" if ok else "❌"
    safe = f"{name} {detail}".encode("ascii","replace").decode()[:120]
    print(f"  {status}  {safe}")
    return ok


def has_event(evs, type_, **kw):
    for e in evs:
        if e.get("type") != type_: continue
        if all(str(kw[k]).lower() in str(e.get(k,"")).lower() for k in kw):
            return True
    return False


# ═══════════════════════════════════════════════════════
# ANTHROPIC
# ═══════════════════════════════════════════════════════
async def test_anthropic():
    P, M = "anthropic", "claude-sonnet-4-6"
    print(f"\n── Anthropic ({M}) ──────────────────────────────")

    # 1. Basic chat
    evs, txt, err = await chat(P, M, "Reply with exactly the word: hello", {})
    chk("anthropic/basic/no-error", not err, err or "")
    chk("anthropic/basic/got-response", len(txt) > 0, f"'{txt[:30]}'")
    chk("anthropic/basic/api-request-logged", has_event(evs, "api_request"))
    chk("anthropic/basic/api-response-logged", has_event(evs, "api_response"))

    # 2. Web search
    evs, txt, err = await chat(P, M, "What day of the week is today? Use web search.", {"web_search": True})
    chk("anthropic/web-search/no-error", not err, err or "")
    chk("anthropic/web-search/got-text", len(txt) > 10)
    chk("anthropic/web-search/tool-event", has_event(evs, "tool_call"))

    # 3. Code execution (bash in container)
    evs, txt, err = await chat(P, M,
        "Use code execution to write a file called todo.md with content '# TODO\n- Task 1\n- Task 2', then read it back.",
        {"code_execution": True})
    chk("anthropic/code-exec/no-error", not err, err or "")
    chk("anthropic/code-exec/got-text", len(txt) > 10)
    chk("anthropic/code-exec/tool-called", has_event(evs, "tool_call"))

    # 4. Web fetch
    evs, txt, err = await chat(P, M, "Fetch https://example.com and tell me the page title.", {"web_fetch": True})
    chk("anthropic/web-fetch/no-error", not err, err or "")
    chk("anthropic/web-fetch/got-text", len(txt) > 5)

    # 5. web_search + code_execution (conflict auto-fix)
    evs, txt, err = await chat(P, M, "Search for today's date and compute 2**10.",
        {"web_search": True, "code_execution": True})
    chk("anthropic/search+code/no-error", not err, err or "")
    chk("anthropic/search+code/got-text", len(txt) > 5)
    chk("anthropic/search+code/conflict-fix-shown",
        has_event(evs, "info") and any("allowed_callers" in e.get("message","") for e in evs if e["type"]=="info"))

    # 6. All tools
    evs, txt, err = await chat(P, M, "Just say hello.",
        {"web_search": True, "code_execution": True, "web_fetch": True})
    chk("anthropic/all-tools/no-error", not err, err or "")
    chk("anthropic/all-tools/got-text", len(txt) > 2)

    # 7. Memory tool (AGENTS.md pattern)
    evs, txt, err = await chat(P, M,
        "Save to memory: key=test_provider value=anthropic_works. Then read it back and confirm.",
        {"memory": True}, timeout=90)
    chk("anthropic/memory/no-error", not err, err or "")
    chk("anthropic/memory/got-text", len(txt) > 10)
    # Check file was written
    mem = pathlib.Path("memories")
    has_file = any(f.is_file() for f in mem.rglob("*")) if mem.exists() else False
    chk("anthropic/memory/file-on-disk", has_file, f"files: {list(mem.rglob('*'))[:3]}")

    # 8. Haiku (no tools — haiku doesn't support server tools)
    evs, txt, err = await chat(P, "claude-haiku-4-5", "Say exactly: haiku ok", {})
    chk("anthropic/haiku/basic-chat-works", not err and len(txt) > 0, txt[:30])

    # 9. AGENTS.md loaded as system
    evs, txt, err = await chat(P, M, "What is your primary mission? (answer in one sentence)", {})
    chk("anthropic/agents-md/system-loaded", not err)
    # AGENTS.md mentions "research assistant" or "LLM Explorer"
    chk("anthropic/agents-md/content-visible", any(
        w in txt.lower() for w in ["research", "explorer", "agent", "tool"]))

    # 10. Multi-turn
    _, txt1, err1 = await chat(P, M, "My name is TestUser. Say 'noted'.", {})
    chk("anthropic/multi-turn/first-ok", not err1 and len(txt1) > 0, txt1[:30])


# ═══════════════════════════════════════════════════════
# OPENAI
# ═══════════════════════════════════════════════════════
async def test_openai():
    P, M = "openai", "gpt-4o-mini"
    print(f"\n── OpenAI ({M}) ─────────────────────────────────")

    # 1. Basic chat
    evs, txt, err = await chat(P, M, "Reply with exactly the word: hello", {})
    chk("openai/basic/no-error", not err, err or "")
    chk("openai/basic/got-response", len(txt) > 0, f"'{txt[:30]}'")
    chk("openai/basic/api-request-logged", has_event(evs, "api_request"))
    chk("openai/basic/api-response-logged", has_event(evs, "api_response"))

    # 2. Web search
    evs, txt, err = await chat(P, M, "What day of the week is today? Use web search.", {"web_search": True})
    chk("openai/web-search/no-error", not err, err or "")
    chk("openai/web-search/got-text", len(txt) > 10)
    chk("openai/web-search/search-event", has_event(evs, "tool_call"))

    # 3. Code interpreter
    evs, txt, err = await chat(P, M, "Use code interpreter to compute the sum of 1 to 100.", {"code_interpreter": True})
    chk("openai/code-interpreter/no-error", not err, err or "")
    chk("openai/code-interpreter/got-text", len(txt) > 5)

    # 4. Web search + code interpreter
    evs, txt, err = await chat(P, "gpt-4o-mini", "Search for today's date, then write Python to print it.",
        {"web_search": True, "code_interpreter": True}, timeout=60)
    is_ratelimit = err and ("rate limit" in str(err).lower() or "429" in str(err))
    chk("openai/search+code/no-error", not err or is_ratelimit,
        "(rate-limited — transient)" if is_ratelimit else (err or ""))
    chk("openai/search+code/got-text", len(txt) > 5 or is_ratelimit)

    # 5. AGENTS.md loaded
    evs, txt, err = await chat(P, M, "What is your primary mission? (one sentence)", {})
    chk("openai/agents-md/loaded", not err)
    chk("openai/agents-md/content", any(w in txt.lower() for w in ["research","explorer","agent","tool"]))

    # 6. Previous response ID chaining
    from session_manager import load_session
    session = load_session("default")
    has_prev_id = bool(session.get("container_ids", {}).get("openai_response_id"))
    chk("openai/conversation-chain/response-id-saved", has_prev_id)

    # 7. MCP native connector
    evs, txt, err = await chat(P, M,
        "Use the DeepWiki MCP to ask: what is Python?",
        {}, mcp=[{"url":"https://mcp.deepwiki.com/mcp","name":"deepwiki","token":""}], timeout=45)
    chk("openai/mcp-native/no-error", not err, err or "")
    chk("openai/mcp-native/got-text", len(txt) > 10)


# ═══════════════════════════════════════════════════════
# GEMINI
# ═══════════════════════════════════════════════════════
async def test_gemini():
    P, M = "gemini", "gemini-2.5-flash"
    print(f"\n── Gemini ({M}) ─────────────────────────────────")

    def skip_rate_limit(evs, err):
        return err and "429" in str(err)

    # 1. Basic chat
    evs, txt, err = await chat(P, M, "Reply with exactly the word: hello", {})
    if skip_rate_limit(evs, err):
        chk("gemini/basic/rate-limited-skip", True, "(free tier 20/day limit)"); return
    chk("gemini/basic/no-error", not err, err or "")
    chk("gemini/basic/got-response", len(txt) > 0, f"'{txt[:30]}'")
    chk("gemini/basic/api-request-logged", has_event(evs, "api_request"))

    # 2. Google Search
    evs, txt, err = await chat(P, M, "What day of the week is today? Use Google Search.", {"google_search": True})
    if skip_rate_limit(evs, err): chk("gemini/google-search/rate-limited", True, "(skip)"); return
    chk("gemini/google-search/no-error", not err, err or "")
    chk("gemini/google-search/got-text", len(txt) > 5)

    # 3. Code execution (Python, stateless)
    evs, txt, err = await chat(P, M, "Use code execution to compute sum(range(100)).", {"code_execution": True})
    if skip_rate_limit(evs, err): chk("gemini/code-exec/rate-limited", True, "(skip)"); return
    chk("gemini/code-exec/no-error", not err, err or "")
    chk("gemini/code-exec/got-text", len(txt) > 2)
    chk("gemini/code-exec/tool-event", has_event(evs, "tool_call", tool="code_execution"))

    # 4. Bash tool (harness-side workspace/)
    ws_file = pathlib.Path("workspace") / "gemini_bash_test.md"
    if ws_file.exists(): ws_file.unlink()

    evs, txt, err = await chat(P, M,
        "Use the bash tool to: 1) run 'ls -la' to see the workspace, "
        "2) write a file called gemini_bash_test.md with content '# Gemini Bash Test\nSuccess!', "
        "3) read it back with 'cat gemini_bash_test.md'.",
        {"bash": True}, timeout=90)
    if skip_rate_limit(evs, err): chk("gemini/bash/rate-limited", True, "(skip)"); return
    chk("gemini/bash/no-error", not err, err or "")
    chk("gemini/bash/got-text", len(txt) > 5)
    chk("gemini/bash/tool-called", has_event(evs, "tool_call", tool="bash"))
    chk("gemini/bash/file-on-disk", ws_file.exists() and "Success" in ws_file.read_text())

    # 5. MCP limitation warning
    evs, _, err2 = await chat(P, M, "hello", {},
        mcp=[{"url":"http://localhost:3001/mcp","name":"test","token":""}])
    if skip_rate_limit(evs, err2): chk("gemini/mcp-warning/rate-limited", True, "(skip)"); return
    chk("gemini/mcp-warning/shown", any(
        "Gemini" in e.get("message","") for e in evs if e["type"]=="info"))

    # 6. AGENTS.md loaded
    evs, txt, err = await chat(P, M, "What is your primary mission? (one sentence)", {})
    if skip_rate_limit(evs, err): chk("gemini/agents-md/rate-limited", True, "(skip)"); return
    chk("gemini/agents-md/loaded", not err)
    chk("gemini/agents-md/content", any(w in txt.lower() for w in ["research","explorer","agent","tool"]))


# ═══════════════════════════════════════════════════════
# FILESYSTEM (all providers)
# ═══════════════════════════════════════════════════════
async def test_filesystem():
    print("\n── Filesystem (all providers) ───────────────────")

    # Anthropic: server-side container
    evs, txt, err = await chat("anthropic","claude-sonnet-4-6",
        "Use code execution to write a todo.md file with 3 tasks, then read it back.",
        {"code_execution": True})
    chk("fs/anthropic/container-write-read/no-error", not err, err or "")
    chk("fs/anthropic/container-write-read/got-text", len(txt) > 10)
    chk("fs/anthropic/container-write-read/tool-called", has_event(evs, "tool_call"))

    # Check container_id saved
    from session_manager import load_session
    sess = load_session("default")
    chk("fs/anthropic/container-id-saved",
        bool(sess.get("container_ids",{}).get("anthropic")),
        str(sess.get("container_ids",{})))

    # OpenAI: code interpreter
    evs, txt, err = await chat("openai","gpt-4o-mini",
        "Use code interpreter to compute fibonacci(10).",
        {"code_interpreter": True})
    chk("fs/openai/code-interpreter/no-error", not err, err or "")
    chk("fs/openai/code-interpreter/got-text", len(txt) > 5)

    # Gemini: harness bash
    ws_dir = pathlib.Path("workspace")
    test_f = ws_dir / "fs_test.txt"
    if test_f.exists(): test_f.unlink()
    from workspace_executor import execute_bash
    r = execute_bash('echo "filesystem test" > fs_test.txt && cat fs_test.txt')
    chk("fs/gemini/harness-bash/write-read", "filesystem test" in r, f"got: {r[:50]}")
    chk("fs/gemini/harness-bash/file-exists", test_f.exists())


# ═══════════════════════════════════════════════════════
# MEMORY (AGENTS.md pattern)
# ═══════════════════════════════════════════════════════
async def test_memory():
    print("\n── Memory (AGENTS.md pattern) ───────────────────")

    # Check AGENTS.md exists
    chk("memory/agents-md-exists", pathlib.Path("AGENTS.md").exists())
    chk("memory/memories-agents-md-exists", pathlib.Path("memories/AGENTS.md").exists())

    # Test memory tool write + read
    evs, txt, err = await chat("anthropic","claude-sonnet-4-6",
        "Use the memory tool to: 1) view /memories to see existing files, "
        "2) save a note to /memories/test_note.md with content '# Test\nMemory works!', "
        "3) read it back.",
        {"memory": True}, timeout=90)
    chk("memory/write-read/no-error", not err, err or "")
    chk("memory/write-read/got-text", len(txt) > 5)
    chk("memory/write-read/tool-called", has_event(evs, "tool_call"))

    # Verify file on disk
    note = pathlib.Path("memories/test_note.md")
    chk("memory/write-read/file-on-disk", note.exists(), f"path: {note}")

    # Memory persists across session (file still there from previous test)
    mem_files = list(pathlib.Path("memories").rglob("*.md")) if pathlib.Path("memories").exists() else []
    chk("memory/persistence/files-on-disk", len(mem_files) > 0, f"files: {[f.name for f in mem_files]}")


# ═══════════════════════════════════════════════════════
# MCP
# ═══════════════════════════════════════════════════════
async def test_mcp():
    print("\n── MCP Servers ──────────────────────────────────")

    # Public server connectivity
    from mcp_client import test_mcp_server
    r = await test_mcp_server("https://mcp.deepwiki.com/mcp")
    chk("mcp/deepwiki/reachable", r["ok"], str(r.get("error","")) or str(r.get("tools","")))

    if r["ok"]:
        # Anthropic native MCP
        evs, txt, err = await chat("anthropic","claude-sonnet-4-6",
            "Use the DeepWiki MCP server to ask: what is Python programming language?",
            {}, mcp=[{"url":"https://mcp.deepwiki.com/mcp","name":"deepwiki","token":""}], timeout=45)
        chk("mcp/anthropic-native/no-error", not err, err or "")
        chk("mcp/anthropic-native/got-text", len(txt) > 20)
        chk("mcp/anthropic-native/mcp-tool-called", has_event(evs, "tool_call"))

        # OpenAI native MCP
        evs, txt, err = await chat("openai","gpt-4o-mini",
            "Use the DeepWiki MCP server to ask: what is Python programming language?",
            {}, mcp=[{"url":"https://mcp.deepwiki.com/mcp","name":"deepwiki","token":""}], timeout=45)
        is_rl = err and "rate limit" in str(err).lower()
        chk("mcp/openai-native/no-error", not err or is_rl, "(rate-limited)" if is_rl else (err or ""))
        chk("mcp/openai-native/got-text", len(txt) > 5 or is_rl)

    # Local MCP server test
    from mcp_client import MCPClient
    try:
        c = MCPClient("http://localhost:3001/mcp")
        tools = await c.list_tools()
        chk("mcp/local/reachable", len(tools) > 0, f"tools: {[t['name'] for t in tools]}")
    except Exception as e:
        chk("mcp/local/reachable", False, f"not running: {e}")


# ═══════════════════════════════════════════════════════
# AGENTS.MD
# ═══════════════════════════════════════════════════════
async def test_agents_md():
    print("\n── AGENTS.md & System Instructions ─────────────")

    agents_md = pathlib.Path("AGENTS.md")
    mem_agents_md = pathlib.Path("memories/AGENTS.md")

    chk("agents-md/file-exists", agents_md.exists())
    chk("agents-md/memories-exists", mem_agents_md.exists())

    if agents_md.exists():
        content = agents_md.read_text()
        chk("agents-md/has-mission-section", "## Core Mission" in content or "## Memory Protocol" in content or "# LLM" in content)
        chk("agents-md/has-memory-protocol", "memories/AGENTS.md" in content)
        chk("agents-md/has-tool-section", "## Tools" in content)

    if mem_agents_md.exists():
        mem_content = mem_agents_md.read_text()
        chk("agents-md/memory-file-readable", len(mem_content) > 0)

    # All 3 providers load AGENTS.md
    for provider, model in [("anthropic","claude-sonnet-4-6"), ("openai","gpt-4o-mini")]:
        evs, txt, err = await chat(provider, model,
            "In one sentence: what is your primary role or mission?", {})
        chk(f"agents-md/{provider}/loaded-as-system", not err)
        chk(f"agents-md/{provider}/reflects-content",
            any(w in txt.lower() for w in ["research","llm","agent","tool","memory"]),
            txt[:80])


# ═══════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════
def print_summary():
    print("\n" + "═"*55)
    print("SUMMARY")
    print("═"*55)
    passed = sum(1 for _,ok,_ in RESULTS if ok)
    failed = sum(1 for _,ok,_ in RESULTS if not ok)
    total = len(RESULTS)
    print(f"  Passed: {passed}/{total}")
    print(f"  Failed: {failed}/{total}")
    if failed:
        print("\nFailed tests:")
        for name, ok, detail in RESULTS:
            if not ok:
                safe = f"  ❌ {name} {detail}".encode("ascii","replace").decode()
                print(safe)


async def main():
    print("LLM Explorer — Comprehensive Test Suite")
    print(f"WebSocket: {WS}\n")

    # Check server
    try:
        async with websockets.connect(WS): pass
    except Exception:
        print(f"❌ Cannot connect to {WS}")
        print("  Start server: python -m uvicorn main:app --port 8080")
        sys.exit(1)

    suites = sys.argv[1:] if len(sys.argv) > 1 else [
        "agents_md", "anthropic", "openai", "gemini", "filesystem", "memory", "mcp"
    ]

    suite_map = {
        "agents_md": test_agents_md,
        "anthropic": test_anthropic,
        "openai": test_openai,
        "gemini": test_gemini,
        "filesystem": test_filesystem,
        "memory": test_memory,
        "mcp": test_mcp,
    }

    for name in suites:
        if name in suite_map:
            await suite_map[name]()
        else:
            print(f"Unknown suite: {name}")

    print_summary()


if __name__ == "__main__":
    asyncio.run(main())

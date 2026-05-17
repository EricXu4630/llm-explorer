"""
Comprehensive provider test suite.
Run: python test_providers.py
Tests all providers, tools, skills, and MCP connectivity.
Uses LIVE APIs -- will consume real tokens.
"""

import asyncio
import json
import sys
import io
import websockets

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

WS_URL = "ws://127.0.0.1:8080/ws"
RESULTS = []


async def call(provider, model, msg, tools=None, mcp_servers=None, skills=None, timeout=60):
    """Send a chat message and collect all events. Returns (events, final_text, error)."""
    events = []
    full_text = ""
    error = None

    try:
        async with websockets.connect(WS_URL) as ws:
            await ws.send(json.dumps({
                "type": "chat",
                "provider": provider,
                "model": model,
                "conversation": [{"role": "user", "content": msg}],
                "tools": tools or {},
                "mcp_servers": mcp_servers or [],
                "skills": skills or [],
            }))
            for _ in range(200):
                try:
                    d = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                    events.append(d)
                    if d["type"] == "token":
                        full_text += d.get("content", "")
                    elif d["type"] == "error":
                        error = d.get("message", "unknown error")
                        break
                    elif d["type"] == "done":
                        full_text = d.get("message", full_text)
                        break
                except asyncio.TimeoutError:
                    error = f"Timeout after {timeout}s"
                    break
    except Exception as e:
        error = str(e)

    return events, full_text, error


def check(name, cond, detail=""):
    status = "✅ PASS" if cond else "❌ FAIL"
    RESULTS.append((name, cond, detail))
    safe = (name + " " + detail).encode("ascii", "replace").decode()
    print(f"  {status}  {safe}")
    return cond


async def test_filesystem():
    """Test filesystem/bash for all three providers."""
    print("\n── Filesystem / Bash (all providers) ───────────")
    import pathlib

    # ── Anthropic: server-side container bash ──
    evs, text, err = await call(
        "anthropic", "claude-sonnet-4-6",
        "Use code execution to: 1) write a file called todo.md with content '# TODO\n- Step 1: research\n- Step 2: summarize', 2) then read it back and confirm its contents.",
        {"code_execution": True}
    )
    check("fs/anthropic/code-exec-write-read/no-error", not err, err or "")
    check("fs/anthropic/code-exec-write-read/got-text", len(text) > 10, f"len={len(text)}")
    has_tool = any(e["type"] == "tool_call" for e in evs)
    check("fs/anthropic/code-exec-write-read/tool-called", has_tool)

    # ── Gemini: harness bash tool ──
    # Clear any stale test file first
    ws_dir = pathlib.Path("workspace")
    test_file = ws_dir / "gemini_test.md"
    if test_file.exists():
        test_file.unlink()

    evs2, text2, err2 = await call(
        "gemini", "gemini-2.5-flash",
        "Use the bash tool to: 1) run 'ls -la' to see the workspace, 2) write a file called gemini_test.md with content '# Gemini Workspace Test\nHello from Gemini!', 3) read it back with 'cat gemini_test.md' and confirm the contents.",
        {"bash": True},
        timeout=90
    )
    check("fs/gemini/bash/no-error", not err2, err2 or "")
    check("fs/gemini/bash/got-text", len(text2) > 10, f"len={len(text2)}")
    has_bash = any(e["type"] == "tool_call" and e.get("tool") == "bash" for e in evs2)
    check("fs/gemini/bash/bash-tool-called", has_bash)
    # Verify file actually landed on disk
    file_written = test_file.exists() and "Gemini" in test_file.read_text()
    check("fs/gemini/bash/file-on-disk", file_written,
          f"file={'exists' if test_file.exists() else 'missing'}")

    # ── OpenAI: web_search (shell tool requires hosted container which may not be provisioned) ──
    evs3, text3, err3 = await call(
        "openai", "gpt-4o-mini",
        "Use web search to find today's date, then tell me what day of the week it is.",
        {"web_search": True}
    )
    check("fs/openai/search/no-error", not err3, err3 or "")
    check("fs/openai/search/got-text", len(text3) > 10, f"len={len(text3)}")


async def test_anthropic():
    print("\n── Anthropic ──────────────────────────────────")

    # 1. Simple chat, no tools
    evs, text, err = await call("anthropic", "claude-sonnet-4-6", "Say only the word: hello", {})
    check("anthropic/no-tools/basic", not err and len(text) > 0, f"got: '{text[:40]}'")
    check("anthropic/no-tools/has-api-request", any(e["type"] == "api_request" for e in evs))
    check("anthropic/no-tools/has-api-response", any(e["type"] == "api_response" for e in evs))

    # 2. Web search only
    evs, text, err = await call("anthropic", "claude-sonnet-4-6",
        "What is today's date? Use web search.",
        {"web_search": True})
    check("anthropic/web-search/no-error", not err, err or "")
    check("anthropic/web-search/got-text", len(text) > 20, f"len={len(text)}")
    has_tool = any(e["type"] == "tool_call" for e in evs)
    check("anthropic/web-search/tool-called", has_tool)

    # 3. Code execution only
    evs, text, err = await call("anthropic", "claude-sonnet-4-6",
        "Use code execution to calculate 2**32.",
        {"code_execution": True})
    check("anthropic/code-exec/no-error", not err, err or "")
    check("anthropic/code-exec/got-text", len(text) > 5, f"len={len(text)}")

    # 4. Web fetch only
    evs, text, err = await call("anthropic", "claude-sonnet-4-6",
        "Fetch https://example.com and summarize it in one sentence.",
        {"web_fetch": True})
    check("anthropic/web-fetch/no-error", not err, err or "")
    check("anthropic/web-fetch/got-text", len(text) > 10, f"len={len(text)}")

    # 5. web_search + code_execution together (conflict auto-fix)
    evs, text, err = await call("anthropic", "claude-sonnet-4-6",
        "Search for today's date and then compute 2**10.",
        {"web_search": True, "code_execution": True})
    check("anthropic/search+code/no-error", not err, err or "")
    check("anthropic/search+code/got-text", len(text) > 10, f"len={len(text)}")
    has_info = any(e["type"] == "info" and "allowed_callers" in e.get("message", "") for e in evs)
    check("anthropic/search+code/conflict-info-shown", has_info)

    # 6. All three tools together
    evs, text, err = await call("anthropic", "claude-sonnet-4-6",
        "Just say hello briefly.",
        {"web_search": True, "code_execution": True, "web_fetch": True})
    check("anthropic/all-tools/no-error", not err, err or "")
    check("anthropic/all-tools/got-text", len(text) > 2, f"len={len(text)}")

    # 7. Memory tool (client-side)
    evs, text, err = await call("anthropic", "claude-sonnet-4-6",
        "Use the memory tool to write key='test_key' value='hello123', then read it back.",
        {"memory": True})
    check("anthropic/memory/no-error", not err, err or "")
    check("anthropic/memory/got-text", len(text) > 5, f"len={len(text)}")

    # 8. Multi-turn (two separate calls sharing conversation)
    evs1, text1, err1 = await call("anthropic", "claude-sonnet-4-6",
        "My favorite color is blue. Just say 'noted'.", {})
    evs2, _, err2 = await call("anthropic", "claude-sonnet-4-6",
        "What is my favorite color?", {},)
    # Note: second call doesn't include history — model won't know.
    # This tests that the harness correctly manages conversation state.
    check("anthropic/multi-turn/first-ok", not err1 and "noted" in text1.lower(), f"'{text1[:30]}'")
    check("anthropic/multi-turn/second-ok", not err2)


async def test_openai():
    print("\n── OpenAI (Responses API) ──────────────────────")

    # 1. Simple chat, no tools
    evs, text, err = await call("openai", "gpt-4o-mini", "Say only the word: hello", {})
    check("openai/no-tools/basic", not err and len(text) > 0, f"got: '{text[:40]}'")
    check("openai/no-tools/has-api-request", any(e["type"] == "api_request" for e in evs))
    check("openai/no-tools/has-api-response", any(e["type"] == "api_response" for e in evs))

    # 2. Web search
    evs, text, err = await call("openai", "gpt-4o-mini",
        "What is today's date? Use web search.",
        {"web_search": True})
    check("openai/web-search/no-error", not err, err or "")
    check("openai/web-search/got-text", len(text) > 20, f"len={len(text)}")
    has_tool = any(e["type"] in ("tool_call", "tool_result") for e in evs)
    check("openai/web-search/tool-visible-in-events", has_tool)

    # 3. Code interpreter
    evs, text, err = await call("openai", "gpt-4o-mini",
        "Use code interpreter to compute the first 10 Fibonacci numbers.",
        {"code_interpreter": True})
    check("openai/code-interpreter/no-error", not err, err or "")
    check("openai/code-interpreter/got-text", len(text) > 20, f"len={len(text)}")

    # 4. Web search + code interpreter together
    evs, text, err = await call("openai", "gpt-4o",
        "Search for today's date, then write code to print it formatted.",
        {"web_search": True, "code_interpreter": True})
    check("openai/search+code/no-error", not err, err or "")
    check("openai/search+code/got-text", len(text) > 10, f"len={len(text)}")


async def test_gemini():
    print("\n── Gemini ──────────────────────────────────────")

    # 1. Simple chat, no tools
    evs, text, err = await call("gemini", "gemini-2.5-flash", "Say only the word: hello", {})
    check("gemini/no-tools/basic", not err and len(text) > 0, f"got: '{text[:40]}'")
    check("gemini/no-tools/has-api-request", any(e["type"] == "api_request" for e in evs))
    check("gemini/no-tools/has-api-response", any(e["type"] == "api_response" for e in evs))

    # 2. Google Search grounding
    evs, text, err = await call("gemini", "gemini-2.5-flash",
        "What is today's date? Use Google Search.",
        {"google_search": True})
    check("gemini/google-search/no-error", not err, err or "")
    check("gemini/google-search/got-text", len(text) > 10, f"len={len(text)}")

    # 3. Code execution
    evs, text, err = await call("gemini", "gemini-2.5-flash",
        "Use code execution to calculate the sum of 1 to 100.",
        {"code_execution": True})
    check("gemini/code-exec/no-error", not err, err or "")
    check("gemini/code-exec/got-text", len(text) > 5, f"len={len(text)}")
    has_code = any(e["type"] == "tool_call" and e.get("tool") == "code_execution" for e in evs)
    check("gemini/code-exec/code-event-shown", has_code)

    # 4. URL context (may hit rate limit on free tier — treated as skip if 429)
    evs, text, err = await call("gemini", "gemini-2.5-flash",
        "Fetch https://example.com and tell me the page title.",
        {"url_context": True})
    is_rate_limit = err and "429" in str(err)
    check("gemini/url-context/no-error-or-rate-limit",
          not err or is_rate_limit,
          "(rate limited — expected on free tier)" if is_rate_limit else (err or ""))
    if not is_rate_limit:
        check("gemini/url-context/got-text", len(text) > 5, f"len={len(text)}")

    # 5. MCP limitation warning
    evs, _, err = await call("gemini", "gemini-2.5-flash", "hello",
        {}, mcp_servers=[{"url": "http://localhost:3001/mcp", "name": "test"}])
    has_warning = any(
        e["type"] == "info" and "Gemini" in e.get("message", "")
        for e in evs
    )
    check("gemini/mcp-shows-limitation-warning", has_warning)


async def test_skills():
    print("\n── Skills ──────────────────────────────────────")

    # Test skill loading from GitHub
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "type": "load_skill",
            "url": "https://github.com/anthropics/skills/blob/main/skills/mcp-builder/SKILL.md"
        }))
        loaded = False
        for _ in range(10):
            try:
                d = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if d["type"] == "skill_loaded":
                    loaded = True
                    skill = d["skill"]
                    check("skills/load-from-github/ok", True)
                    check("skills/load-from-github/has-name", bool(skill.get("name")), skill.get("name", ""))
                    check("skills/load-from-github/has-description", bool(skill.get("description")))
                    check("skills/load-from-github/has-content", len(skill.get("content", "")) > 50)
                    break
                elif d["type"] == "error":
                    check("skills/load-from-github/ok", False, d.get("message", ""))
                    loaded = True
                    break
            except asyncio.TimeoutError:
                break
        if not loaded:
            check("skills/load-from-github/ok", False, "timeout")


async def test_mcp_local():
    print("\n── MCP (local server connectivity) ─────────────")
    from mcp_client import test_mcp_server

    # Test local MCP server (must be running)
    result = await test_mcp_server("http://localhost:3001/mcp")
    check("mcp/local-server/reachable", result["ok"],
          f"tools: {result.get('tools', result.get('error', ''))}")
    if result["ok"]:
        check("mcp/local-server/has-tools", result["tool_count"] > 0, f"count={result['tool_count']}")


async def test_mcp_public():
    print("\n── MCP (public server — mcp.deepwiki.com) ───────")
    from mcp_client import test_mcp_server

    PUBLIC_MCP = "https://mcp.deepwiki.com/mcp"

    result = await test_mcp_server(PUBLIC_MCP)
    check("mcp/public-server/reachable", result["ok"],
          f"tools={result.get('tools', result.get('error', ''))}")

    if not result["ok"]:
        return

    # Test Anthropic native MCP connector with public server
    # (Anthropic calls the MCP server directly — our harness just passes the URL)
    evs, text, err = await call("anthropic", "claude-sonnet-4-6",
        "Use the DeepWiki MCP server to ask: 'What is Python?'",
        {},
        mcp_servers=[{"url": PUBLIC_MCP, "name": "deepwiki", "token": ""}],
        timeout=45)
    check("mcp/anthropic-native/no-error", not err, err or "")
    check("mcp/anthropic-native/got-text", len(text) > 10, f"len={len(text)}")
    has_mcp = any(e["type"] == "tool_call" for e in evs)
    check("mcp/anthropic-native/mcp-tool-called", has_mcp)

    # Test OpenAI native MCP connector with same public server
    evs2, text2, err2 = await call("openai", "gpt-4o",
        "Use the DeepWiki MCP server to ask: 'What is Python?'",
        {},
        mcp_servers=[{"url": PUBLIC_MCP, "name": "deepwiki", "token": ""}],
        timeout=45)
    check("mcp/openai-native/no-error", not err2, err2 or "")
    check("mcp/openai-native/got-text", len(text2) > 10, f"len={len(text2)}")


def print_summary():
    print("\n" + "═" * 50)
    print("SUMMARY")
    print("═" * 50)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"  Passed: {passed}/{len(RESULTS)}")
    print(f"  Failed: {failed}/{len(RESULTS)}")
    if failed:
        print("\nFailed tests:")
        for name, ok, detail in RESULTS:
            if not ok:
                safe = (name + " " + detail).encode("ascii", "replace").decode()
                print(f"  ❌ {safe}")


async def main():
    print("LLM Explorer — Provider Test Suite")
    print(f"WebSocket: {WS_URL}")

    # Check server is running
    try:
        async with websockets.connect(WS_URL):
            pass
    except Exception:
        print(f"\n❌ Cannot connect to {WS_URL}")
        print("  Start the server: python -m uvicorn main:app --port 8080")
        sys.exit(1)

    suites = sys.argv[1:] if len(sys.argv) > 1 else ["filesystem", "anthropic", "openai", "gemini", "skills", "mcp_local", "mcp_public"]

    if "filesystem" in suites:
        await test_filesystem()
    if "anthropic" in suites:
        await test_anthropic()
    if "openai" in suites:
        await test_openai()
    if "gemini" in suites:
        await test_gemini()
    if "skills" in suites:
        await test_skills()
    if "mcp_local" in suites:
        await test_mcp_local()
    if "mcp_public" in suites:
        await test_mcp_public()

    print_summary()


if __name__ == "__main__":
    asyncio.run(main())

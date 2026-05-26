# Thin Harness, Fat Skills

**How much agent power can you get from LLM provider APIs alone — with almost no scaffolding?**

A multi-provider agent harness for Anthropic, OpenAI, and Gemini that delegates everything possible to the provider APIs. Web search, code execution, persistent containers, URL fetching, image generation, MCP connectors, Skills APIs, memory tools — all handled natively by the provider. The harness just wires them together.

**~3,500 lines total. ~550 lines that actually execute anything.**

![screenshot placeholder — add a gif here]

---

## Why this exists

Most agent frameworks re-implement capabilities the provider APIs already have: web search, code execution, filesystem access, tool calling loops, memory. They add thousands of lines of orchestration on top of things that are already built in.

This project tests the opposite premise: build the thinnest possible wrapper, delegate everything to the raw APIs, and measure what's left. The answer is surprisingly little.

---

## What the provider handles — you just configure it

These run entirely on the provider's infrastructure. The harness passes a config flag. That's it.

| Capability | Anthropic | OpenAI | Gemini |
|---|---|---|---|
| **Web search** | `web_search_20260209` | `web_search_preview` | Google Search grounding |
| **Code / bash** | `code_execution_20250825`<br>bash+Python, 30-day container | `code_interpreter` (Python)<br>`shell` (bash, `/mnt/data`) | Python stateless (30s) |
| **URL fetch** | `web_fetch_20260209` | — | `url_context` |
| **Image generation** | — | `image_generation` (gpt-image-2) | — |
| **Maps / location** | — | — | `google_maps` |
| **MCP tool calls** | Native API, server-side | Native API, server-side | — |
| **Skills (SKILL.md)** | Native Skills API<br>progressive disclosure | `shell.environment.skills` | system prompt injection |
| **Memory tool** | `memory_20250818` | — | — |
| **Advisor tool** | `advisor_20260301` (beta)<br>Sonnet consults Opus mid-task | — | — |
| **Cross-session** | `container_id` (30d)<br>Files API (indefinite) | `previous_response_id` (30d)<br>Conversations API (indefinite) | local disk only |
| **Context window** | 1M tokens (Opus/Sonnet)<br>200K (Haiku) | 1M (gpt-5.5)<br>128K others | 1M+ all 2.5/3 models |

---

## What the harness does

The only code that *executes* anything:

| File | Lines | What it runs |
|---|---|---|
| `memory_manager.py` | ~250 | Writes to `memories/` when model calls `memory_20250818` |
| `workspace_executor.py` | ~300 | Runs bash commands in `workspace/` for Gemini |
| **everything else** | ~2,900 | Routing, config forwarding, UI, session state — nothing executed |

The three provider wrappers (`providers/*.py`, ~1,075 lines combined) are pure API forwarding. They build the request, stream the response, and relay events to the UI. They don't execute tools.

The local machine stores three directories:
- `memories/` — model's written memory (permanent)
- `workspace/` — bash sandbox for Gemini (permanent)
- `sessions/` — container IDs for cross-session persistence

All compute — inference, search, code execution, image generation — happens on the provider's infrastructure.

---

## How tool execution works

Tools in the UI are labelled **API** or **harness**. The distinction:

**API tools** — provider servers handle everything. Harness never sees the intermediate steps:
```
user message
  → harness sends request with tool config
  → provider executes internally (web search, code run, image gen...)
  → provider returns final answer
```

**Harness tools** — model generates a tool call, provider sends it *back* to the harness, harness executes locally, result goes back to the model. The `← harness` label shows this return flow:
```
user message
  → harness sends request with tool config
  → model decides to call: memory("write /memories/notes.md", "...")
  → provider returns the tool_call to the harness   ← execution comes back here
  → harness writes the file to disk
  → harness sends result back to provider
  → model sees result and continues
```

You use both the same way — enable the checkbox, ask naturally. The loop is handled automatically.

| Tool | Executes on |
|---|---|
| Web search | Provider servers |
| Code execution | Provider container |
| URL fetch | Provider servers |
| Image generation | Provider servers |
| MCP tool calls | Your MCP server (provider calls it) |
| `memory_20250818` | **Your machine** — `memories/` |
| `bash_20250124` | **Your machine** — `workspace/` |
| Gemini bash | **Your machine** — `workspace/` |

---

## AGENTS.md and memory

`AGENTS.md` (repo root) is the static system prompt, loaded on every call. Defines role, tools, behavior. You edit this — the model never writes to it.

`memories/AGENTS.md` is model-writable via `memory_20250818`. It accumulates learned preferences, facts, and context across sessions. The model reads it at session start to restore context.

```
session 1:  model learns something → writes memories/AGENTS.md
session 2:  model reads /memories → picks up where it left off
```

Works with Anthropic only (memory tool is Anthropic-native). OpenAI and Gemini rely on conversation history.

---

## Skills (SKILL.md standard)

Load any skill from a GitHub URL in the sidebar. The harness zips the SKILL.md and uploads it to the provider Skills API once, then caches the `skill_id`. After the first upload, only the `skill_id` is sent — the full content never appears in requests again.

| Step | What happens |
|---|---|
| First load | Harness zips SKILL.md, uploads to provider, caches `skill_id` locally |
| Every call after | Only `skill_id` sent — no content in the request |
| Progressive disclosure (Anthropic + OpenAI) | L1: name + description only (~100 tokens, always). L2: full SKILL.md, on trigger. L3: bundled resources, on demand. |
| Gemini | No native Skills API — full SKILL.md injected into system prompt every call |

Browse skills at [skillsmp.com](https://skillsmp.com).

---

## MCP servers

Add any public MCP server URL in the sidebar. Anthropic and OpenAI call your MCP server natively — server-side, no client loop. Gemini has no native MCP connector.

Test with a free public server: `mcp.deepwiki.com/mcp`

---

## Current models

| Provider | Recommended | Others |
|---|---|---|
| Anthropic | `claude-opus-4-7`, `claude-sonnet-4-6` (1M ctx) | `claude-haiku-4-5` (200K), `claude-opus-4-6`, `claude-sonnet-4-5` |
| OpenAI | `gpt-5.5` (1M ctx), `gpt-5.4`, `gpt-5.4-mini` | `gpt-5.4-nano`, `gpt-5`, `gpt-4.1`, `gpt-4o`, `o3` |
| Gemini | `gemini-2.5-pro`, `gemini-2.5-flash` (1M ctx) | `gemini-2.5-flash-lite`, `gemini-3.1-pro-preview`, `gemini-3-flash-preview`, `gemini-2.0-flash` |

*Verified against live provider docs 2026-05-17*

---

## Getting started

```bash
git clone https://github.com/EricXu4630/llm-explorer
cd llm-explorer
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8080 --reload
```

Open `http://127.0.0.1:8080`. Enter your API key in the Provider panel. Keys are stored in `localStorage` — no server setup needed.

**Optional `.env` for server-side keys** (e.g. for Railway deployment):
```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...
```

---

## File structure

```
providers/
  anthropic_provider.py     # Anthropic Messages API — streaming, tools, memory loop
  openai_provider.py        # OpenAI Responses API — streaming, shell, skills, images
  gemini_provider.py        # Gemini GenAI SDK — function-calling loop, grounding
memory_manager.py           # memory_20250818 → reads/writes memories/ on disk
workspace_executor.py       # bash tool → runs commands in workspace/ on disk
mcp_client.py               # Streamable HTTP MCP client (for local test server)
mcp_server.py               # Local test MCP server (FastMCP, port 3001)
main.py                     # FastAPI app + WebSocket router
session_manager.py          # Saves container_id + response_id to sessions/
skills_manager.py           # SKILL.md upload + skill_id cache
static/
  app.js                    # WebSocket client, tool cards, inspector, resize
  style.css
  index.html
  about.html                # Reference tables for all capabilities
memories/                   # Model-writable memory (permanent, not committed)
workspace/                  # Bash sandbox (permanent, not committed)
sessions/                   # Container IDs (local, not committed)
AGENTS.md                   # Static system prompt — edit this
Procfile                    # Railway deployment
railway.toml
```

---

## Deployment

Configured for Railway out of the box:

```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` in the Railway dashboard. Users can also enter keys directly in the UI without touching the server config.

> **Note:** Railway has an ephemeral filesystem — `memories/`, `workspace/`, and `sessions/` reset on redeploy. Add a persistent volume or external storage if you need cross-deploy memory.

---

## Harness code breakdown

```
Provider wrappers   ~1,075 lines   pure API forwarding, nothing executed
Client-side tools     ~550 lines   memory writes + bash commands (local execution)
Infrastructure        ~260 lines   routing, session persistence
Frontend            ~1,600 lines   UI, inspector, tool cards

Total               ~3,487 lines
```

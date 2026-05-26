# Thin Harness, Fat Skills

> How much agent orchestration can you get from the LLM provider APIs alone — with almost no scaffolding?

This repo is an answer to that question. It's a multi-provider agent harness for Anthropic, OpenAI, and Gemini that delegates everything possible to the provider APIs and measures what's left.

**The result:** ~550 lines of code that actually *execute* anything. The rest is just wiring.

---

## Motivation

Modern LLM providers — Anthropic, OpenAI, Gemini — have quietly built most of what an "agent framework" provides directly into their APIs: web search, code execution, persistent containers, filesystem access, URL fetching, image generation, MCP connectors, Skills APIs, memory tools, advisor models. Most agent frameworks re-implement these at the application layer, adding thousands of lines of scaffolding on top of capabilities the API already provides natively.

This project tests the limits of that premise. Build the thinnest possible wrapper. See how far the raw APIs take you.

---

## What the provider handles (you just configure it)

| Capability | Anthropic | OpenAI | Gemini |
|---|---|---|---|
| Web search | `web_search_20260209` | `web_search_preview` | Google Search grounding |
| Code / bash execution | `code_execution_20250825` — bash+Python, 30d container | `code_interpreter` + `shell` — Python + bash, `/mnt/data` | Python stateless |
| URL fetch | `web_fetch_20260209` | — | `url_context` |
| Image generation | — | `image_generation` (gpt-image-2) | — |
| Maps | — | — | `google_maps` |
| MCP tool calls | Native API, server-side | Native API, server-side | — |
| Skills (SKILL.md) | Native Skills API, progressive disclosure | `shell.environment.skills` | system prompt injection |
| Memory tool | `memory_20250818` | — | — |
| Advisor (Sonnet→Opus) | `advisor_20260301` beta | — | — |
| Cross-session persistence | `container_id` (30d) + Files API (∞) | `previous_response_id` (30d) + Conversations API (∞) | local disk only |
| Context window | 1M tokens (Opus/Sonnet) | 1M (gpt-5.5), 128K others | 1M+ all 2.5/3 models |

## What the harness does (~550 lines that actually execute)

| File | Lines | What it executes |
|---|---|---|
| `memory_manager.py` | ~250 | Reads/writes `memories/` when model calls `memory_20250818` |
| `workspace_executor.py` | ~300 | Runs bash commands in `workspace/` for Gemini |
| everything else | ~2,900 | Routing, config forwarding, UI, session persistence — nothing executed |

The three provider wrappers (`providers/*.py`, ~1,075 lines combined) are pure API forwarding. They build the request, stream the response, and relay events to the frontend. They don't execute tools.

---

## Features

- **Chat UI** with provider toggle (Anthropic / OpenAI / Gemini) and model selector
- **API Inspector** — every raw request and response, human-readable summary + collapsible raw JSON
- **Tool cards** — real-time visibility into which tools fired, with inputs shown
- **MCP connector** — add any public MCP server URL; Anthropic and OpenAI call it natively
- **Skills** — load any SKILL.md from a GitHub URL; uploaded once, `skill_id` cached
- **AGENTS.md** — static system prompt loaded on every call; `memories/AGENTS.md` is model-writable
- **API key input** — enter keys in the UI sidebar, stored in `localStorage`; no `.env` required for users
- **Resizable panels** — drag handles between sidebar, chat, and inspector
- **About page** — reference tables for all capabilities, tool routing, file structure, model list

---

## How tool execution works

Tools in the UI are labelled either **API** or **harness**. The distinction matters:

**API tools** — the provider's servers handle everything. The harness just includes the tool in the request and receives the final answer. It never sees the intermediate steps.

```
user message
    → harness builds API request (tools: [web_search, ...])
    → provider searches the web internally
    → provider returns answer
```

**Harness tools** — the model decides to call a tool, the provider sends that call *back* to the harness, the harness executes it locally, then sends the result back so the model can continue. The `←` in the UI label shows this return flow.

```
user message
    → harness builds API request (tools: [memory, ...])
    → model generates a tool_call: memory("write /memories/notes.md", "...")
    → provider returns the tool_call to the harness  ← execution comes back here
    → harness writes the file to disk
    → harness sends tool result back to provider
    → model sees the result and continues
```

You use both the same way — enable the checkbox, ask naturally. The harness handles the execution loop automatically.

| Tool | Provider | Executes on |
|---|---|---|
| Web search | All | Provider servers |
| Code execution | Anthropic, OpenAI | Provider container |
| URL fetch | Anthropic, Gemini | Provider servers |
| Image generation | OpenAI | Provider servers |
| MCP tool calls | Anthropic, OpenAI | Your MCP server (provider calls it) |
| Memory (`memory_20250818`) | Anthropic | **Your machine** — `memories/` directory |
| Bash (`bash_20250124`) | Anthropic | **Your machine** — `workspace/` directory |
| Bash function | Gemini | **Your machine** — `workspace/` directory |

The local machine is only used for file storage: `memories/` (model memory), `workspace/` (bash sandbox), and `sessions/` (container IDs for cross-session persistence). All compute happens on the provider's infrastructure.

---

## Getting started

```bash
git clone https://github.com/EricXu4630/llm-explorer
cd llm-explorer
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8080 --reload
```

Open `http://127.0.0.1:8080`. Enter your API key in the Provider panel on the left. No `.env` needed — keys are stored in your browser's `localStorage`.

**Optional `.env` for server-side keys:**
```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...
```

---

## File structure

```
providers/
  anthropic_provider.py   # Anthropic Messages API
  openai_provider.py      # OpenAI Responses API
  gemini_provider.py      # Gemini GenAI SDK
memory_manager.py         # memory_20250818 tool → memories/ on disk
workspace_executor.py     # bash tool → workspace/ on disk
mcp_client.py             # Streamable HTTP MCP client
mcp_server.py             # Local test MCP server (port 3001)
main.py                   # FastAPI app + WebSocket router
session_manager.py        # Container ID + response ID persistence
skills_manager.py         # SKILL.md upload + skill_id cache
static/
  app.js                  # Frontend — WebSocket, UI, inspector
  style.css
  index.html
  about.html              # Reference page
memories/                 # Model-writable memory (permanent)
workspace/                # Bash sandbox files (permanent)
sessions/                 # Container IDs (local, not committed)
AGENTS.md                 # Static system prompt — edit this
```

---

## AGENTS.md and memory

`AGENTS.md` is the static system prompt loaded on every call. You edit it to define the agent's role and behavior.

`memories/AGENTS.md` is written by the model using the `memory_20250818` tool (Anthropic). It accumulates facts, preferences, and context across sessions. The model reads it at session start.

Cross-session flow:
```
model calls memory tool → harness writes memories/AGENTS.md
next session → model reads /memories → picks up where it left off
```

---

## Skills (SKILL.md standard)

Load any skill from a GitHub URL in the sidebar. The harness zips the SKILL.md and uploads it to the provider's Skills API. After the first upload, only the `skill_id` is sent — the full content is never in the request again.

- **Anthropic + OpenAI**: native Skills API with progressive disclosure (L1: name only, L2: full content on trigger, L3: bundled resources)
- **Gemini**: no native Skills API — full content injected into system prompt

Browse skills at [skillsmp.com](https://skillsmp.com).

---

## Deployment

Configured for Railway (`Procfile` + `railway.toml` included). Set API keys as environment variables in the Railway dashboard.

```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Note: Railway has an ephemeral filesystem — `memories/`, `workspace/`, and `sessions/` reset on redeploy. Add a persistent volume or database if you need cross-deploy memory.

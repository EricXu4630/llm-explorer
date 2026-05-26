/* ═══════════════════════════════════════════════════
   LLM Explorer — app.js
   Thin harness: conversation state + WebSocket events.
   Everything else handled by the LLM provider's API.
═══════════════════════════════════════════════════ */

// ─── State ──────────────────────────────────────────
const state = {
  provider: "anthropic",
  model: null,
  conversation: [],
  mcpServers: [],
  skills: [],
  tools: {},
  busy: false,
  ws: null,
  streamEl: null,
  streamText: "",
  inspCount: 0,
  apiKeys: JSON.parse(localStorage.getItem("llm_explorer_api_keys") || "{}"),
};

// ─── Provider Config ─────────────────────────────────
// Verified against live provider docs, 2026-05-17
const PROVIDERS = {
  anthropic: {
    // https://platform.claude.com/docs/en/about-claude/models/overview
    models: [
      "claude-opus-4-7",        // Latest flagship — 1M ctx, best agentic coding
      "claude-sonnet-4-6",      // Best speed/intelligence — 1M ctx
      "claude-haiku-4-5",       // Fastest — 200K ctx
      "claude-opus-4-6",        // Previous Opus
      "claude-sonnet-4-5",      // Previous Sonnet
    ],
    // https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference
    tools: [
      { id:"web_search",    label:"Web Search",         icon:"🔍", note:"API · web_search_20260209",    cls:"native"  },
      { id:"code_execution",label:"Code Execution",     icon:"💻", note:"API · bash+Python · 30d container", cls:"native" },
      { id:"web_fetch",     label:"Web Fetch",          icon:"🌐", note:"API · web_fetch_20260209",     cls:"native"  },
      { id:"memory",        label:"Memory",             icon:"🧠", note:"harness · model writes notes to memories/ on disk", cls:"harness" },
      { id:"bash_tool",     label:"Bash Tool",          icon:"🖥️", note:"harness · model runs commands in workspace/ on disk",  cls:"harness" },
      { id:"advisor",       label:"Advisor",            icon:"🎯", note:"API beta · Sonnet consults Opus mid-task", cls:"native" },
    ],
    mcpNote: "✅ Native API MCP (mcp-client-2025-11-20) — Anthropic calls MCP servers server-side. Needs public HTTPS.",
    defaultTools: ["web_search"],
    contextInfo: "1M tokens (Opus/Sonnet) · 200K (Haiku)",
  },
  openai: {
    // https://developers.openai.com/api/docs/models/all
    models: [
      "gpt-5.5",        // Flagship 2026 · 1M ctx · computer use · all tools
      "gpt-5.4",        // Affordable for coding + professional work
      "gpt-5.4-mini",   // Strongest mini · coding + subagents
      "gpt-5.4-nano",   // Cheapest gpt-5.4 class · high-volume
      "gpt-5",          // Reasoning model
      "gpt-4.1",        // Smartest non-reasoning model
      "gpt-4o",         // General purpose (legacy)
      "o3",             // Reasoning · complex tasks
    ],
    // https://developers.openai.com/api/docs/guides/tools
    tools: [
      { id:"web_search",       label:"Web Search",       icon:"🔍", note:"API · web_search_preview",          cls:"native"  },
      { id:"code_interpreter", label:"Code Interpreter",  icon:"💻", note:"API · hosted Python sandbox",        cls:"native"  },
      { id:"shell",            label:"Shell /mnt/data",   icon:"🖥️", note:"API · hosted bash + filesystem",     cls:"native"  },
      { id:"image_generation", label:"Image Generation",  icon:"🎨", note:"API · generates image server-side",  cls:"native"  },
      { id:"file_search",      label:"File Search",       icon:"📚", note:"API · RAG · needs vector_store_ids", cls:"native" },
      { id:"computer",         label:"Computer Use",      icon:"🖱️", note:"harness must screenshot + control mouse/keyboard · not implemented", cls:"harness", disabled:true },
    ],
    mcpNote: "✅ Native API MCP (type:mcp) — OpenAI calls MCP servers server-side. Needs public HTTPS.",
    defaultTools: ["web_search"],
    contextInfo: "1M tokens (gpt-5.5) · 128K (others)",
  },
  gemini: {
    // https://ai.google.dev/gemini-api/docs/models
    models: [
      "gemini-2.5-pro",              // Most advanced · complex reasoning
      "gemini-2.5-flash",            // Best price-performance · reasoning
      "gemini-2.5-flash-lite",       // Fastest + cheapest 2.5
      "gemini-3.1-pro-preview",      // Gemini 3.1 Pro Preview (NEW)
      "gemini-3-flash-preview",      // Gemini 3 Flash Preview (NEW)
      "gemini-2.0-flash",            // Stable 2.0
    ],
    // https://ai.google.dev/gemini-api/docs/tools
    tools: [
      { id:"google_search",  label:"Google Search",    icon:"🔍", note:"API · Google Search grounding",  cls:"native"  },
      { id:"code_execution", label:"Code Execution",   icon:"💻", note:"API · Python · 30s · stateless", cls:"native"  },
      { id:"url_context",    label:"URL Context",      icon:"🌐", note:"API · fetches URLs server-side", cls:"native"  },
      { id:"google_maps",    label:"Google Maps",      icon:"🗺️", note:"API · location + places",        cls:"native"  },
      { id:"bash",           label:"Bash (workspace/)",icon:"🖥️", note:"harness · model runs commands in workspace/ on disk", cls:"harness" },
    ],
    mcpNote: "❌ No native MCP — Gemini has no API-level MCP connector. MCP servers are ignored. Use Anthropic/OpenAI for native MCP.",
    defaultTools: ["google_search"],
    contextInfo: "1M tokens (all 2.5/3 models)",
  },
};

// ─── Init ────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  connectWS();
  selectProvider("anthropic");
  document.getElementById("provider-tabs").addEventListener("click", e => {
    const btn = e.target.closest("[data-provider]");
    if (btn) selectProvider(btn.dataset.provider);
  });
});

function connectWS() {
  state.ws = new WebSocket(`ws://${location.host}/ws`);
  state.ws.onmessage = e => handleEvent(JSON.parse(e.data));
  state.ws.onclose = () => {
    setTimeout(connectWS, 2000);
    appendInfo("WebSocket disconnected — reconnecting…");
  };
  state.ws.onerror = () => {};
}

// ─── Provider Selection ──────────────────────────────
function selectProvider(name) {
  state.provider = name;
  const cfg = PROVIDERS[name];

  document.querySelectorAll(".ptab").forEach(t => t.classList.toggle("active", t.dataset.provider === name));

  const sel = document.getElementById("model-select");
  sel.innerHTML = cfg.models.map(m => `<option value="${m}">${m}</option>`).join("");
  state.model = cfg.models[0];
  sel.onchange = () => { state.model = sel.value; updateChatHeader(); };

  // Tools
  state.tools = {};
  cfg.tools.forEach(t => { state.tools[t.id] = !t.disabled && cfg.defaultTools.includes(t.id); });
  document.getElementById("tools-list").innerHTML = cfg.tools.map(t => `
    <div class="tool-row${t.disabled ? " tool-row-disabled" : ""}">
      <label>
        <input type="checkbox" ${state.tools[t.id] ? "checked" : ""} ${t.disabled ? "disabled" : ""} onchange="toggleTool('${t.id}',this.checked)"/>
        <span class="tool-icon">${t.icon}</span>
        <span class="tool-name">${t.label}</span>
        ${t.disabled ? `<span class="tool-unimplemented">not implemented</span>` : ""}
      </label>
      <span class="tool-note ${t.cls}">${t.note}</span>
    </div>`).join("");

  document.getElementById("mcp-note").textContent = cfg.mcpNote;

  // API key field
  const keyLabels = { anthropic: "Anthropic API Key (sk-ant-…)", openai: "OpenAI API Key (sk-…)", gemini: "Gemini API Key (AIza…)" };
  document.getElementById("api-key-label").textContent = keyLabels[name] || "API Key";
  const keyInput = document.getElementById("api-key-input");
  keyInput.value = state.apiKeys[name] || "";
  updateKeyStatus(name);

  updateChatHeader();
}

function saveApiKey(value) {
  const k = value.trim();
  state.apiKeys[state.provider] = k;
  localStorage.setItem("llm_explorer_api_keys", JSON.stringify(state.apiKeys));
  updateKeyStatus(state.provider);
}

function updateKeyStatus(provider) {
  const keyInput = document.getElementById("api-key-input");
  const status = document.getElementById("api-key-status");
  const has = !!(state.apiKeys[provider] || "").trim();
  keyInput.classList.toggle("has-key", has);
  status.textContent = has ? "✓ key saved in browser" : "";
}

function toggleKeyVisibility() {
  const inp = document.getElementById("api-key-input");
  inp.type = inp.type === "password" ? "text" : "password";
}

function toggleTool(id, on) {
  state.tools[id] = on;
  updateChatHeader();
}

function updateChatHeader() {
  const cfg = PROVIDERS[state.provider];
  document.getElementById("chat-provider-badge").textContent = `${state.provider} / ${state.model || cfg.models[0]}`;
  const active = Object.entries(state.tools).filter(([,v]) => v).map(([k]) => k);
  document.getElementById("chat-tools-active").innerHTML = active.map(t =>
    `<span class="tool-badge">${t}</span>`).join("") +
    (state.mcpServers.length ? `<span class="tool-badge">mcp(${state.mcpServers.length})</span>` : "") +
    (state.skills.length ? `<span class="tool-badge">skills(${state.skills.length})</span>` : "");
}

// ─── MCP ────────────────────────────────────────────
function addMcp() {
  const url = document.getElementById("mcp-url").value.trim();
  if (!url) return;
  const name = document.getElementById("mcp-name").value.trim() || new URL(url).hostname;
  const token = document.getElementById("mcp-token").value.trim();
  state.mcpServers.push({ url, name, token });
  document.getElementById("mcp-url").value = "";
  document.getElementById("mcp-name").value = "";
  document.getElementById("mcp-token").value = "";
  renderMcpList();
  updateChatHeader();
}

function removeMcp(i) {
  state.mcpServers.splice(i, 1);
  renderMcpList();
  updateChatHeader();
}

function renderMcpList() {
  document.getElementById("mcp-list").innerHTML = state.mcpServers.map((m, i) => `
    <div class="item-row">
      <span class="tool-icon">🔌</span>
      <div style="flex:1;min-width:0">
        <div class="item-name">${esc(m.name)}</div>
        <div class="item-url">${esc(m.url)}</div>
      </div>
      <button class="item-remove" onclick="removeMcp(${i})">×</button>
    </div>`).join("");
}

// ─── Skills ─────────────────────────────────────────
function loadSkill() {
  const url = document.getElementById("skill-url").value.trim();
  if (!url || !state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  document.getElementById("skill-url").value = "";
  state.ws.send(JSON.stringify({ type: "load_skill", url }));
  appendInfo(`Loading skill from ${url}…`);
}

function removeSkill(i) {
  state.skills.splice(i, 1);
  renderSkillList();
  updateChatHeader();
}

function renderSkillList() {
  document.getElementById("skills-list").innerHTML = state.skills.map((s, i) => `
    <div class="item-row">
      <span class="tool-icon">🧠</span>
      <div style="flex:1;min-width:0">
        <div class="item-name">${esc(s.name)}</div>
        <div class="item-url" title="${esc(s.description)}">${esc(s.description.slice(0,60))}…</div>
      </div>
      <button class="item-remove" onclick="removeSkill(${i})">×</button>
    </div>`).join("");
}

// ─── Capabilities ────────────────────────────────────
function toggleCapabilities() {
  document.getElementById("capabilities-panel").classList.toggle("hidden");
}

// ─── Clear session ───────────────────────────────────
function clearAll() {
  state.conversation = [];
  state.streamEl = null;
  state.streamText = "";
  document.getElementById("messages").innerHTML = "";
  appendInfo("Session cleared. Conversation history reset.");
}

// ─── Memory Drawer ───────────────────────────────────
async function openMemoryDrawer() {
  const overlay = document.getElementById("memory-overlay");
  const drawer = document.getElementById("memory-drawer");
  const body = document.getElementById("drawer-body");
  overlay.classList.remove("hidden");
  drawer.classList.remove("hidden");
  requestAnimationFrame(() => drawer.classList.add("open"));

  body.innerHTML = `<div class="loading-text">Loading memory files…</div>`;
  try {
    const data = await fetch("/api/info").then(r => r.json());
    renderMemoryDrawer(data, body);
  } catch (e) {
    body.innerHTML = `<div class="error-msg">Failed to load: ${esc(String(e))}</div>`;
  }
}

function closeMemoryDrawer() {
  const drawer = document.getElementById("memory-drawer");
  drawer.classList.remove("open");
  setTimeout(() => {
    drawer.classList.add("hidden");
    document.getElementById("memory-overlay").classList.add("hidden");
  }, 200);
}

function renderMemoryDrawer(data, body) {
  const { root, agents_md, memory_dir, workspace_dir, sessions_dir, memory_files, workspace_files, session_state } = data;

  const pathRow = (icon, label, path, desc, badge="") => `
    <div class="mem-path-row">
      <div class="mem-path-icon">${icon}</div>
      <div class="mem-path-info">
        <div class="mem-path-label">${label}</div>
        <div class="mem-path-value">${esc(path)}</div>
        ${desc ? `<div class="mem-path-desc">${desc}</div>` : ""}
      </div>
      ${badge ? `<span class="local-badge">${badge}</span>` : ""}
    </div>`;

  const cids = session_state?.container_ids || {};

  body.innerHTML = `
    <div class="mem-section">
      <div class="mem-section-title">File Locations — On This Machine</div>
      ${pathRow("📄","AGENTS.md (instructions)", agents_md, "Static agent instructions. Loaded as system prompt on every call.", "📍 local")}
      ${pathRow("🧠","memories/AGENTS.md (memory)", memory_dir + "/AGENTS.md", "Model writes durable preferences here via memory tool.", "📍 local")}
      ${pathRow("📁","memories/ (memory tool root)", memory_dir, "All memory tool files live here. Model reads/writes via view/create/str_replace.", "📍 local")}
      ${pathRow("🖥️","workspace/ (Gemini bash)", workspace_dir, "Harness-side bash filesystem for Gemini. Permanent local storage.", "📍 local")}
      ${pathRow("💾","sessions/ (conversation state)", sessions_dir, "Conversation history + container IDs saved here for cross-session resumption.", "📍 local")}
    </div>

    ${Object.keys(cids).length ? `
    <div class="mem-section">
      <div class="mem-section-title">Active Containers (Cross-session)</div>
      ${Object.entries(cids).map(([k,v]) => `
        <div class="container-row">
          <strong>${k}:</strong> <span class="container-id">${esc(v)}</span>
          ${k === "anthropic" ? "<br><span style='font-size:9px;color:var(--green)'>✅ Anthropic container — 30-day expiry, files persist</span>" : ""}
          ${k === "openai" ? "<br><span style='font-size:9px;color:var(--green)'>✅ OpenAI container — /mnt/data filesystem persists</span>" : ""}
          ${k === "openai_response_id" ? "<br><span style='font-size:9px;color:var(--green)'>✅ OpenAI response chain — conversation history linked</span>" : ""}
        </div>`).join("")}
    </div>` : ""}

    ${memory_files.length ? `
    <div class="mem-section">
      <div class="mem-section-title">Memory Files (${memory_files.length})</div>
      ${memory_files.map(f => `
        <div class="mem-file-row">
          <div style="flex:1;min-width:0">
            <div class="mem-file-name">${esc(f.path)}</div>
            <div class="mem-file-size">${f.size} bytes</div>
            ${f.content_preview ? `<div class="mem-file-preview">${esc(f.content_preview)}</div>` : ""}
          </div>
        </div>`).join("")}
    </div>` : `<div class="mem-section"><div class="mem-section-title">Memory Files</div><div style="font-size:11px;color:var(--text-dim)">No memory files yet. Use the memory tool to write memories.</div></div>`}

    ${workspace_files.length ? `
    <div class="mem-section">
      <div class="mem-section-title">Workspace Files / Gemini (${workspace_files.length})</div>
      ${workspace_files.map(f => `
        <div class="container-row">
          ${esc(f.path)} <span style="color:var(--text-dim);">(${f.size}B)</span>
        </div>`).join("")}
    </div>` : ""}
  `;
}

// ─── Send Message ─────────────────────────────────────
function handleKey(e) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function sendMessage() {
  if (state.busy) return;
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text) return;

  // Remove welcome
  const welcome = document.querySelector(".welcome-block");
  if (welcome) welcome.remove();

  input.value = "";
  appendUserMsg(text);
  state.conversation.push({ role: "user", content: text });

  state.busy = true;
  document.getElementById("send-btn").disabled = true;

  state.streamText = "";
  state.streamEl = null;  // Created lazily on first token — so tool calls appear above it

  scrollBottom(true);  // Scroll to show the user's message

  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    appendError("WebSocket not connected.");
    finishBusy(); return;
  }

  state.ws.send(JSON.stringify({
    type: "chat",
    provider: state.provider,
    model: state.model,
    conversation: state.conversation,
    tools: state.tools,
    mcp_servers: state.mcpServers,
    skills: state.skills,
    api_key: (state.apiKeys[state.provider] || "").trim() || null,
  }));
}

// ─── Event Handler ────────────────────────────────────
function handleEvent(evt) {
  switch (evt.type) {
    case "api_request":
      addInspEntry("request", evt.payload, `${state.provider} → ${state.model}`);
      break;
    case "api_response":
      addInspEntry("response", evt.payload, "API Response");
      break;
    case "token":
      // Create the assistant bubble on the FIRST token — after any tool call
      // divs that arrived earlier, so tool calls appear above the answer.
      if (!state.streamEl) {
        state.streamEl = appendAssistantBubble(true);
      }
      state.streamText += evt.content;
      {
        const bubble = state.streamEl.querySelector(".msg-bubble");
        const cur = bubble.querySelector(".cursor");
        if (cur) bubble.removeChild(cur);
        bubble.textContent = state.streamText;
        bubble.appendChild(mkCursor());
        scrollBottom();  // Gentle: only scrolls if user is near bottom
      }
      break;
    case "tool_call":
      // Tool call with early detection (input may be empty {}; final input comes via "tool" event)
      appendToolEvent("call", evt.tool, evt.input);
      addInspEntry("tool", { tool: evt.tool, input: evt.input }, `Tool: ${evt.tool}`);
      scrollBottom();
      break;
    case "tool":
      // Complete tool input (after streaming ends) — update inspector only, not chat card
      addInspEntry("tool", { tool: evt.tool, input: evt.input }, `Tool: ${evt.tool}`);
      break;
    case "tool_result":
      appendToolEvent("result", evt.tool, evt.output);
      scrollBottom();
      break;
    case "image": {
      // Image generated (e.g. OpenAI image_generation tool) — display inline
      if (!state.streamEl) state.streamEl = appendAssistantBubble(false);
      const bubble = state.streamEl.querySelector(".msg-bubble");
      const cur = bubble.querySelector(".cursor");
      if (cur) bubble.removeChild(cur);
      const img = document.createElement("img");
      img.src = `data:image/png;base64,${evt.data}`;
      img.style.cssText = "max-width:100%;border-radius:4px;margin-top:8px;display:block;";
      img.alt = "Generated image";
      bubble.appendChild(img);
      scrollBottom(true);
      break;
    }
    case "info":
      appendInfo(evt.message);
      break;
    case "skill_loaded":
      state.skills.push(evt.skill);
      renderSkillList();
      updateChatHeader();
      appendInfo(`Skill loaded: "${evt.skill.name}" — ${evt.skill.description}`);
      break;
    case "done": {
      const final = evt.message || state.streamText;
      if (!state.streamEl && final) {
        // No tokens streamed (e.g. non-streaming path emitted done without tokens)
        state.streamEl = appendAssistantBubble(false);
      }
      if (state.streamEl) {
        const bubble = state.streamEl.querySelector(".msg-bubble");
        const cur = bubble.querySelector(".cursor");
        if (cur) bubble.removeChild(cur);
        bubble.textContent = final;
        state.streamEl = null;
      }
      state.conversation.push({ role: "assistant", content: final });
      finishBusy();
      scrollBottom();
      break;
    }
    case "error":
      if (state.streamEl) { state.streamEl.remove(); state.streamEl = null; }
      appendError(evt.message);
      addInspEntry("error", { message: evt.message }, "Error");
      finishBusy();
      break;
  }
}

function finishBusy() {
  state.busy = false;
  document.getElementById("send-btn").disabled = false;
}

// ─── Chat DOM Helpers ─────────────────────────────────
function appendUserMsg(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="msg-meta">you</div><div class="msg-bubble">${esc(text)}</div>`;
  document.getElementById("messages").appendChild(el);
  scrollBottom();
}

function appendAssistantBubble(streaming) {
  const cfg = PROVIDERS[state.provider];
  const el = document.createElement("div");
  el.className = "msg assistant";
  const modelLabel = (state.model || cfg.models[0]).split("-").slice(0,2).join("-");
  el.innerHTML = `<div class="msg-meta">${state.provider} / ${modelLabel}</div><div class="msg-bubble">${streaming ? "" : ""}</div>`;
  if (streaming) el.querySelector(".msg-bubble").appendChild(mkCursor());
  document.getElementById("messages").appendChild(el);
  scrollBottom();
  return el;
}

// ── Tool event helpers ────────────────────────────────

const TOOL_ICONS = {
  web_search:"🔍", web_search_preview:"🔍",
  code_execution:"💻", code_interpreter:"💻", bash_code_execution:"💻",
  bash:"🖥️", shell:"🖥️",
  web_fetch:"🌐", url_context:"🌐", read_url:"🌐",
  memory:"🧠",
  mcp:"🔌", ask_question:"🔌", read_wiki:"🔌",
  google_search:"🔍",
};

function toolIcon(name) {
  const key = Object.keys(TOOL_ICONS).find(k => name.toLowerCase().includes(k));
  return key ? TOOL_ICONS[key] : "🛠️";
}

// Extract the most meaningful single-line preview from tool input
function toolPreview(name, data) {
  if (!data || data === "...") return "";
  if (typeof data === "string") return data.slice(0, 100);
  const d = data;
  // Most useful field first, by tool type
  if (d.query)   return `"${String(d.query).slice(0,100)}"`;
  if (d.url)     return String(d.url).replace(/^https?:\/\//, "").slice(0, 100);
  if (d.command && d.path) return `${d.command}  ${d.path}`;
  if (d.command) return d.command;
  if (d.code)    return String(d.code).split("\n")[0].slice(0, 100);
  if (d.input)   return String(d.input).slice(0, 100);
  if (d.question) return `"${String(d.question).slice(0,100)}"`;
  if (d.expression) return d.expression;
  // Fallback: first non-empty string value
  const first = Object.values(d).find(v => v && typeof v === "string");
  return first ? first.slice(0, 100) : "";
}

// Pretty-print tool input for expanded view
function toolDetail(data) {
  if (!data || data === "...") return "";
  try { return JSON.stringify(data, null, 2); } catch { return String(data); }
}

let toolCardSeq = 0;

function appendToolEvent(kind, toolName, data) {
  const msgs = document.getElementById("messages");

  if (kind === "call") {
    const id = `tc${++toolCardSeq}`;
    const icon = toolIcon(toolName);
    const preview = toolPreview(toolName, data);
    const detail  = toolDetail(data);

    const el = document.createElement("div");
    el.className = "tool-card";
    el.dataset.tool = toolName;
    el.dataset.id = id;
    el.innerHTML = `
      <div class="tc-row" onclick="tcToggle('${id}')">
        <span class="tc-icon">${icon}</span>
        <span class="tc-name">${esc(toolName)}</span>
        ${preview ? `<span class="tc-preview">${esc(preview)}</span>` : ""}
        <span class="tc-chevron">▸</span>
      </div>
      <div class="tc-detail" id="${id}">
        <pre class="tc-pre">${esc(detail)}</pre>
      </div>`;
    msgs.appendChild(el);

  } else {
    // Result: attach to the most recent matching call card, or append standalone
    const preview = typeof data === "string"
      ? data.slice(0, 120)
      : (data?.output ?? data?.content ?? JSON.stringify(data)).toString().slice(0, 120);

    // Find last call card with matching tool name
    const cards = msgs.querySelectorAll(`.tool-card[data-tool="${CSS.escape(toolName)}"]`);
    const card = cards[cards.length - 1];
    if (card) {
      const res = document.createElement("div");
      res.className = "tc-result";
      res.textContent = preview;
      card.appendChild(res);
    } else {
      const el = document.createElement("div");
      el.className = "tool-card";
      el.innerHTML = `<div class="tc-row tc-row-result"><span class="tc-icon">✓</span><span class="tc-name">${esc(toolName)}</span><span class="tc-preview">${esc(preview)}</span></div>`;
      msgs.appendChild(el);
    }
  }
  scrollBottom();
}

function tcToggle(id) {
  const detail = document.getElementById(id);
  if (!detail) return;
  const open = detail.style.display === "block";
  detail.style.display = open ? "none" : "block";
  const row = detail.previousElementSibling;
  if (row) {
    const ch = row.querySelector(".tc-chevron");
    if (ch) ch.textContent = open ? "▸" : "▾";
  }
}

function appendInfo(msg) {
  const el = document.createElement("div");
  el.className = "info-msg";
  el.textContent = msg;
  document.getElementById("messages").appendChild(el);
  scrollBottom();
}

function appendError(msg) {
  const el = document.createElement("div");
  el.className = "error-msg";
  el.textContent = `Error: ${msg}`;
  document.getElementById("messages").appendChild(el);
  scrollBottom();
}

function mkCursor() {
  const s = document.createElement("span");
  s.className = "cursor";
  return s;
}

function scrollBottom(force = false) {
  const m = document.getElementById("messages");
  const nearBottom = m.scrollHeight - m.scrollTop - m.clientHeight < 120;
  if (force || nearBottom) {
    m.scrollTop = m.scrollHeight;
  }
}

// ─── Inspector ────────────────────────────────────────
let inspCount = 0;

function addInspEntry(kind, payload, label) {
  const empty = document.querySelector(".inspector-empty");
  if (empty) empty.remove();

  const id = `insp-${++inspCount}`;
  const rawId = `raw-${id}`;
  const ts = new Date().toLocaleTimeString();

  const block = document.createElement("div");
  block.className = "insp-block";
  block.id = id;

  let summary = "";
  if (kind === "request")  summary = buildRequestSummary(payload);
  if (kind === "response") summary = buildResponseSummary(payload);
  if (kind === "tool")     summary = buildToolSummary(payload);
  if (kind === "error")    summary = `<div class="insp-err">${esc(payload?.message || String(payload))}</div>`;

  const dirIcon = kind === "request" ? "📤" : kind === "response" ? "📥" : kind === "error" ? "⚠️" : "🔧";
  const dirLabel = kind === "request" ? `Sent to ${state.provider}`
                 : kind === "response" ? `Received from ${state.provider}`
                 : kind === "error" ? "Error"
                 : `Tool · ${payload?.tool || label}`;

  block.innerHTML = `
    <div class="insp-header" onclick="toggleInsp('${id}')">
      <span class="insp-dir">${dirIcon} ${dirLabel}</span>
      <span class="insp-ts">${ts}</span>
      <span class="insp-toggle">▾</span>
    </div>
    <div class="insp-body">
      ${summary}
      <div class="insp-raw-toggle" onclick="toggleRaw('${rawId}')">Raw JSON ▸</div>
      <div class="insp-raw" id="${rawId}">
        <div class="json-viewer">${highlight(payload)}</div>
      </div>
    </div>`;

  if (inspCount <= 2) block.classList.add("open");

  const body = document.getElementById("inspector-turns");
  body.appendChild(block);
  body.scrollTop = body.scrollHeight;
}

function toggleInsp(id) {
  document.getElementById(id).classList.toggle("open");
}

function toggleRaw(id) {
  const el = document.getElementById(id);
  const toggle = el.previousElementSibling;
  const open = el.style.display === "block";
  el.style.display = open ? "none" : "block";
  toggle.textContent = open ? "Raw JSON ▸" : "Raw JSON ▾";
}

function clearInspector() {
  document.getElementById("inspector-turns").innerHTML =
    `<div class="inspector-empty">Each API call will be explained here — what was sent, what came back.</div>`;
  inspCount = 0;
}

// ── Request summary ───────────────────────────────────
function buildRequestSummary(p) {
  if (!p) return "";
  const rows = [];

  // Model
  if (p.model) rows.push(inRow("Model", `<strong>${esc(p.model)}</strong>`));

  // System prompt (AGENTS.md)
  const sys = p.system || p.instructions || (p.input?.find?.(m => m.role === "system")?.content);
  if (sys) {
    const preview = String(sys).slice(0, 120).replace(/\n/g, " ");
    rows.push(inRow("System prompt",
      `<span class="insp-dim">From AGENTS.md — defines the agent's role and rules</span>
       <div class="insp-preview">"${esc(preview)}${sys.length > 120 ? "…" : ""}"</div>`));
  }

  // Conversation
  const msgs = p.messages || p.input?.filter?.(m => m.role !== "system");
  if (msgs?.length) {
    const last = msgs[msgs.length - 1];
    const lastContent = typeof last?.content === "string"
      ? last.content
      : last?.content?.[0]?.text || last?.content?.[0]?.content || "";
    rows.push(inRow("Conversation",
      `<span class="insp-dim">${msgs.length} message${msgs.length > 1 ? "s" : ""} — the full chat history is sent every time</span>
       ${lastContent ? `<div class="insp-preview">"${esc(String(lastContent).slice(0, 100))}${String(lastContent).length > 100 ? "…" : ""}"</div>` : ""}`));
  }

  // Tools
  const tools = p.tools;
  if (tools?.length) {
    const toolList = tools.map(t => {
      const name = t.name || t.type || "tool";
      const isServer = ["web_search_20260209","web_fetch_20260209","code_execution_20250825",
                        "web_search_preview","image_generation","code_interpreter",
                        "google_search","code_execution","url_context","google_maps",
                        "advisor_20260301","mcp_toolset","mcp","shell"].some(k => String(t.type||"").includes(k) || name.includes(k));
      const tag = isServer ? `<span class="tag-api-s">API executes</span>` : `<span class="tag-harness-s">harness executes</span>`;
      const icon = toolIcon(name);
      return `<span class="insp-tool">${icon} ${esc(name)} ${tag}</span>`;
    });
    rows.push(inRow("Tools enabled", toolList.join(" ")));
  }

  // MCP servers
  const mcps = p.mcp_servers;
  if (mcps?.length) {
    rows.push(inRow("MCP servers",
      mcps.map(m => `<span class="insp-tool">🔌 ${esc(m.name)} <span class="insp-dim">${esc(m.url)}</span>
        <span class="tag-api-s">API calls this server</span></span>`).join(" ") +
      `<div class="insp-dim" style="margin-top:4px">These URLs are sent to ${state.provider}, which calls them on your behalf — your harness makes no request to them.</div>`));
  }

  // Skills (container.skills or shell.environment.skills)
  const skillList = p.container?.skills || tools?.flatMap?.(t => t.environment?.skills || []);
  if (skillList?.length) {
    rows.push(inRow("Skills",
      skillList.map(s => {
        const sid = s.skill_id || "(id)";
        const stype = s.type === "anthropic" ? "Anthropic built-in" : "your custom skill";
        return `<span class="insp-tool">🧠 ${esc(sid.slice(0,20))}… <span class="insp-dim">(${stype})</span></span>`;
      }).join(" ") +
      `<div class="insp-dim" style="margin-top:4px">Only the skill ID is sent — the SKILL.md content stays on ${state.provider}'s servers, loaded on demand.</div>`));
  }

  // Container (reusing)
  if (p.container?.id || typeof p.container === "string") {
    const cid = p.container?.id || p.container;
    rows.push(inRow("Container",
      `<span class="insp-tool">📦 ${esc(String(cid).slice(0,24))}…</span>
       <div class="insp-dim" style="margin-top:4px">Reusing a saved workspace — any files you created previously are still there (30-day window).</div>`));
  }

  // Beta headers
  if (p.betas?.length) {
    rows.push(inRow("Beta features", p.betas.map(b => `<code>${esc(b)}</code>`).join(" ")));
  }

  return `<div class="insp-summary">${rows.join("")}</div>`;
}

// ── Response summary ──────────────────────────────────
function buildResponseSummary(p) {
  if (!p) return "";
  const rows = [];

  // Stop reason
  const stop = p.stop_reason || p.status || (p.candidates?.[0]?.finish_reason);
  if (stop) {
    const labels = {
      end_turn:    "✅ Finished — AI completed its response",
      tool_use:    "🔧 Paused to use a tool — will continue after",
      pause_turn:  "⏸️ Long task paused — server will continue automatically",
      max_tokens:  "📏 Hit token limit",
      completed:   "✅ Finished",
      STOP:        "✅ Finished",
    };
    rows.push(inRow("Status", labels[stop] || `<code>${esc(String(stop))}</code>`));
  }

  // Token usage
  const usage = p.usage || p.usage_metadata;
  if (usage) {
    const inp = usage.input_tokens || usage.prompt_token_count || 0;
    const out = usage.output_tokens || usage.candidates_token_count || 0;
    rows.push(inRow("Tokens used",
      `<strong>${inp.toLocaleString()}</strong> sent → <strong>${out.toLocaleString()}</strong> received
       <span class="insp-dim"> (tokens ≈ words; you pay per token)</span>`));
  }

  // Container ID returned
  const ctr = p.container;
  if (ctr?.id) {
    rows.push(inRow("Container saved",
      `<span class="insp-tool">📦 ${esc(ctr.id.slice(0,24))}…</span>
       <div class="insp-dim" style="margin-top:4px">This ID is saved locally. Next call will pass it back so the AI finds its files.</div>`));
  }

  // Content blocks summary
  const content = p.content || p.output;
  if (content?.length) {
    const types = content.map(b => b.type || b.role || "?");
    const summary = [...new Set(types)].map(t => {
      const friendly = {
        text: "Text reply", message: "Text reply",
        tool_use: "Tool call", server_tool_use: "Tool call (server)",
        web_search_tool_result: "Search result", code_execution_tool_result: "Code output",
        bash_code_execution_tool_result: "Bash output", web_fetch_tool_result: "Fetched URL",
        image_generation_call: "Generated image",
        mcp_tool_use: "MCP tool call", mcp_tool_result: "MCP result",
        advisor_tool_result: "Advisor guidance",
      };
      return friendly[t] || t;
    });
    rows.push(inRow("Response contains", summary.join(" · ")));
  }

  return `<div class="insp-summary">${rows.join("")}</div>`;
}

// ── Tool event summary ────────────────────────────────
function buildToolSummary(p) {
  if (!p) return "";
  const tool = p.tool || "";
  const input = p.input;
  const preview = toolPreview(tool, input);
  return `<div class="insp-summary">
    ${inRow("Tool", `<span class="insp-tool">${toolIcon(tool)} <strong>${esc(tool)}</strong></span>`)}
    ${preview ? inRow("Input", `<span class="insp-preview">${esc(preview)}</span>`) : ""}
  </div>`;
}

function inRow(label, value) {
  return `<div class="insp-row"><div class="insp-row-label">${label}</div><div class="insp-row-value">${value}</div></div>`;
}

// ─── JSON Highlight ───────────────────────────────────
function highlight(obj) {
  let s;
  try { s = JSON.stringify(obj, null, 2); } catch { s = String(obj); }
  s = s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  return s.replace(
    /("(\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    m => {
      if (/^"/.test(m)) return /:$/.test(m) ? `<span class="j-key">${m}</span>` : `<span class="j-str">${m}</span>`;
      if (/true|false/.test(m)) return `<span class="j-bool">${m}</span>`;
      if (/null/.test(m)) return `<span class="j-null">${m}</span>`;
      return `<span class="j-num">${m}</span>`;
    }
  );
}

function esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ─── Resizable Panels ────────────────────────────────
// Drag the handle divs to resize sidebar or inspector.
// Double-click a handle to collapse / restore that panel.
// Sizes persist in localStorage.

const MIN_W = 160;

function loadSizes() {
  const s = JSON.parse(localStorage.getItem("panels") || "{}");
  return { left: s.left||280, right: s.right||380,
           lc: s.lc||false, rc: s.rc||false };
}
function storeSizes(s) {
  localStorage.setItem("panels", JSON.stringify(s));
}
function applySizes(s) {
  const sidebar   = document.querySelector(".sidebar");
  const inspector = document.querySelector(".inspector");
  sidebar.style.width   = (s.lc ? 0 : s.left)  + "px";
  inspector.style.width = (s.rc ? 0 : s.right) + "px";
  // Keep content from peeking out when collapsed
  sidebar.style.minWidth   = "0";
  inspector.style.minWidth = "0";
}

function initResize() {
  applySizes(loadSizes());
  makeResizable("rh-left",  "left");
  makeResizable("rh-right", "right");
}

function makeResizable(id, side) {
  const handle = document.getElementById(id);
  if (!handle) return;

  handle.addEventListener("mousedown", e => {
    if (e.button !== 0) return;
    const s = loadSizes();
    if (side === "left" && s.lc) return;
    if (side === "right" && s.rc) return;
    e.preventDefault();
    handle.classList.add("dragging");
    const x0     = e.clientX;
    const size0  = side === "left" ? s.left : s.right;

    const onMove = mv => {
      const delta  = side === "left" ? mv.clientX - x0 : x0 - mv.clientX;
      const fresh  = loadSizes();
      fresh[side]  = Math.max(MIN_W, size0 + delta);
      storeSizes(fresh);
      applySizes(fresh);
    };
    const onUp = () => {
      handle.classList.remove("dragging");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup",   onUp);
  });

  handle.addEventListener("dblclick", () => {
    const s = loadSizes();
    if (side === "left")  s.lc = !s.lc;
    else                  s.rc = !s.rc;
    storeSizes(s);
    applySizes(s);
  });
}

document.addEventListener("DOMContentLoaded", initResize);

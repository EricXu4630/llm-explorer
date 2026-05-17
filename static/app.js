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
};

// ─── Provider Config ─────────────────────────────────
const PROVIDERS = {
  anthropic: {
    models: ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"],
    tools: [
      { id:"web_search",    label:"Web Search",     icon:"🔍", note:"native", cls:"native" },
      { id:"code_execution",label:"Code Execution", icon:"💻", note:"native (30d container)", cls:"native" },
      { id:"web_fetch",     label:"Web Fetch",      icon:"🌐", note:"native", cls:"native" },
      { id:"memory",        label:"Memory Tool",    icon:"🧠", note:"harness /memories/", cls:"harness" },
    ],
    mcpNote: "✅ Native MCP — Anthropic calls MCP servers server-side (public HTTPS required)",
    defaultTools: ["web_search"],
  },
  openai: {
    models: ["gpt-4o", "gpt-4o-mini", "o3", "gpt-4.1"],
    tools: [
      { id:"web_search",      label:"Web Search",      icon:"🔍", note:"native", cls:"native" },
      { id:"code_interpreter",label:"Code Interpreter",icon:"💻", note:"native (hosted)", cls:"native" },
      { id:"shell",           label:"Shell /mnt/data", icon:"🖥️", note:"native (bash container)", cls:"native" },
    ],
    mcpNote: "✅ Native MCP — OpenAI calls MCP servers server-side (public HTTPS required)",
    defaultTools: ["web_search"],
  },
  gemini: {
    models: ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
    tools: [
      { id:"google_search", label:"Google Search",  icon:"🔍", note:"native (grounded)", cls:"native" },
      { id:"code_execution",label:"Code Execution", icon:"💻", note:"native (stateless)", cls:"native" },
      { id:"url_context",   label:"URL Context",    icon:"🌐", note:"native", cls:"native" },
      { id:"bash",          label:"Bash",           icon:"🖥️", note:"harness workspace/", cls:"harness" },
    ],
    mcpNote: "❌ No native MCP — Gemini has no API-level MCP connector. MCP servers are ignored.",
    defaultTools: ["google_search"],
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
  cfg.tools.forEach(t => { state.tools[t.id] = cfg.defaultTools.includes(t.id); });
  document.getElementById("tools-list").innerHTML = cfg.tools.map(t => `
    <div class="tool-row">
      <label>
        <input type="checkbox" ${state.tools[t.id] ? "checked" : ""} onchange="toggleTool('${t.id}',this.checked)"/>
        <span class="tool-icon">${t.icon}</span>
        <span class="tool-name">${t.label}</span>
      </label>
      <span class="tool-note ${t.cls}">${t.note}</span>
    </div>`).join("");

  document.getElementById("mcp-note").textContent = cfg.mcpNote;
  updateChatHeader();
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
  state.streamEl = appendAssistantBubble(true);

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
      if (state.streamEl) {
        state.streamText += evt.content;
        const bubble = state.streamEl.querySelector(".msg-bubble");
        const cur = bubble.querySelector(".cursor");
        if (cur) bubble.removeChild(cur);
        bubble.textContent = state.streamText;
        bubble.appendChild(mkCursor());
        scrollBottom();
      }
      break;
    case "tool_call":
      appendToolEvent("call", evt.tool, evt.input);
      addInspEntry("tool", { tool: evt.tool, input: evt.input }, `Tool: ${evt.tool}`);
      break;
    case "tool_result":
      appendToolEvent("result", evt.tool, evt.output);
      break;
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

function appendToolEvent(kind, toolName, data) {
  const icons = {
    web_search:"🔍", web_search_preview:"🔍", web_search_call:"🔍",
    code_execution:"💻", code_interpreter:"💻", bash_code_execution:"💻",
    bash:"🖥️", shell:"🖥️",
    web_fetch:"🌐", url_context:"🌐",
    memory:"🧠", mcp:"🔌",
    google_search:"🔍",
  };
  const icon = Object.entries(icons).find(([k]) => toolName.includes(k))?.[1] || "🛠️";
  const dataStr = typeof data === "object" ? JSON.stringify(data).slice(0,120) : String(data||"").slice(0,120);
  const el = document.createElement("div");
  el.className = "tool-event";
  if (kind === "call") {
    el.innerHTML = `<div class="tool-icon-lg">${icon}</div><div class="tool-body"><div class="tool-label">${esc(toolName)}</div><div class="tool-sub">${esc(dataStr)}</div></div>`;
  } else {
    el.innerHTML = `<div class="tool-icon-lg">✓</div><div class="tool-body"><div class="tool-label result">${esc(toolName)} result</div><div class="tool-sub">${esc(dataStr)}</div></div>`;
  }
  document.getElementById("messages").appendChild(el);
  scrollBottom();
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

function scrollBottom() {
  const m = document.getElementById("messages");
  m.scrollTop = m.scrollHeight;
}

// ─── Inspector ────────────────────────────────────────
let inspCount = 0;
function addInspEntry(kind, payload, label) {
  const empty = document.querySelector(".inspector-empty");
  if (empty) empty.remove();

  const id = `insp-${++inspCount}`;
  const ts = new Date().toLocaleTimeString();
  const block = document.createElement("div");
  block.className = "insp-block";
  block.id = id;
  // Auto-open first 2 entries
  if (inspCount <= 2) block.classList.add("open");

  block.innerHTML = `
    <div class="insp-header" onclick="toggleInsp('${id}')">
      <span class="insp-tag ${kind}">${kind.toUpperCase()}</span>
      <span class="insp-meta">${esc(label)} · ${ts}</span>
      <span class="insp-toggle">▾</span>
    </div>
    <div class="insp-body">
      <div class="json-viewer">${highlight(payload)}</div>
    </div>`;

  const body = document.getElementById("inspector-turns");
  body.appendChild(block);
  body.scrollTop = body.scrollHeight;
}

function toggleInsp(id) {
  document.getElementById(id).classList.toggle("open");
}

function clearInspector() {
  document.getElementById("inspector-turns").innerHTML =
    `<div class="inspector-empty">API payloads appear here as you chat.</div>`;
  inspCount = 0;
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

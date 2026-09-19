// knowme dashboard — model picker/catalog/pins, and the remaining Settings toggle.
// Split out of app.js: classic <script>, shared global scope (no build
// step, no modules). Load order + rules: static/README.md.

async function saveSettings(){
  const experimental = document.getElementById("set-experimental")?.value;
  const graph_workflows = document.getElementById("set-graph-workflows")?.value;
  document.getElementById("set-msg").textContent = "正在切换…";
  const r = await postJSON("/api/settings", {experimental, graph_workflows});
  document.getElementById("set-msg").textContent = r.error ? ("错误："+r.error) : "已保存。";
}
function markEditing(){ editing = true; }

// Model picker: fill the settings datalist from /api/models (the active
// endpoint's live catalog; on OpenRouter each entry says free / tool support).
// KnowMe's loop needs tool calling, so tool-less models are labelled as such.
let modelCatalog = null;
async function loadModelList(){
  const dl = document.getElementById("model-list");
  if (!dl) return;
  if (modelCatalog === null){
    try { modelCatalog = await (await fetch("/api/models")).json(); }
    catch(e){ modelCatalog = {models:[], listed:false}; }
  }
  const ms = modelCatalog.models || [];
  dl.innerHTML = ms.map(m => {
    const price = m.free ? "免费" : (m.price_out != null ? `$${m.price_in}/$${m.price_out} / 百万 Token` : "");
    const tags = [price, m.tools === false ? "仅聊天" : "", m.reasoning ? "推理模型" : "",
                  m.context ? Math.round(m.context/1000) + "k 上下文" : ""].filter(Boolean).join(" · ");
    return `<option value="${esc(m.id)}">${esc(tags)}</option>`;
  }).join("");
  const msg = document.getElementById("model-list-msg");
  if (!msg) return;
  if (modelCatalog.listed){
    const free = ms.filter(m=>m.free), freeTools = free.filter(m=>m.tools);
    msg.textContent = `${modelCatalog.endpoint} 上有 ${ms.length} 个模型` +
      (free.length ? ` · ${free.length} 个免费，其中 ${freeTools.length} 个支持工具调用（KnowMe 需要工具调用）` : "") +
      ` · 在上方字段输入即可搜索`;
  } else {
    msg.textContent = modelCatalog.error ? `模型列表不可用：${modelCatalog.error}` : "";
  }
  renderCatalog();
}

// The catalog browser (shown when the endpoint lists models, i.e. OpenRouter):
// suggested picks per SLOT, a search + free/tools filter, and the full list
// grouped by vendor. Every row can go to either slot: "use" is the loop model
// (needs tool calling), "gate" is the small model (needs terse JSON, so
// reasoning models are steered away from it).
let catFilter = {q: "", free: false, tools: false};

function modelRow(m, st){
  const cur = m.id === st.model, curGate = m.id === st.small_model;
  const isPinned = (st.pinned || []).some(p => p.provider === st.provider && p.model === m.id);
  const price = m.free ? "免费" : (m.price_out != null ? `$${m.price_in}/$${m.price_out} / 百万 Token` : "");
  const tags = [price, m.context ? Math.round(m.context/1000) + "k 上下文" : ""]
               .filter(Boolean).join(" · ");
  return `<div class="tool" style="display:flex;align-items:center;gap:8px;padding:6px 8px">
    <a class="pinstar ${isPinned?"on":""}" title="${isPinned?"已固定到‘你的模型’——点击移除":"固定到‘你的模型’（会显示在聊天模型切换器中）"}"
       onclick="pinModel('${esc(st.provider)}','${esc(m.id)}','${isPinned?"unpin":"pin"}')">${isPinned?"★":"☆"}</a>
    <code style="flex:1;word-break:break-all">${esc(m.id)}</code>
    <span class="meta" style="margin:0;white-space:nowrap">${esc(tags)}</span>
    ${m.reasoning ? `<span class="srcpill apple" title="回答前会进行推理：适合主循环，但不适合 Token 预算很小的门控">推理</span>` : ""}
    ${curGate ? `<span class="srcpill">门控</span>`
              : `<a class="reveal" data-id="${esc(m.id)}" onclick="switchModel(this.dataset.id,true)" title="用作门控/摘要模型">设为门控</a>`}
    ${cur ? `<span class="srcpill" style="background:var(--good-soft);color:var(--good)">当前</span>`
          : (m.tools === false ? `<span class="meta" style="margin:0" title="循环需要工具调用能力">仅聊天</span>`
                               : `<button class="save" data-id="${esc(m.id)}" onclick="switchModel(this.dataset.id)">使用</button>`)}
  </div>`;
}

// Slot suggestions are transparent heuristics over catalog metadata (tools,
// price, context, reasoning), NOT a quality leaderboard. Loop: tool-capable,
// free first, then biggest context. Gate: cheap non-reasoning instruct-style.
const GATE_HINT = /instruct|gemma|haiku|flash|mini|nano|lite|small/;
function loopPicks(ms){
  return ms.filter(m => m.tools)
           .sort((a,b) => (b.free - a.free) || ((b.context||0) - (a.context||0))).slice(0, 4);
}
function gatePicks(ms){
  return ms.filter(m => m.tools !== false && m.reasoning !== true
                        && (m.free || (m.price_out != null && m.price_out <= 1.5)))
           .sort((a,b) => (GATE_HINT.test(b.id) - GATE_HINT.test(a.id))
                        || (b.free - a.free) || ((a.price_out||99) - (b.price_out||99))).slice(0, 4);
}

function renderCatalog(){
  const box = document.getElementById("catalog");
  if (!box || !modelCatalog) return;
  const all = modelCatalog.models || [];
  const head = document.getElementById("catalog-h");
  if (!modelCatalog.listed || !all.length){
    box.style.display = "none"; if (head) head.style.display = "none"; return;
  }
  box.style.display = ""; if (head) head.style.display = "";
  box.innerHTML = `
    <div class="cat-controls">
      <input id="cat-q" type="text" placeholder="筛选模型…" value="${esc(catFilter.q)}"
        onfocus="markEditing()" oninput="catFilter.q=this.value;renderCatalogList()">
      <label class="meta" style="margin:0"><input type="checkbox" id="cat-free" ${catFilter.free?"checked":""}
        onchange="catFilter.free=this.checked;renderCatalogList()"> 仅免费模型</label>
      <label class="meta" style="margin:0"><input type="checkbox" id="cat-tools" ${catFilter.tools?"checked":""}
        onchange="catFilter.tools=this.checked;renderCatalogList()"> 仅支持工具调用</label>
    </div>
    <div id="cat-list"></div>
    <div class="meta" id="free-switch-msg" style="margin-top:6px"></div>`;
  renderCatalogList();
}

function renderCatalogList(){
  const list = document.getElementById("cat-list");
  if (!list || !modelCatalog) return;
  const st = (D && D.settings) || {};
  const all = modelCatalog.models || [];
  const q = catFilter.q.trim().toLowerCase();
  const shown = all.filter(m => (!q || m.id.toLowerCase().includes(q))
                             && (!catFilter.free || m.free)
                             && (!catFilter.tools || m.tools));
  let h = "";
  if (!q && !catFilter.free && !catFilter.tools){
    h += `<div class="meta" style="margin:4px 0">推荐模型：根据目录元数据（工具、价格、上下文）使用透明规则筛选，不代表质量排名</div>`;
    h += `<div class="meta" style="margin:6px 0 2px"><b>用于主循环</b>（需要工具调用；免费优先、上下文越大越优先）</div>`;
    h += loopPicks(all).map(m => modelRow(m, st)).join("");
    h += `<div class="meta" style="margin:10px 0 2px"><b>用于门控</b>（低成本、回答简短、非推理模型）</div>`;
    h += gatePicks(all).map(m => modelRow(m, st)).join("");
    h += `<div class="meta" style="margin:12px 0 2px"><b>全部模型</b>（${all.length} 个，按厂商分组）</div>`;
  } else {
    h += `<div class="meta" style="margin:4px 0">显示 ${shown.length}/${all.length} 个模型</div>`;
  }
  const vendors = {};
  shown.forEach(m => (vendors[m.id.split("/")[0]] ??= []).push(m));
  const expand = q || catFilter.free || catFilter.tools;
  h += Object.keys(vendors).sort().map(v => `
    <details ${expand ? "open" : ""}><summary><code>${esc(v)}</code>
      <span class="meta" style="margin-left:6px">${vendors[v].length}${vendors[v].some(m=>m.free) ? " · 包含免费模型" : ""}</span></summary>
      ${vendors[v].map(m => modelRow(m, st)).join("")}
    </details>`).join("");
  list.innerHTML = h;
}

// One-click model switch: posts to /api/providers so the provider/model pair
// is validated and applied by the integrations layer. Keeps the other slot
// (main vs gate) as-is. Live for the next turn.
async function switchModel(id, asGate){
  const st = (D && D.settings) || {};
  const msg = document.getElementById("free-switch-msg");
  if (msg) msg.textContent = "正在切换…";
  const payload = {provider: st.provider,
    model: asGate ? st.model : id, small_model: asGate ? id : st.small_model};
  const r = await postJSON("/api/providers", payload);
  if (!r.error){ editing = false; modelCatalog = null; await refresh(); }
  if (msg) msg.textContent = r.error ? ("错误：" + r.error)
                                     : (asGate ? "门控模型现为 " : "主模型现为 ") + id + "。从下一条消息开始生效。";
}

// "Your models" — the curated shortlist the chat pill shows, spanning every
// provider. The first pinned model per provider is that provider's default
// (used when you switch to it). pin/unpin/default all POST /api/pin.
function yourModelsCard(st){
  const pinned = st.pinned || [];
  const providers = (st.providers || []).map(p => p.name);
  const rows = pinned.map(p => `
    <div class="pinrow ${(p.provider===st.provider && p.model===st.model)?"on":""}">
      <span class="mm-prov">${esc(p.provider)}</span>
      <code style="flex:1;word-break:break-all">${esc(p.model)}</code>
      ${p.default ? `<span class="srcpill" title="该服务商的默认模型">默认</span>`
                  : `<a class="reveal" onclick="pinModel('${esc(p.provider)}','${esc(p.model)}','default')" title="设为 ${esc(p.provider)} 的默认模型">设为默认</a>`}
      <a class="reveal" onclick="pinModel('${esc(p.provider)}','${esc(p.model)}','unpin')" title="从列表中移除">移除</a>
    </div>`).join("") || `<div class="meta">尚未固定模型——可在下方添加。</div>`;
  // The add row is self-contained: pick any provider + type/choose a model id,
  // then Add. Works even for providers with no live catalog. The datalist
  // suggests the CURRENT provider's models (the only one we've fetched).
  const provOpts = providers.map(n => `<option value="${esc(n)}" ${n===st.provider?"selected":""}>${esc(n)}</option>`).join("");
  // Populate the model <select> for the initially-selected provider once the
  // card is in the DOM (a fresh fetch of that provider's catalog).
  setTimeout(() => loadAddModels(st.provider), 0);
  return `<h2>你的模型 <span class="meta" style="font-weight:400">——聊天模型切换器中显示的内容</span></h2>
    <div class="card">
      ${rows}
      <div class="addmodel">
        <select id="add-prov" onfocus="markEditing()" onchange="loadAddModels(this.value)">${provOpts}</select>
        <select id="add-model"><option value="">正在加载模型…</option></select>
        <button class="save" onclick="addPinnedModel()">添加</button>
      </div>
      <div class="meta" style="margin-top:6px" id="add-msg">选择服务商和模型，然后点击“添加”。</div>
    </div>`;
}

// Fill the add-row model <select> with a provider's catalog (any provider, not
// just the active one — the backend takes a ?provider= override).
async function loadAddModels(provider){
  const sel = document.getElementById("add-model");
  const msg = document.getElementById("add-msg");
  if (!sel) return;
  sel.innerHTML = `<option value="">正在加载 ${esc(provider)} 模型…</option>`;
  let data;
  try { data = await (await fetch("/api/models?provider=" + encodeURIComponent(provider))).json(); }
  catch(e){ sel.innerHTML = `<option value="">加载失败——请选择其他服务商</option>`; return; }
  const ms = data.models || [];
  sel.innerHTML = `<option value="">选择模型…</option>` + ms.map(m => {
    const meta = [m.free ? "免费" : (m.price_out != null ? `$${m.price_in}/$${m.price_out}` : ""),
                  m.context ? Math.round(m.context/1000) + "k" : ""].filter(Boolean).join(" · ");
    return `<option value="${esc(m.id)}">${esc(m.id)}${meta ? "  ("+esc(meta)+")" : ""}</option>`;
  }).join("");
  if (msg) msg.innerHTML = data.listed
    ? `<b>${esc(provider)}</b> 上有 ${ms.length} 个模型。选择一个并添加，或在下方目录中点击星标。`
    : data.error
      ? `无法列出 <b>${esc(provider)}</b>：<span style="color:var(--bad)">${esc(data.error)}</span>——仅显示默认模型。`
      : `<b>${esc(provider)}</b> 没有可用的实时目录（仅显示默认模型）。设置其 API 密钥可列出更多模型。`;
}

async function addPinnedModel(){
  const provider = document.getElementById("add-prov")?.value;
  const model = document.getElementById("add-model")?.value;
  if (!provider || !model) return;
  await pinModel(provider, model, "pin");   // refreshes; the row appears in the list
}

async function pinModel(provider, model, action){
  const r = await postJSON("/api/pin", {provider, model, action});
  if (!r.error){ editing = false; await refresh(); }
}

// --- Models page: a grid of provider cards (logo, name, status dot, actions)
// plus an edit modal. Status is derived, never stored: unconfigured = no key,
// configured = key set but disabled, enabled = key set and available. The
// ACTIVE provider (settings.provider) can't be disabled (server guards too).
function providerCardStatus(p, st){
  const keySet = !!(p.fields && p.fields[0] && p.fields[0].configured);
  if (!keySet) return "unconfigured";
  return (st.disabled_providers || []).includes(p.key) ? "configured" : "enabled";
}

function modelsGrid(d){
  const st = d.settings || {};
  const rank = p => p.key === st.provider ? 0
    : providerCardStatus(p, st) === "enabled" ? 1
    : providerCardStatus(p, st) === "configured" ? 2 : 3;
  const providers = (d.providers || []).slice()
    .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  return `<div class="provgrid">` + providers.map(p => providerCard(p, st)).join("") +
    `</div><div id="prov-modal-root"></div>`;
}

function providerCard(p, st){
  const status = providerCardStatus(p, st);
  const current = p.key === st.provider;
  const dot = status === "enabled" ? "var(--good)" : status === "configured" ? "#4c9aff" : "var(--bad)";
  return `<div class="provcard" data-provider="${esc(p.key)}">
    ${current ? `<span class="srcpill prov-current" style="background:var(--good-soft);color:var(--good)">当前</span>` : ""}
    <img class="provlogo" src="/static/logos/${esc(p.key)}.svg" alt="" onerror="this.style.display='none'">
    <div class="provname">${esc(p.name)}</div>
    <div class="provstatus"><span class="provdot" style="background:${dot}"></span>${statusZh(status)}</div>
    <div class="provactions">
      <button class="save ghost" onclick="openProviderModal('${esc(p.key)}')">编辑</button>
      ${status === "configured" ? `<button class="save ghost" onclick="toggleProvider('${esc(p.key)}',false)">启用</button>` : ""}
      ${status === "enabled" && !current ? `<button class="save ghost" onclick="toggleProvider('${esc(p.key)}',true)">禁用</button>` : ""}
    </div></div>`;
}

// enable/disable a provider (the grid button). Server keeps the key; the
// provider just leaves/enters the available list.
async function toggleProvider(provider, disabled){
  const r = await postJSON("/api/providers", {provider, disabled});
  if (!r.ok) alert(r.error || "更新失败");
  else { editing = false; await refresh(); }
}

// --- edit modal: API key (+ main/small model when this provider is current,
// with a searchable live catalog) and a "set as current" action.
function openProviderModal(provider){
  markEditing();   // keep the 5s refresh loop from wiping this modal
  const st = (D && D.settings) || {};
  const p = (D.providers || []).find(x => x.key === provider);
  if (!p) return;
  const current = provider === st.provider;
  const f = (p.fields || [])[0] || {};
  const baseField = (p.fields || []).find(field => field.name.endsWith("_BASE_URL"));
  const selectedBaseUrl = current && st.base_url ? st.base_url : (baseField?.value || "");
  const root = document.getElementById("prov-modal-root");
  root.innerHTML = `<div class="provmodal-back" onclick="closeProviderModal()">
    <div class="provmodal${current ? " provmodal-models" : ""}" onclick="event.stopPropagation()">
      <div class="u" style="display:flex;justify-content:space-between;align-items:center">
        <b>${esc(p.name)}</b><a class="reveal" onclick="closeProviderModal()">✕</a></div>
      <label class="fld"><span>API 密钥 <span class="meta">(${esc(f.name || "")})</span>
        ${f.configured ? `<span class="srcpill" style="background:var(--good-soft);color:var(--good)">已设置 ····${esc(f.last4 || "")}</span>`
                       : `<span class="srcpill apple">未设置</span>`}</span>
        <input type="password" id="pm-key" placeholder="${f.configured ? "已有密钥——留空即可保留" : "粘贴密钥"}"></label>
      ${baseField ? `<label class="fld"><span>Base URL <span class="meta">（选择 API 密钥所属区域）</span></span>
        <select id="pm-base-url" onfocus="markEditing()">
          ${(baseField.options || []).map((url, index) => {
            const label = (baseField.option_labels || [])[index];
            return `<option value="${escAttr(url)}" ${url===selectedBaseUrl?"selected":""}>${label?esc(label)+" — ":""}${esc(url)}</option>`;
          }).join("")}
        </select></label>` : ""}
      ${current ? `
      ${renderModelPicker("pm-model", "主模型（运行循环；需要工具调用能力）", st.model || "")}
      ${renderModelPicker("pm-small-model", "门控 / 摘要模型", st.small_model || "")}` : ""}
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="save" id="pm-save" onclick="saveProviderModal('${esc(provider)}')">保存</button>
        ${!current ? `<button class="save ghost" id="pm-make-current" onclick="makeCurrentProvider('${esc(provider)}')">设为当前服务商</button>` : ""}
      </div>
      <span class="meta" id="pm-msg"></span>
    </div></div>`;
  if (current) loadModalModels(provider);
}

function closeProviderModal(){
  editing = false;
  const root = document.getElementById("prov-modal-root");
  if (root) root.innerHTML = "";
}

// Populate both modal pickers from one request: this provider's live catalog,
// or its defaults when there is no catalog. Manual typing always still works.
async function loadModalModels(provider){
  setupModelPickers([], provider);
  setModelPickerMeta("正在加载模型…");
  let data;
  try {
    const response = await fetch("/api/models?provider=" + encodeURIComponent(provider));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch(e){
    data = {models: [], listed: false, error: e.message || String(e)};
  }
  setupModelPickers(data.models || [], provider);
  if (!data.listed){
    setModelPickerMeta(data.error && !(data.models || []).length
      ? "无法加载模型目录——你仍可手动输入任意模型 ID。"
      : data.error
        ? "无法加载模型目录——仅显示默认模型。"
      : "实时模型目录不可用——显示默认模型。");
  }
}

// Shared model list for the currently open modal.
let _modalModels = [];
let _activeModelPicker = null;
let _outsidePickerListener = false;

function escAttr(s){
  return esc(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function renderModelPicker(id, label, value){
  return `<label class="fld">${esc(label)}
    <div class="model-picker" id="${escAttr(id)}-picker">
      <div class="model-picker-input">
        <input type="text" id="${escAttr(id)}" value="${escAttr(value || "")}" autocomplete="off" onfocus="markEditing()" onclick="event.stopPropagation()">
        <button type="button" class="model-picker-toggle" onclick="toggleModelPicker('${escAttr(id)}'); event.stopPropagation();" aria-label="展开模型列表" aria-controls="${escAttr(id)}-list" aria-expanded="false">▾</button>
      </div>
      <div class="model-picker-list" id="${escAttr(id)}-list" role="listbox">
        <input type="text" class="model-picker-search" id="${escAttr(id)}-search" placeholder="筛选模型…" autocomplete="off" aria-label="筛选模型" oninput="filterModelPicker('${escAttr(id)}')" onfocus="markEditing()" onclick="event.stopPropagation()">
        <div class="model-picker-items" id="${escAttr(id)}-items"></div>
        <div class="model-picker-meta" id="${escAttr(id)}-meta" aria-live="polite"></div>
      </div>
    </div>
  </label>`;
}

function setupModelPickers(models, provider){
  _modalModels = Array.isArray(models) ? models : [];
  ["pm-model", "pm-small-model"].forEach(id => {
    const input = document.getElementById(id);
    if (!input) return;
    const itemsBox = document.getElementById(id + "-items");
    const search = document.getElementById(id + "-search");
    if (itemsBox && !itemsBox.dataset.modelPickerBound){
      itemsBox.dataset.modelPickerBound = "true";
      itemsBox.addEventListener("click", e => {
        const item = e.target.closest(".model-picker-item");
        if (!item) return;
        e.stopPropagation();
        selectModelPicker(id, item.dataset.model || "");
      });
      search?.addEventListener("keydown", e => {
        if (e.key !== "Enter") return;
        const query = search.value.toLowerCase();
        const first = _modalModels.find(m => (m.id || "").toLowerCase().includes(query));
        if (!first) return;
        e.preventDefault();
        selectModelPicker(id, first.id || "");
      });
    }
    renderModelPickerItems(id, (search?.value || "").toLowerCase());
  });
  if (!_outsidePickerListener){
    _outsidePickerListener = true;
    document.addEventListener("click", e => {
      if (!e.target.closest?.(".model-picker")) closeAllModelPickers();
    }, true);
    document.addEventListener("keydown", e => { if (e.key === "Escape") closeAllModelPickers(); });
  }
}

function setModelPickerMeta(message){
  ["pm-model", "pm-small-model"].forEach(id => {
    const meta = document.getElementById(id + "-meta");
    if (meta) meta.textContent = message;
  });
}

function toggleModelPicker(id){
  const list = document.getElementById(id + "-list");
  if (!list) return;
  const isOpen = list.classList.contains("open");
  closeAllModelPickers();
  if (!isOpen){
    list.classList.add("open");
    list.parentElement.querySelector(".model-picker-toggle")?.setAttribute("aria-expanded", "true");
    _activeModelPicker = id;
    const search = document.getElementById(id + "-search");
    if (search) search.focus();
  }
}

function closeAllModelPickers(){
  document.querySelectorAll(".model-picker-list.open").forEach(el => {
    el.classList.remove("open");
    el.parentElement.querySelector(".model-picker-toggle")?.setAttribute("aria-expanded", "false");
  });
  _activeModelPicker = null;
}

function closeModelPicker(id){
  const list = document.getElementById(id + "-list");
  if (list){
    list.classList.remove("open");
    list.parentElement.querySelector(".model-picker-toggle")?.setAttribute("aria-expanded", "false");
  }
  if (_activeModelPicker === id) _activeModelPicker = null;
}

function filterModelPicker(id){
  const query = (document.getElementById(id + "-search")?.value || "").toLowerCase();
  renderModelPickerItems(id, query);
}

function renderModelPickerItems(id, query){
  const itemsBox = document.getElementById(id + "-items");
  const metaBox = document.getElementById(id + "-meta");
  if (!itemsBox) return;
  const filtered = _modalModels.filter(m => (m.id || "").toLowerCase().includes(query));
  itemsBox.innerHTML = filtered.map((m, index) => `<div class="model-picker-item${index === 0 ? " active" : ""}" role="option" data-model="${escAttr(m.id)}">${esc(m.id)}</div>`).join("");
  if (metaBox){
    if (_modalModels.length === 0) metaBox.textContent = "未加载模型——你仍可手动输入任意模型 ID。";
    else if (filtered.length === 0) metaBox.textContent = `没有模型匹配“${query}”。`;
    else metaBox.textContent = "";
  }
}

function selectModelPicker(id, value){
  const input = document.getElementById(id);
  if (input){
    input.value = value;
    input.focus();
  }
  closeModelPicker(id);
}

function modalKeyPayload(provider){
  const key = document.getElementById("pm-key")?.value;
  const payload = {provider};
  if (key) payload.key = key;
  const baseUrl = document.getElementById("pm-base-url")?.value;
  if (baseUrl) payload.base_url = baseUrl;
  return payload;
}

function setProviderModalBusy(activeId, busy){
  ["pm-save", "pm-make-current"].forEach(id => {
    const button = document.getElementById(id);
    if (!button) return;
    if (!button.dataset.label) button.dataset.label = button.textContent;
    button.disabled = busy;
    button.textContent = busy && id === activeId
      ? (id === "pm-make-current" ? "正在切换…" : "正在保存…")
      : button.dataset.label;
  });
  if (busy){
    const msg = document.getElementById("pm-msg");
    if (msg) msg.textContent = activeId === "pm-make-current"
      ? "正在切换服务商…" : "正在保存并验证更改…";
  }
}

async function submitProviderModal(provider, payload, activeId){
  setProviderModalBusy(activeId, true);
  let r;
  try { r = await postJSON("/api/providers", payload); }
  catch(e){ r = {ok:false, error:e.message || String(e)}; }
  if (!r.ok){
    setProviderModalBusy(activeId, false);
    const msg = document.getElementById("pm-msg");
    if (msg) msg.textContent = r.error || "更新失败";
    return;
  }
  editing = false;
  closeProviderModal();
  await refresh();
}

async function saveProviderModal(provider){
  const st = (D && D.settings) || {};
  const payload = modalKeyPayload(provider);
  payload.activate = false;
  if (provider === st.provider){
    payload.model = document.getElementById("pm-model")?.value ?? "";
    payload.small_model = document.getElementById("pm-small-model")?.value ?? "";
  }
  await submitProviderModal(provider, payload, "pm-save");
}

// "Set as current": apply_provider switches provider and picks its default
// models when none are passed (keeps the key field if one was just typed).
async function makeCurrentProvider(provider){
  await submitProviderModal(provider, modalKeyPayload(provider), "pm-make-current");
}

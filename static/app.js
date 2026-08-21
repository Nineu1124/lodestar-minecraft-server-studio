"use strict";

const state = {
  servers: [],
  activeId: null,
  status: null,
  mods: [],
  modFilter: "all",
  view: "dashboard",
  commandHistory: [],
  commandIndex: 0,
  logCleared: false,
  diagnostics: null,
  timers: [],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function formatBytesMB(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return number >= 1024 ? `${(number / 1024).toFixed(1)} GB` : `${number.toFixed(1)} MB`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "等待采样";
  const total = Math.max(0, Math.floor(Number(seconds)));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return `已运行 ${days} 天 ${hours} 小时`;
  if (hours) return `已运行 ${hours} 小时 ${minutes} 分`;
  return `已运行 ${minutes} 分钟`;
}

function activeServer() {
  return state.servers.find((server) => server.id === state.activeId) || null;
}

function loadCommandHistory() {
  try {
    state.commandHistory = JSON.parse(localStorage.getItem(`ripple-command-${state.activeId}`) || "[]");
    if (!Array.isArray(state.commandHistory)) state.commandHistory = [];
  } catch {
    state.commandHistory = [];
  }
  state.commandHistory = state.commandHistory.slice(-50);
  state.commandIndex = state.commandHistory.length;
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: { ...(options.headers || {}) } };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`面板返回了无法解析的内容（HTTP ${response.status}）`);
  }
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  return payload;
}

function toast(message, type = "success", duration = 4200) {
  const item = document.createElement("div");
  item.className = `toast ${type === "error" ? "error" : ""}`;
  const text = document.createElement("span");
  text.textContent = message;
  item.append(text);
  $("#toast-region").append(item);
  window.setTimeout(() => item.remove(), duration);
}

function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = label || "处理中…";
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("ripple-theme", theme);
  const meta = $('meta[name="theme-color"]');
  if (meta) meta.content = theme === "dark" ? "#191917" : "#f6f4ee";
}

function initializeTheme() {
  const stored = localStorage.getItem("ripple-theme");
  const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  applyTheme(stored || (dark ? "dark" : "light"));
}

function openImportModal() {
  $("#import-modal").classList.remove("hidden");
  window.setTimeout(() => $("#import-path").focus(), 50);
}

function closeImportModal() {
  $("#import-modal").classList.add("hidden");
}

function showView(view) {
  state.view = view;
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach((panel) => panel.classList.toggle("active", panel.dataset.viewPanel === view));
  $("#sidebar").classList.remove("open");
  if (!state.activeId) return;
  if (view === "console") refreshLogs();
  if (view === "mods") refreshMods();
  if (view === "settings") refreshSettings();
  if (view === "players") refreshPlayers();
  if (view === "backups") { refreshBackups(); refreshJobs(); }
  if (view === "automation") refreshAutomation();
  if (view === "diagnostics") refreshDiagnostics();
}

function renderServerList() {
  const container = $("#server-list");
  container.innerHTML = "";
  for (const server of state.servers) {
    const button = document.createElement("button");
    button.className = `server-entry ${server.id === state.activeId ? "active" : ""}`;
    button.dataset.serverId = server.id;
    const initial = (server.name || "M").trim().slice(0, 1).toUpperCase();
    button.innerHTML = `<span class="server-icon">${escapeHtml(initial)}</span><span><strong>${escapeHtml(server.name)}</strong></span><i class="mini-dot ${server.id === state.activeId && state.status?.running ? "online" : ""}"></i>`;
    container.append(button);
  }
}

function renderWorkspaceState() {
  const empty = !state.activeId || state.servers.length === 0;
  $("#empty-state").classList.toggle("hidden", !empty);
  $("#workspace").classList.toggle("hidden", empty);
  $("#start-button").disabled = empty;
  $("#stop-button").disabled = empty;
  if (empty) {
    $("#server-title").textContent = "Ripple Server Panel";
    $("#server-path").textContent = "尚未导入服务端";
  }
}

async function bootstrap(preferredId = null) {
  const payload = await api("/api/bootstrap");
  state.servers = payload.data.servers || [];
  state.activeId = preferredId && state.servers.some((item) => item.id === preferredId)
    ? preferredId
    : payload.data.active_server_id || state.servers[0]?.id || null;
  renderWorkspaceState();
  renderServerList();
  if (state.activeId) {
    loadCommandHistory();
    await refreshStatus();
    await refreshCurrentView();
  }
}

async function refreshCurrentView() {
  if (state.view === "console") return refreshLogs();
  if (state.view === "mods") return refreshMods();
  if (state.view === "settings") return refreshSettings();
  if (state.view === "players") return refreshPlayers();
  if (state.view === "backups") return Promise.all([refreshBackups(), refreshJobs()]);
  if (state.view === "automation") return refreshAutomation();
  if (state.view === "diagnostics") return refreshDiagnostics();
}

async function selectServer(serverId) {
  if (serverId === state.activeId) return;
  await api("/api/servers/select", { method: "POST", body: { server_id: serverId } });
  state.activeId = serverId;
  state.status = null;
  state.mods = [];
  state.logCleared = false;
  loadCommandHistory();
  renderServerList();
  await refreshStatus();
  await refreshCurrentView();
}

function setStatusPill(status) {
  const pill = $("#status-pill");
  pill.classList.remove("online", "offline", "starting");
  if (status?.ready) {
    pill.classList.add("online");
    $("span", pill).textContent = "运行中";
  } else if (status?.running) {
    pill.classList.add("starting");
    $("span", pill).textContent = "正在加载";
  } else {
    pill.classList.add("offline");
    $("span", pill).textContent = "已停止";
  }
}

function updateStatusUI(status) {
  const server = activeServer();
  if (!server) return;
  $("#server-title").textContent = server.name;
  $("#server-path").textContent = server.path;
  setStatusPill(status);
  $("#start-button").textContent = status.running ? "重新启动" : "启动服务端";
  $("#start-button").dataset.action = status.running ? "restart" : "start";
  $("#stop-button").disabled = !status.running;
  const heroStart = $(".hero-actions .button.primary");
  if (heroStart) {
    heroStart.dataset.action = status.running ? "stop" : "start";
    heroStart.textContent = status.running ? "正常停服" : "启动";
  }

  $("#hero-title").textContent = status.ready ? "服务器正在运行。" : status.running ? "服务器正在启动。" : "服务器当前已停止。";
  $("#hero-subtitle").textContent = status.ready
    ? `${status.motd || "Minecraft Server"} · ${status.managed ? "由面板托管" : "已接管外部控制台"}`
    : status.running ? "Java 进程已启动，等待 Minecraft 状态端口就绪。" : "可以调整启动配置、管理 Mod 或创建离线备份。";

  $("#metric-players").textContent = `${status.players?.online ?? 0} / ${status.players?.max ?? "—"}`;
  $("#metric-player-names").textContent = status.players?.names?.length ? status.players.names.join("、") : "暂无在线玩家";
  $("#metric-memory").textContent = formatBytesMB(status.process?.memory_mb);
  $("#metric-pid").textContent = status.process?.pid ? `PID ${status.process.pid}` : "等待 Java 进程";
  $("#metric-cpu").textContent = status.process?.cpu_percent === null || status.process?.cpu_percent === undefined
    ? "—" : `${Number(status.process.cpu_percent).toFixed(1)}%`;
  $("#metric-uptime").textContent = status.running ? formatDuration(status.process?.uptime_seconds) : "服务端未运行";
  $("#metric-version").textContent = status.version || "未知";
  $("#metric-loader").textContent = String(status.loader || "unknown").toUpperCase();
  $("#metric-port").textContent = String(status.port || "—");
  $("#metric-address").textContent = `127.0.0.1:${status.port || "—"}`;
  $("#metric-disk").textContent = status.storage?.free_gb === null || status.storage?.free_gb === undefined
    ? "—" : `${Number(status.storage.free_gb).toFixed(1)} GB`;
  $("#metric-disk-used").textContent = status.storage?.used_percent === null || status.storage?.used_percent === undefined
    ? "等待读取" : `已使用 ${Number(status.storage.used_percent).toFixed(1)}%`;
  const launchLabels = {
    forge_args: "Forge / NeoForge 参数文件",
    jar: "直接运行 JAR",
    script: "启动脚本",
  };
  $("#detail-launch").textContent = server.launch_mode === "auto"
    ? `自动检测 · ${String(status.loader || "Minecraft").toUpperCase()}`
    : launchLabels[server.launch_mode] || server.launch_mode;
  $("#detail-memory").textContent = `${server.xms} – ${server.xmx}`;
  $("#detail-external").textContent = server.external_address || "未设置";
  $("#detail-frp").textContent = status.frp_running ? "frpc 正在运行" : "未发现 frpc";
  $("#detail-online-mode").textContent = status.online_mode === false ? "已关闭" : "读取配置中";

  const eulaBox = $("#eula-box");
  if (eulaBox) {
    eulaBox.classList.toggle("pending", !status.eula);
    $("#eula-status").textContent = status.eula ? "已同意（eula=true）" : "尚未同意，无法启动服务端";
    $("#accept-eula").classList.toggle("hidden", Boolean(status.eula));
  }
  renderServerList();
}

async function refreshStatus(showError = false) {
  if (!state.activeId || document.hidden) return;
  try {
    const payload = await api(`/api/status?server_id=${encodeURIComponent(state.activeId)}`);
    state.status = payload.data;
    const config = await api(`/api/config?server_id=${encodeURIComponent(state.activeId)}`);
    state.status.online_mode = config.data["online-mode"] === "true";
    updateStatusUI(state.status);
  } catch (error) {
    if (showError) toast(error.message, "error");
  }
}

async function runAction(action, sourceButton = null) {
  if (!state.activeId) return openImportModal();
  if (action === "restart" && !window.confirm("确定正常保存并重启服务端吗？")) return;
  if (action === "stop" && !window.confirm("确定正常保存并停止服务端吗？")) return;
  const button = sourceButton || $(`[data-action="${action}"]`);
  setBusy(button, true, action === "start" ? "正在启动…" : action === "stop" ? "正在停服…" : "处理中…");
  try {
    const payload = await api("/api/action", { method: "POST", body: { server_id: state.activeId, action } });
    toast(payload.message);
    await refreshStatus(true);
  } catch (error) {
    toast(error.message, "error", 6500);
    if (error.message.includes("EULA")) showView("settings");
  } finally {
    setBusy(button, false);
  }
}

async function refreshLogs() {
  if (!state.activeId || state.view !== "console" || document.hidden || state.logCleared) return;
  try {
    const payload = await api(`/api/logs?server_id=${encodeURIComponent(state.activeId)}`);
    const output = $("#console-output");
    const shouldStick = $("#log-autoscroll").checked && (output.scrollHeight - output.scrollTop - output.clientHeight < 130);
    output.textContent = payload.data.text;
    $("#log-source").textContent = payload.data.source;
    if (shouldStick) output.scrollTop = output.scrollHeight;
  } catch (error) {
    $("#console-output").textContent = `读取日志失败：${error.message}`;
  }
}

async function sendConsoleCommand(event) {
  event.preventDefault();
  const input = $("#command-input");
  const command = input.value.trim().replace(/^\//, "");
  if (!command) return;
  const submit = $("button[type='submit']", event.currentTarget);
  setBusy(submit, true, "发送中…");
  try {
    const payload = await api("/api/command", { method: "POST", body: { server_id: state.activeId, command } });
    state.commandHistory.push(command);
    state.commandHistory = state.commandHistory.slice(-50);
    state.commandIndex = state.commandHistory.length;
    localStorage.setItem(`ripple-command-${state.activeId}`, JSON.stringify(state.commandHistory));
    input.value = "";
    state.logCleared = false;
    toast(payload.message);
    window.setTimeout(refreshLogs, 350);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(submit, false);
    input.focus();
  }
}

async function runPresetCommand(button) {
  const command = button.dataset.serverCommand;
  if (!command || !state.activeId) return;
  setBusy(button, true, "执行中…");
  try {
    await api("/api/command", { method: "POST", body: { server_id: state.activeId, command } });
    state.logCleared = false;
    toast(button.dataset.success || "命令已执行");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function sendBroadcast(event) {
  event.preventDefault();
  const input = $("#broadcast-message");
  const message = input.value.trim().replace(/[\r\n]+/g, " ");
  if (!message) return toast("请输入公告内容", "error");
  const button = $("button[type='submit']", event.currentTarget);
  setBusy(button, true, "发送中…");
  try {
    await api("/api/command", { method: "POST", body: { server_id: state.activeId, command: `say ${message}` } });
    input.value = "";
    state.logCleared = false;
    toast("公告已发送");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function commandHistoryKey(event) {
  if (!["ArrowUp", "ArrowDown"].includes(event.key) || !state.commandHistory.length) return;
  event.preventDefault();
  if (event.key === "ArrowUp") state.commandIndex = Math.max(0, state.commandIndex - 1);
  else state.commandIndex = Math.min(state.commandHistory.length, state.commandIndex + 1);
  event.currentTarget.value = state.commandHistory[state.commandIndex] || "";
}

async function refreshMods() {
  if (!state.activeId || document.hidden) return;
  try {
    const payload = await api(`/api/mods?server_id=${encodeURIComponent(state.activeId)}`);
    state.mods = payload.data || [];
    renderMods();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderMods() {
  const search = $("#mod-search").value.trim().toLocaleLowerCase();
  const counts = { all: state.mods.length, enabled: 0, disabled: 0, trash: 0 };
  state.mods.forEach((mod) => counts[mod.state]++);
  Object.entries(counts).forEach(([key, value]) => { $(`#mod-count-${key}`).textContent = value; });
  const items = state.mods.filter((mod) => {
    const stateMatches = state.modFilter === "all" || mod.state === state.modFilter;
    const text = `${mod.display_name} ${mod.name} ${mod.mod_id}`.toLocaleLowerCase();
    return stateMatches && (!search || text.includes(search));
  });
  const tbody = $("#mod-table");
  tbody.innerHTML = items.map((mod) => {
    const label = mod.state === "enabled" ? "已启用" : mod.state === "disabled" ? "已停用" : "回收站";
    let actions = "";
    if (mod.state === "enabled") actions = `<button class="row-button" data-mod-action="disable">停用</button><button class="row-button danger" data-mod-action="remove">移除</button>`;
    if (mod.state === "disabled") actions = `<button class="row-button" data-mod-action="enable">启用</button><button class="row-button danger" data-mod-action="remove">移除</button>`;
    if (mod.state === "trash") actions = `<button class="row-button" data-mod-action="restore">恢复</button>`;
    return `<tr data-name="${escapeHtml(mod.name)}" data-state="${escapeHtml(mod.state)}"><td><div class="mod-name"><span class="mod-gem">◇</span><span><strong>${escapeHtml(mod.display_name || mod.name)}</strong><small>${escapeHtml(mod.mod_id || mod.name)}</small></span></div></td><td>${escapeHtml(mod.version || "—")}</td><td>${mod.size_mb.toFixed(2)} MB</td><td><span class="state-badge ${escapeHtml(mod.state)}">${label}</span></td><td><div class="row-actions">${actions}</div></td></tr>`;
  }).join("");
  $("#mod-empty").classList.toggle("hidden", items.length !== 0);
}

async function modAction(button) {
  const row = button.closest("tr");
  const action = button.dataset.modAction;
  const name = row.dataset.name;
  if (action === "remove" && !window.confirm(`把 ${name} 移入面板回收站吗？`)) return;
  setBusy(button, true);
  try {
    const payload = await api("/api/mods/action", { method: "POST", body: { server_id: state.activeId, action, state: row.dataset.state, name } });
    toast(payload.message);
    await refreshMods();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function uploadMods(files) {
  if (!files.length || !state.activeId) return;
  const label = $(".upload-button");
  const original = label.childNodes[0].textContent;
  try {
    let index = 0;
    for (const file of files) {
      index++;
      label.childNodes[0].textContent = `上传 ${index}/${files.length}…`;
      const response = await fetch("/api/mods/upload", {
        method: "POST",
        headers: {
          "Content-Type": "application/java-archive",
          "X-Server-Id": state.activeId,
          "X-Filename": encodeURIComponent(file.name),
        },
        body: file,
      });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.error || `上传 ${file.name} 失败`);
      toast(payload.message);
    }
    await refreshMods();
  } catch (error) {
    toast(error.message, "error", 6500);
  } finally {
    label.childNodes[0].textContent = original;
    $("#mod-upload").value = "";
  }
}

async function refreshSettings() {
  if (!state.activeId) return;
  try {
    const [configPayload] = await Promise.all([
      api(`/api/config?server_id=${encodeURIComponent(state.activeId)}`),
      refreshStatus(),
    ]);
    fillProperties(configPayload.data);
    fillLaunchProfile(activeServer());
  } catch (error) {
    toast(error.message, "error");
  }
}

function fillProperties(config) {
  const form = $("#properties-form");
  for (const [key, value] of Object.entries(config)) {
    const field = form.elements.namedItem(key);
    if (!field) continue;
    if (field.type === "checkbox") field.checked = value === "true";
    else field.value = value;
  }
}

function fillLaunchProfile(server) {
  if (!server) return;
  const form = $("#launch-form");
  for (const key of ["name", "java", "xms", "xmx", "launch_mode", "external_address", "auto_backup_keep"]) {
    if (form.elements.namedItem(key)) form.elements.namedItem(key).value = server[key] ?? "";
  }
  const select = $("#launch-target");
  select.innerHTML = '<option value="">自动选择</option>';
  for (const candidate of server.detected?.candidates || []) {
    const option = document.createElement("option");
    option.value = candidate.target;
    option.textContent = `${candidate.label} · ${candidate.target}`;
    select.append(option);
  }
  if (server.launch_target && ![...select.options].some((option) => option.value === server.launch_target)) {
    const option = document.createElement("option");
    option.value = server.launch_target;
    option.textContent = `自定义 · ${server.launch_target}`;
    select.append(option);
  }
  select.value = server.launch_target || "";
}

async function saveProperties(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const settings = {};
  for (const field of form.elements) {
    if (!field.name) continue;
    if (field.type === "checkbox") settings[field.name] = field.checked;
    else if (field.type === "number") {
      if (field.value !== "") settings[field.name] = Number(field.value);
    } else settings[field.name] = field.value;
  }
  const button = $("button[type='submit']", form);
  setBusy(button, true);
  try {
    const payload = await api("/api/config", { method: "POST", body: { server_id: state.activeId, settings } });
    toast(payload.message);
    await refreshStatus();
  } catch (error) {
    toast(error.message, "error");
  } finally { setBusy(button, false); }
}

async function saveLaunch(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const profile = Object.fromEntries(new FormData(form).entries());
  profile.auto_backup_keep = Number(profile.auto_backup_keep);
  const button = $("button[type='submit']", form);
  setBusy(button, true);
  try {
    const payload = await api("/api/servers/update", { method: "POST", body: { server_id: state.activeId, profile } });
    toast(payload.message);
    await bootstrap(state.activeId);
  } catch (error) {
    toast(error.message, "error");
  } finally { setBusy(button, false); }
}

async function acceptEula() {
  if (!window.confirm("只有在你已经阅读并同意 Minecraft EULA 后才能继续。确定写入 eula=true 吗？")) return;
  try {
    const payload = await api("/api/action", { method: "POST", body: { server_id: state.activeId, action: "accept_eula" } });
    toast(payload.message);
    await refreshStatus(true);
  } catch (error) { toast(error.message, "error"); }
}

async function forceStop() {
  if (!window.confirm("强制停止不会等待世界保存，可能导致数据损坏。确定继续吗？")) return;
  await runAction("force_stop", $("#force-stop"));
}

async function removeServer() {
  const server = activeServer();
  if (!server || !window.confirm(`只从面板移除“${server.name}”吗？原文件夹不会被删除。`)) return;
  try {
    const payload = await api("/api/servers/remove", { method: "POST", body: { server_id: state.activeId } });
    toast(payload.message);
    await bootstrap();
  } catch (error) { toast(error.message, "error"); }
}

function renderNameList(containerSelector, countSelector, values) {
  const container = $(containerSelector);
  const names = values.map((item) => item.name || item.ip || "未知");
  $(countSelector).textContent = names.length;
  container.innerHTML = names.length
    ? names.map((name) => `<span class="player-chip">${escapeHtml(name)}</span>`).join("")
    : '<span class="chip-empty">暂无记录</span>';
}

async function refreshPlayers() {
  if (!state.activeId) return;
  try {
    const payload = await api(`/api/players?server_id=${encodeURIComponent(state.activeId)}`);
    renderNameList("#whitelist-list", "#whitelist-count", payload.data.whitelist || []);
    renderNameList("#ops-list", "#ops-count", payload.data.operators || []);
    renderNameList("#banned-list", "#banned-count", payload.data.banned_players || []);
  } catch (error) { toast(error.message, "error"); }
}

async function submitPlayerAction(event) {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  const body = { server_id: state.activeId, name: $("#player-name").value.trim(), action: $("#player-action").value, reason: $("#player-reason").value.trim() };
  setBusy(button, true);
  try {
    const payload = await api("/api/player", { method: "POST", body });
    toast(payload.message);
    window.setTimeout(refreshPlayers, 550);
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(button, false); }
}

async function refreshBackups() {
  if (!state.activeId) return;
  try {
    const payload = await api(`/api/backups?server_id=${encodeURIComponent(state.activeId)}`);
    const tbody = $("#backup-table");
    tbody.innerHTML = (payload.data || []).map((backup) => `<tr data-name="${escapeHtml(backup.name)}"><td><div class="mod-name"><span class="mod-gem">◴</span><span><strong>${escapeHtml(backup.name)}</strong><small>世界与关键配置</small></span></div></td><td>${escapeHtml(formatDate(backup.modified))}</td><td>${Number(backup.size_mb).toFixed(2)} MB</td><td><div class="row-actions"><a class="row-button" href="/api/backup/download?server_id=${encodeURIComponent(state.activeId)}&name=${encodeURIComponent(backup.name)}">下载</a><button class="row-button danger" data-backup-remove>移除</button></div></td></tr>`).join("");
    $("#backup-empty").classList.toggle("hidden", payload.data.length !== 0);
  } catch (error) { toast(error.message, "error"); }
}

async function createBackup() {
  const button = $("#backup-create");
  setBusy(button, true, "正在创建…");
  try {
    const payload = await api("/api/backups", { method: "POST", body: { server_id: state.activeId, action: "create" } });
    toast(payload.message);
    $("#backup-job").classList.remove("hidden");
    await refreshJobs();
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(button, false); }
}

async function removeBackup(button) {
  const name = button.closest("tr").dataset.name;
  if (!window.confirm(`把备份 ${name} 移入 .trash 吗？`)) return;
  setBusy(button, true);
  try {
    const payload = await api("/api/backups", { method: "POST", body: { server_id: state.activeId, action: "remove", name } });
    toast(payload.message);
    await refreshBackups();
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(button, false); }
}

async function refreshJobs() {
  if (!state.activeId || state.view !== "backups") return;
  try {
    const payload = await api("/api/jobs");
    const jobs = payload.data.filter((job) => job.server_id === state.activeId && job.type === "backup");
    const job = jobs.at(-1);
    const card = $("#backup-job");
    if (!job || (job.state === "done" && Date.now() - new Date(job.created_at).getTime() > 120000)) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    $("#job-title").textContent = job.state === "error" ? "备份失败" : job.state === "done" ? "备份完成" : "正在备份";
    $("#job-message").textContent = job.message;
    $("#job-progress").style.width = `${job.state === "done" ? 100 : job.progress || 12}%`;
    if (job.state === "done") refreshBackups();
  } catch { /* 定时刷新不打扰用户 */ }
}

async function importServer(event) {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  setBusy(button, true, "正在检测…");
  try {
    const payload = await api("/api/servers/import", { method: "POST", body: data });
    toast(payload.message);
    closeImportModal();
    event.currentTarget.reset();
    event.currentTarget.elements.xms.value = "2G";
    event.currentTarget.elements.xmx.value = "8G";
    await bootstrap(payload.profile.id);
  } catch (error) { toast(error.message, "error", 6500); }
  finally { setBusy(button, false); }
}

async function pickFolder() {
  const button = $("#pick-folder");
  setBusy(button, true, "等待选择…");
  try {
    const payload = await api("/api/servers/pick-folder", { method: "POST", body: {} });
    if (payload.path) $("#import-path").value = payload.path;
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(button, false); }
}

function fillAutomation(data) {
  const form = $("#automation-form");
  form.elements.backup_schedule_enabled.checked = Boolean(data.backup_schedule_enabled);
  form.elements.backup_interval_hours.value = data.backup_interval_hours ?? 6;
  form.elements.restart_schedule_enabled.checked = Boolean(data.restart_schedule_enabled);
  form.elements.restart_time.value = data.restart_time || "04:00";
  $("#automation-last-backup").textContent = data.last_backup ? formatDate(data.last_backup) : "尚未执行";
  $("#automation-next-backup").textContent = data.next_backup ? formatDate(data.next_backup) : "计划未启用";
  $("#automation-last-restart").textContent = data.last_restart_date || "尚未执行";
  $("#automation-next-restart").textContent = data.next_restart ? formatDate(data.next_restart) : "计划未启用";
}

async function refreshAutomation() {
  if (!state.activeId || document.hidden) return;
  try {
    const payload = await api(`/api/automation?server_id=${encodeURIComponent(state.activeId)}`);
    fillAutomation(payload.data);
  } catch (error) { toast(error.message, "error"); }
}

async function saveAutomation() {
  if (!state.activeId) return;
  const button = $("#automation-save");
  const form = $("#automation-form");
  const automation = {
    backup_schedule_enabled: form.elements.backup_schedule_enabled.checked,
    backup_interval_hours: Number(form.elements.backup_interval_hours.value),
    restart_schedule_enabled: form.elements.restart_schedule_enabled.checked,
    restart_time: form.elements.restart_time.value,
  };
  setBusy(button, true, "正在保存…");
  try {
    const payload = await api("/api/automation", { method: "POST", body: { server_id: state.activeId, automation } });
    fillAutomation(payload.data);
    toast(payload.message);
    await bootstrap(state.activeId);
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(button, false); }
}

function renderDiagnostics(data) {
  state.diagnostics = data;
  $("#diagnostic-java").textContent = data.java_version || "无法读取";
  $("#diagnostic-loader").textContent = `${String(data.loader || "unknown").toUpperCase()}${data.game_version ? ` · ${data.game_version}` : ""}`;
  $("#diagnostic-mods").textContent = String(data.mod_count ?? 0);
  $("#diagnostic-crashes").textContent = String(data.crash_reports?.length ?? 0);
  $("#diagnostic-disk").textContent = data.storage?.free_gb === null || data.storage?.free_gb === undefined
    ? "无法读取" : `可用 ${Number(data.storage.free_gb).toFixed(1)} / ${Number(data.storage.total_gb).toFixed(1)} GB`;
  $("#diagnostic-log-size").textContent = `${Number(data.latest_log_size_mb || 0).toFixed(2)} MB`;
  $("#diagnostic-last-exit").textContent = data.last_exit
    ? `退出码 ${data.last_exit.code} · ${formatDate(data.last_exit.at)}` : "本次面板启动后暂无记录";
  $("#diagnostic-generated").textContent = formatDate(data.generated_at);

  const errors = data.error_lines || [];
  $("#diagnostic-error-count").textContent = String(errors.length);
  $("#diagnostic-errors").innerHTML = errors.map((line, index) => `<li><span>${String(index + 1).padStart(2, "0")}</span><code>${escapeHtml(line)}</code></li>`).join("");
  $("#diagnostic-errors-empty").classList.toggle("hidden", errors.length !== 0);

  const crashes = data.crash_reports || [];
  $("#crash-list").innerHTML = crashes.map((report) => `<article><div><strong>${escapeHtml(report.name)}</strong><span>${escapeHtml(formatDate(report.modified))} · ${Number(report.size_kb).toFixed(1)} KB</span></div><a class="row-button" href="/api/crash-report/download?server_id=${encodeURIComponent(state.activeId)}&name=${encodeURIComponent(report.name)}">下载</a></article>`).join("");
  $("#crash-empty").classList.toggle("hidden", crashes.length !== 0);
}

async function refreshDiagnostics() {
  if (!state.activeId || document.hidden) return;
  const button = $("#diagnostic-refresh");
  setBusy(button, true, "扫描中…");
  try {
    const payload = await api(`/api/diagnostics?server_id=${encodeURIComponent(state.activeId)}`);
    renderDiagnostics(payload.data);
  } catch (error) { toast(error.message, "error"); }
  finally { setBusy(button, false); }
}

async function copyDiagnosticSummary() {
  const data = state.diagnostics;
  if (!data) return toast("请先重新扫描诊断信息", "error");
  const server = activeServer();
  const summary = [
    `Ripple Server Panel 诊断摘要`,
    `服务端：${server?.name || "未知"}`,
    `生成时间：${formatDate(data.generated_at)}`,
    `Java：${data.java_version || "无法读取"}`,
    `加载器：${data.loader || "unknown"} ${data.game_version || ""}`.trim(),
    `Mod：启用 ${data.mod_count || 0}，停用 ${data.disabled_mod_count || 0}`,
    `崩溃报告：${data.crash_reports?.length || 0}`,
    `磁盘：可用 ${data.storage?.free_gb ?? "—"} GB，已用 ${data.storage?.used_percent ?? "—"}%`,
    `最近错误：`,
    ...(data.error_lines || []).slice(-20),
  ].join("\n");
  try {
    await navigator.clipboard.writeText(summary);
    toast("诊断摘要已复制");
  } catch { toast("浏览器拒绝访问剪贴板，请使用 HTTPS 或本机地址", "error"); }
}

function bindEvents() {
  $("#theme-toggle").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  $("#mobile-menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#import-open").addEventListener("click", openImportModal);
  $("#empty-import").addEventListener("click", openImportModal);
  $$('[data-modal-close]').forEach((button) => button.addEventListener("click", closeImportModal));
  $("#import-modal").addEventListener("click", (event) => { if (event.target === event.currentTarget) closeImportModal(); });
  $("#import-form").addEventListener("submit", importServer);
  $("#pick-folder").addEventListener("click", pickFolder);
  $("#server-list").addEventListener("click", (event) => {
    const entry = event.target.closest(".server-entry");
    if (entry) selectServer(entry.dataset.serverId).catch((error) => toast(error.message, "error"));
  });
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  $$('[data-view-jump]').forEach((button) => button.addEventListener("click", () => showView(button.dataset.viewJump)));
  $$('[data-action]').forEach((button) => button.addEventListener("click", () => runAction(button.dataset.action, button)));
  $("#start-button").addEventListener("click", (event) => runAction(event.currentTarget.dataset.action || "start", event.currentTarget));
  $("#stop-button").addEventListener("click", (event) => runAction("stop", event.currentTarget));
  $("#log-refresh").addEventListener("click", () => { state.logCleared = false; refreshLogs(); });
  $("#log-clear").addEventListener("click", () => { state.logCleared = true; $("#console-output").textContent = "显示已清空；点击刷新可重新载入日志。"; });
  $("#command-form").addEventListener("submit", sendConsoleCommand);
  $("#command-input").addEventListener("keydown", commandHistoryKey);
  $("#rules-board").addEventListener("click", (event) => {
    const button = event.target.closest("[data-server-command]");
    if (button) runPresetCommand(button);
  });
  $("#broadcast-form").addEventListener("submit", sendBroadcast);
  $("#mod-search").addEventListener("input", renderMods);
  $("#mod-filter").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-mod-state]");
    if (!button) return;
    state.modFilter = button.dataset.modState;
    $$("button", event.currentTarget).forEach((item) => item.classList.toggle("active", item === button));
    renderMods();
  });
  $("#mod-table").addEventListener("click", (event) => {
    const button = event.target.closest("[data-mod-action]");
    if (button) modAction(button);
  });
  $("#mod-upload").addEventListener("change", (event) => uploadMods([...event.currentTarget.files]));
  $("#properties-form").addEventListener("submit", saveProperties);
  $("#launch-form").addEventListener("submit", saveLaunch);
  $("#accept-eula").addEventListener("click", acceptEula);
  $("#force-stop").addEventListener("click", forceStop);
  $("#remove-server").addEventListener("click", removeServer);
  $("#player-form").addEventListener("submit", submitPlayerAction);
  $("#backup-create").addEventListener("click", createBackup);
  $("#automation-save").addEventListener("click", saveAutomation);
  $("#diagnostic-refresh").addEventListener("click", refreshDiagnostics);
  $("#diagnostic-copy").addEventListener("click", copyDiagnosticSummary);
  $("#backup-table").addEventListener("click", (event) => {
    const button = event.target.closest("[data-backup-remove]");
    if (button) removeBackup(button);
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeImportModal(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refreshStatus(); refreshCurrentView(); } });
}

async function initialize() {
  initializeTheme();
  bindEvents();
  try {
    await bootstrap();
  } catch (error) {
    toast(error.message, "error", 8000);
  }
  state.timers.push(window.setInterval(() => refreshStatus(), 5000));
  state.timers.push(window.setInterval(() => refreshLogs(), 2200));
  state.timers.push(window.setInterval(() => refreshJobs(), 2800));
}

initialize();

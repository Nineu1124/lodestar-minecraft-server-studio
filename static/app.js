"use strict";

const state = {
  servers: [],
  activeId: null,
  status: null,
  mods: [],
  modFilter: "all",
  contentKind: "mods",
  filePath: "",
  files: [],
  editingFile: null,
  view: "dashboard",
  commandHistory: [],
  commandIndex: 0,
  logCleared: false,
  diagnostics: null,
  importSource: "archive",
  importInspection: null,
  importJobId: null,
  timers: [],
};

const COMMAND_LIBRARY = [
  { category: "状态", label: "在线玩家", command: "list", description: "列出在线玩家与人数" },
  { category: "状态", label: "性能采样", command: "forge tps", description: "Forge 服务端查看各维度 TPS" },
  { category: "状态", label: "种子", command: "seed", description: "显示当前世界种子" },
  { category: "世界", label: "保存世界", command: "save-all flush", description: "立即将所有区块写入磁盘" },
  { category: "世界", label: "开启保存", command: "save-on", description: "恢复世界自动保存" },
  { category: "世界", label: "暂停保存", command: "save-off", description: "暂时关闭自动保存，备份后记得恢复" },
  { category: "世界", label: "白天", command: "time set day", description: "将时间设为白天" },
  { category: "世界", label: "夜晚", command: "time set night", description: "将时间设为夜晚" },
  { category: "世界", label: "晴天", command: "weather clear", description: "清除雨雪和雷暴" },
  { category: "世界", label: "雷暴", command: "weather thunder", description: "切换为雷暴天气" },
  { category: "玩家", label: "设为 OP", command: "op <玩家名>", description: "授予管理员权限" },
  { category: "玩家", label: "取消 OP", command: "deop <玩家名>", description: "撤销管理员权限" },
  { category: "玩家", label: "踢出", command: "kick <玩家名> <原因>", description: "将玩家踢出服务器" },
  { category: "玩家", label: "封禁", command: "ban <玩家名> <原因>", description: "封禁玩家账号" },
  { category: "玩家", label: "解除封禁", command: "pardon <玩家名>", description: "解除玩家账号封禁" },
  { category: "玩家", label: "传送", command: "tp <玩家名> <目标玩家或 x y z>", description: "传送玩家" },
  { category: "玩家", label: "切换模式", command: "gamemode survival <玩家名>", description: "设置指定玩家游戏模式" },
  { category: "玩家", label: "给予物品", command: "give <玩家名> minecraft:<物品> <数量>", description: "给予玩家物品" },
  { category: "玩家", label: "清空背包", command: "clear <玩家名>", description: "清除玩家物品栏" },
  { category: "玩家", label: "经验值", command: "experience add <玩家名> <数量> points", description: "增加玩家经验" },
  { category: "名单", label: "白名单列表", command: "whitelist list", description: "显示当前白名单" },
  { category: "名单", label: "加入白名单", command: "whitelist add <玩家名>", description: "将玩家加入白名单" },
  { category: "名单", label: "移出白名单", command: "whitelist remove <玩家名>", description: "将玩家移出白名单" },
  { category: "名单", label: "重载白名单", command: "whitelist reload", description: "从文件重新载入白名单" },
  { category: "规则", label: "保留物品", command: "gamerule keepInventory true", description: "玩家死亡后保留物品" },
  { category: "规则", label: "关闭生物破坏", command: "gamerule mobGriefing false", description: "阻止生物破坏方块" },
  { category: "规则", label: "一人入睡", command: "gamerule playersSleepingPercentage 1", description: "一名玩家睡觉即可跳过夜晚" },
  { category: "规则", label: "关闭火焰蔓延", command: "gamerule doFireTick false", description: "阻止火焰继续蔓延" },
  { category: "规则", label: "普通难度", command: "difficulty normal", description: "将服务器设为普通难度" },
  { category: "规则", label: "困难难度", command: "difficulty hard", description: "将服务器设为困难难度" },
  { category: "公告", label: "广播消息", command: "say <消息>", description: "向所有在线玩家发送消息" },
  { category: "公告", label: "标题公告", command: "title @a title {\"text\":\"<标题>\",\"color\":\"gold\"}", description: "在所有玩家屏幕中央显示标题" },
  { category: "公告", label: "行动栏", command: "title @a actionbar {\"text\":\"<消息>\"}", description: "在快捷栏上方显示消息" },
  { category: "维护", label: "重载数据包", command: "reload", description: "重新载入数据包与函数" },
  { category: "维护", label: "数据包列表", command: "datapack list", description: "列出已启用和可用数据包" },
  { category: "维护", label: "世界边界", command: "worldborder set <直径>", description: "设置世界边界直径" },
  { category: "维护", label: "计划停服", command: "say 服务器将在 5 分钟后维护", description: "发送维护前公告" },
  { category: "维护", label: "正常停服", command: "stop", description: "保存世界后停止服务器" },
];

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
    state.commandHistory = JSON.parse(localStorage.getItem(`lodestar-command-${state.activeId}`) || "[]");
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
  localStorage.setItem("lodestar-theme", theme);
  const meta = $('meta[name="theme-color"]');
  if (meta) meta.content = theme === "dark" ? "#181816" : "#faf9f5";
  const toggle = $("#theme-toggle");
  if (toggle) toggle.setAttribute("aria-label", theme === "dark" ? "切换浅色模式" : "切换深色模式");
}

function initializeTheme() {
  const stored = localStorage.getItem("lodestar-theme") || localStorage.getItem("ripple-theme");
  const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  applyTheme(stored || (dark ? "dark" : "light"));
}

function openImportModal() {
  $("#import-modal").classList.remove("hidden");
  switchImportSource(state.importSource || "archive");
  window.setTimeout(() => $(state.importSource === "folder" ? "#import-path" : "#archive-path").focus(), 50);
}

function closeImportModal() {
  $("#import-modal").classList.add("hidden");
}

function setMobileMenu(open) {
  const sidebar = $("#sidebar");
  const button = $("#mobile-menu");
  sidebar.classList.toggle("open", open);
  document.body.classList.toggle("menu-open", open);
  button.setAttribute("aria-expanded", String(open));
  button.setAttribute("aria-label", open ? "关闭导航菜单" : "打开导航菜单");
}

function showView(view) {
  state.view = view;
  $$(".nav-item").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$(".view").forEach((panel) => panel.classList.toggle("active", panel.dataset.viewPanel === view));
  setMobileMenu(false);
  if (!state.activeId) return;
  if (view === "console") refreshLogs();
  if (view === "performance") refreshPerformance();
  if (view === "mods") refreshMods();
  if (view === "files") refreshFiles(state.filePath);
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
    $("#server-title").textContent = "Lodestar";
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
  if (state.view === "performance") return refreshPerformance();
  if (state.view === "mods") return refreshMods();
  if (state.view === "files") return refreshFiles(state.filePath);
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
  pill.classList.remove("online", "offline", "starting", "error");
  if (status?.ready) {
    pill.classList.add("online");
    $("span", pill).textContent = "运行中";
  } else if (status?.startup_failure) {
    pill.classList.add("error");
    $("span", pill).textContent = "启动失败";
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
  $("#start-button").textContent = status.startup_failure ? "结束失败进程" : status.running ? "重新启动" : "启动服务端";
  $("#start-button").dataset.action = status.startup_failure ? "force_stop" : status.running ? "restart" : "start";
  $("#stop-button").disabled = !status.running;
  const heroStart = $(".hero-actions .button.primary");
  if (heroStart) {
    heroStart.dataset.action = status.startup_failure ? "force_stop" : status.running ? "stop" : "start";
    heroStart.textContent = status.startup_failure ? "结束失败进程" : status.running ? "正常停服" : "启动";
  }

  $("#hero-title").textContent = status.ready ? "服务器正在运行。" : status.startup_failure ? "服务器启动失败。" : status.running ? "服务器正在启动。" : "服务器当前已停止。";
  $("#hero-subtitle").textContent = status.ready
    ? `${status.motd || "Minecraft Server"} · ${status.managed ? "由面板托管" : "已接管外部控制台"}`
    : status.startup_failure ? `${status.startup_failure.title} · ${status.startup_failure.detail}`
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

function renderMetricBars(container, points, key, maxValue, formatter) {
  const recent = points.slice(-72);
  const maximum = Math.max(1, Number(maxValue || 0), ...recent.map((point) => Number(point[key] || 0)));
  container.innerHTML = recent.map((point) => {
    const value = Number(point[key] || 0);
    const height = point.running ? Math.max(3, Math.min(100, value / maximum * 100)) : 1;
    return `<i class="${point.running ? "" : "offline"}" style="height:${height.toFixed(2)}%" title="${escapeHtml(formatDate(point.at))} · ${escapeHtml(formatter(value))}"></i>`;
  }).join("");
}

async function refreshPerformance(sample = true) {
  if (!state.activeId || document.hidden) return;
  const button = $("#performance-refresh");
  if (sample) setBusy(button, true, "采样中…");
  try {
    if (sample) await refreshStatus();
    const payload = await api(`/api/metrics?server_id=${encodeURIComponent(state.activeId)}`);
    const points = payload.data || [];
    const last = points.at(-1) || {};
    const cpuPeak = Math.max(0, ...points.map((point) => Number(point.cpu || 0)));
    const memoryPeak = Math.max(0, ...points.map((point) => Number(point.memory_mb || 0)));
    const playerPeak = Math.max(0, ...points.map((point) => Number(point.players || 0)));
    $("#performance-cpu-peak").textContent = `${cpuPeak.toFixed(1)}%`;
    $("#performance-memory-peak").textContent = formatBytesMB(memoryPeak);
    $("#performance-player-peak").textContent = String(playerPeak);
    $("#performance-samples").textContent = String(points.length);
    $("#performance-cpu-now").textContent = `${Number(last.cpu || 0).toFixed(1)}%`;
    $("#performance-memory-now").textContent = formatBytesMB(last.memory_mb || 0);
    $("#performance-players-now").textContent = String(last.players || 0);
    renderMetricBars($("#performance-cpu-chart"), points, "cpu", 100, (value) => `${value.toFixed(1)}%`);
    renderMetricBars($("#performance-memory-chart"), points, "memory_mb", memoryPeak, (value) => formatBytesMB(value));
    renderMetricBars($("#performance-player-chart"), points, "players", Math.max(1, state.status?.players?.max || playerPeak), (value) => `${value} 人`);
  } catch (error) { toast(error.message, "error"); }
  finally { if (sample) setBusy(button, false); }
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
  if (action === "force_stop" && sourceButton?.id !== "force-stop" && !window.confirm("服务端已确认启动失败。确定结束残留 Java 进程吗？")) return;
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
    localStorage.setItem(`lodestar-command-${state.activeId}`, JSON.stringify(state.commandHistory));
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

function renderCommandLibrary() {
  const search = ($("#command-search")?.value || "").trim().toLocaleLowerCase("zh-CN");
  const activeCategory = $("#command-categories .active")?.dataset.commandCategory || "全部";
  const categories = ["全部", ...new Set(COMMAND_LIBRARY.map((item) => item.category))];
  $("#command-categories").innerHTML = categories.map((category) => `<button type="button" data-command-category="${escapeHtml(category)}" class="${category === activeCategory ? "active" : ""}">${escapeHtml(category)}</button>`).join("");
  const visible = COMMAND_LIBRARY.filter((item) => {
    const matchesCategory = activeCategory === "全部" || item.category === activeCategory;
    const haystack = `${item.label} ${item.command} ${item.description} ${item.category}`.toLocaleLowerCase("zh-CN");
    return matchesCategory && (!search || haystack.includes(search));
  });
  $("#command-reference").innerHTML = visible.map((item) => `
    <button type="button" data-command-insert="${escapeHtml(item.command)}">
      <span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.description)}</small></span>
      <code>${escapeHtml(item.command)}</code>
    </button>`).join("") || '<div class="command-empty">没有匹配的指令</div>';
}

function insertCommand(command) {
  const input = $("#command-input");
  input.value = command;
  input.focus();
  const placeholder = input.value.indexOf("<");
  if (placeholder >= 0) {
    const end = input.value.indexOf(">", placeholder);
    input.setSelectionRange(placeholder, end >= 0 ? end + 1 : input.value.length);
  } else {
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

async function refreshMods() {
  if (!state.activeId || document.hidden) return;
  try {
    const payload = await api(`/api/mods?server_id=${encodeURIComponent(state.activeId)}&kind=${encodeURIComponent(state.contentKind)}`);
    state.mods = payload.data || [];
    renderMods();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderMods() {
  const isPlugin = state.contentKind === "plugins";
  $("#content-heading").textContent = isPlugin ? "插件管理" : "Mod 管理";
  $("#content-description").textContent = isPlugin ? "管理 Bukkit、Paper 与混合端插件；改动需要重启服务端生效。" : "管理 Forge、NeoForge 与 Fabric Mod；改动需要重启服务端生效。";
  $("#content-upload-label").textContent = isPlugin ? "添加插件" : "添加 Mod";
  $("#content-table-label").textContent = isPlugin ? "插件" : "Mod";
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
    const payload = await api("/api/mods/action", { method: "POST", body: { server_id: state.activeId, action, state: row.dataset.state, name, kind: state.contentKind } });
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
  const label = $("#content-upload-label");
  const original = label.textContent;
  try {
    let index = 0;
    for (const file of files) {
      index++;
      label.textContent = `上传 ${index}/${files.length}…`;
      const response = await fetch("/api/mods/upload", {
        method: "POST",
        headers: {
          "Content-Type": "application/java-archive",
          "X-Server-Id": state.activeId,
          "X-Filename": encodeURIComponent(file.name),
          "X-Content-Kind": state.contentKind,
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
    label.textContent = original;
    $("#mod-upload").value = "";
  }
}

function joinServerPath(folder, name) {
  return [folder, name].filter(Boolean).join("/");
}

async function refreshFiles(path = "") {
  if (!state.activeId || document.hidden) return;
  try {
    const payload = await api(`/api/files?server_id=${encodeURIComponent(state.activeId)}&path=${encodeURIComponent(path)}`);
    state.filePath = payload.data.path || "";
    state.files = payload.data.entries || [];
    renderFiles(payload.data.parent || "");
  } catch (error) { toast(error.message, "error"); }
}

function renderFiles(parent) {
  const parts = state.filePath ? state.filePath.split("/") : [];
  const crumbs = [{ label: "服务端根目录", path: "" }];
  parts.forEach((part, index) => crumbs.push({ label: part, path: parts.slice(0, index + 1).join("/") }));
  $("#file-breadcrumb").innerHTML = crumbs.map((crumb, index) => `<button type="button" data-file-path="${escapeHtml(crumb.path)}">${escapeHtml(crumb.label)}</button>${index < crumbs.length - 1 ? "<span>/</span>" : ""}`).join("");
  $("#file-up").disabled = !state.filePath;
  $("#file-up").dataset.filePath = parent;
  $("#file-table").innerHTML = state.files.map((entry) => {
    const icon = entry.type === "directory" ? "▱" : "·";
    const size = entry.type === "directory" ? "—" : formatFileSize(entry.size);
    const open = entry.type === "directory" ? `<button class="row-button" data-file-open>打开</button>` : entry.editable ? `<button class="row-button" data-file-edit>编辑</button>` : "";
    const download = entry.type === "file" ? `<a class="row-button" href="/api/file/download?server_id=${encodeURIComponent(state.activeId)}&path=${encodeURIComponent(entry.path)}">下载</a>` : "";
    return `<tr data-path="${escapeHtml(entry.path)}" data-name="${escapeHtml(entry.name)}" data-type="${entry.type}"><td><div class="file-name"><i>${icon}</i><strong>${escapeHtml(entry.name)}</strong></div></td><td>${size}</td><td>${escapeHtml(formatDate(entry.modified))}</td><td><div class="row-actions">${open}${download}<button class="row-button" data-file-rename>重命名</button><button class="row-button danger" data-file-trash>移入回收站</button></div></td></tr>`;
  }).join("");
  $("#file-empty").classList.toggle("hidden", state.files.length !== 0);
}

async function openTextFile(path) {
  const payload = await api(`/api/file/content?server_id=${encodeURIComponent(state.activeId)}&path=${encodeURIComponent(path)}`);
  state.editingFile = payload.data.path;
  $("#file-editor-title").textContent = payload.data.path;
  $("#file-editor-content").value = payload.data.content;
  $("#file-editor").classList.remove("hidden");
  $("#file-editor").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function saveTextFile() {
  if (!state.editingFile) return;
  const button = $("#file-editor-save");
  setBusy(button, true, "保存中…");
  try {
    const payload = await api("/api/file/save", { method: "POST", body: { server_id: state.activeId, path: state.editingFile, content: $("#file-editor-content").value } });
    toast(payload.message);
    await refreshFiles(state.filePath);
  } catch (error) { toast(error.message, "error", 6500); }
  finally { setBusy(button, false); }
}

async function fileAction(action, path, name = "") {
  const payload = await api("/api/file/action", { method: "POST", body: { server_id: state.activeId, action, path, name } });
  toast(payload.message);
  await refreshFiles(state.filePath);
}

async function uploadServerFiles(files) {
  if (!files.length || !state.activeId) return;
  const label = $("#file-upload-label");
  const original = label.textContent;
  try {
    for (let index = 0; index < files.length; index++) {
      const file = files[index];
      label.textContent = `上传 ${index + 1}/${files.length}…`;
      const relative = joinServerPath(state.filePath, file.name);
      const response = await fetch("/api/file/upload", { method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Server-Id": state.activeId, "X-Relative-Path": encodeURIComponent(relative) }, body: file });
      const payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.error || `上传 ${file.name} 失败`);
      toast(payload.message);
    }
    await refreshFiles(state.filePath);
  } catch (error) { toast(error.message, "error", 6500); }
  finally { label.textContent = original; $("#file-upload").value = ""; }
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
    tbody.innerHTML = (payload.data || []).map((backup) => `<tr data-name="${escapeHtml(backup.name)}"><td><div class="mod-name"><span class="mod-gem">◴</span><span><strong>${escapeHtml(backup.name)}</strong><small>世界与关键配置</small></span></div></td><td>${escapeHtml(formatDate(backup.modified))}</td><td>${Number(backup.size_mb).toFixed(2)} MB</td><td><div class="row-actions"><a class="row-button" href="/api/backup/download?server_id=${encodeURIComponent(state.activeId)}&name=${encodeURIComponent(backup.name)}">下载</a><button class="row-button" data-backup-restore>恢复</button><button class="row-button danger" data-backup-remove>移除</button></div></td></tr>`).join("");
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

async function restoreBackup(button) {
  const name = button.closest("tr").dataset.name;
  if (state.status?.running) return toast("恢复前请先正常停止服务器", "error", 6000);
  if (!window.confirm(`确认恢复 ${name}？当前世界与同名配置会先保存到恢复历史。`)) return;
  setBusy(button, true, "准备恢复…");
  try {
    const payload = await api("/api/backups", { method: "POST", body: { server_id: state.activeId, action: "restore", name } });
    toast(payload.message);
    $("#backup-job").classList.remove("hidden");
    await refreshJobs();
  } catch (error) { toast(error.message, "error", 6500); }
  finally { setBusy(button, false); }
}

async function refreshJobs() {
  if (!state.activeId || state.view !== "backups") return;
  try {
    const payload = await api("/api/jobs");
    const jobs = payload.data.filter((job) => job.server_id === state.activeId && ["backup", "restore"].includes(job.type));
    const job = jobs.at(-1);
    const card = $("#backup-job");
    if (!job || (job.state === "done" && Date.now() - new Date(job.created_at).getTime() > 120000)) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");
    const restoring = job.type === "restore";
    $("#job-title").textContent = job.state === "error" ? (restoring ? "恢复失败" : "备份失败") : job.state === "done" ? (restoring ? "恢复完成" : "备份完成") : (restoring ? "正在恢复" : "正在备份");
    $("#job-message").textContent = job.message;
    $("#job-progress").style.width = `${job.state === "done" ? 100 : job.progress || 12}%`;
    if (job.state === "done") refreshBackups();
  } catch { /* 定时刷新不打扰用户 */ }
}

function switchImportSource(source) {
  state.importSource = source;
  $$('[data-import-source]').forEach((button) => button.classList.toggle("active", button.dataset.importSource === source));
  $$('[data-import-panel]').forEach((panel) => {
    const active = panel.dataset.importPanel === source;
    panel.classList.toggle("hidden", !active);
    $$('input, select, button', panel).forEach((control) => {
      if (!control.closest(".modal-actions")) control.disabled = !active || Boolean(state.importJobId);
    });
  });
  const submit = $("#import-submit");
  if (!state.importJobId) submit.textContent = source === "folder" ? "检测并导入" : state.importInspection ? "导入并开服" : "检查压缩包";
}

function formatFileSize(bytes) {
  const value = Number(bytes || 0);
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(0, value / 1024).toFixed(1)} KB`;
}

function renderArchiveInspection(data) {
  state.importInspection = data;
  const summary = $("#archive-summary");
  summary.innerHTML = `
    <article><span>识别结果</span><strong>${escapeHtml(String(data.loader || "unknown").toUpperCase())} · ${escapeHtml(data.version || "未知")}</strong></article>
    <article><span>服务端目录</span><strong>${escapeHtml(data.root || "压缩包根目录")}</strong></article>
    <article><span>解压体积</span><strong>${escapeHtml(formatFileSize(data.expanded_size))}</strong></article>
    <article><span>文件数量</span><strong>${Number(data.file_count || 0).toLocaleString("zh-CN")}</strong></article>`;
  const form = $("#import-form");
  form.elements.archive_name.value = data.name || "Minecraft Server";
  form.elements.destination.value = data.destination || "";
  const javaSelect = $("#archive-java");
  javaSelect.innerHTML = (data.java_runtimes || []).map((runtime) => `<option value="${escapeHtml(runtime.path)}" ${runtime.path === data.recommended_java ? "selected" : ""}>${escapeHtml(runtime.label)} · ${escapeHtml(runtime.path)}</option>`).join("");
  if (!javaSelect.options.length) javaSelect.innerHTML = '<option value="java">系统 Java</option>';
  const launch = $("#archive-launch");
  const recommendedValue = data.recommended_launch ? `${data.recommended_launch.mode}|${data.recommended_launch.target}` : "";
  launch.innerHTML = (data.launch_candidates || []).map((item, index) => {
    const value = `${item.mode}|${item.target}`;
    return `<option value="${escapeHtml(value)}" ${value === recommendedValue || (!recommendedValue && index === 0) ? "selected" : ""}>${escapeHtml(item.label)}${value === recommendedValue ? " · 推荐" : ""}</option>`;
  }).join("");
  $("#archive-review").classList.remove("hidden");
  $("#import-submit").textContent = "导入并开服";
}

async function inspectArchive() {
  const path = $("#archive-path").value.trim();
  if (!path) throw new Error("请先选择服务端压缩包");
  const payload = await api("/api/import/archive/inspect", { method: "POST", body: { path } });
  renderArchiveInspection(payload.data);
}

function updateImportProgress(job) {
  const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
  $("#import-progress").classList.remove("hidden");
  $("#import-progress-label").textContent = job.message || "正在导入…";
  $("#import-progress-value").textContent = `${progress}%`;
  $("#import-progress-bar").style.width = `${progress}%`;
}

async function waitForImport(jobId) {
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 900));
    const payload = await api("/api/jobs");
    const job = (payload.data || []).find((item) => item.id === jobId);
    if (!job) throw new Error("面板没有找到导入任务");
    updateImportProgress(job);
    if (job.state === "error") throw new Error(job.error || job.message || "导入失败");
    if (job.state === "done") return job;
  }
}

async function importArchive() {
  if (!state.importInspection || state.importInspection.path !== $("#archive-path").value.trim()) {
    await inspectArchive();
    return null;
  }
  const form = $("#import-form");
  const [launchMode, ...targetParts] = form.elements.archive_launch.value.split("|");
  const body = {
    archive_path: state.importInspection.path,
    destination: form.elements.destination.value.trim(),
    name: form.elements.archive_name.value.trim(),
    java: form.elements.archive_java.value,
    xms: form.elements.archive_xms.value.trim(),
    xmx: form.elements.archive_xmx.value.trim(),
    launch_mode: launchMode,
    launch_target: targetParts.join("|"),
    accept_eula: form.elements.accept_eula.checked,
    start_after_import: form.elements.start_after_import.checked,
    properties: {
      "server-port": Number(form.elements.server_port.value),
      "max-players": Number(form.elements.max_players.value),
      "view-distance": Number(form.elements.view_distance.value),
      "simulation-distance": Number(form.elements.simulation_distance.value),
      "gamemode": form.elements.gamemode.value,
      "difficulty": form.elements.difficulty.value,
      "online-mode": form.elements.online_mode.checked,
      "enforce-secure-profile": form.elements.secure_profile.checked,
      "allow-flight": form.elements.allow_flight.checked,
      "white-list": form.elements.whitelist.checked,
      "enforce-whitelist": form.elements.whitelist.checked,
    },
  };
  const payload = await api("/api/import/archive/execute", { method: "POST", body });
  state.importJobId = payload.job.id;
  updateImportProgress(payload.job);
  switchImportSource("archive");
  return waitForImport(payload.job.id);
}

async function importServer(event) {
  event.preventDefault();
  const button = $("button[type='submit']", event.currentTarget);
  setBusy(button, true, state.importSource === "archive" && state.importInspection ? "正在导入…" : "正在检测…");
  try {
    if (state.importSource === "archive") {
      const job = await importArchive();
      if (!job) return;
      toast(job.message || "服务端已导入");
      await bootstrap(job.server_id);
      closeImportModal();
    } else {
      const form = event.currentTarget;
      const data = {
        path: form.elements.path.value,
        name: form.elements.name.value,
        java: form.elements.java.value,
        xms: form.elements.xms.value,
        xmx: form.elements.xmx.value,
      };
      const payload = await api("/api/servers/import", { method: "POST", body: data });
      toast(payload.message);
      closeImportModal();
      await bootstrap(payload.profile.id);
    }
  } catch (error) { toast(error.message, "error", 6500); }
  finally {
    state.importJobId = null;
    setBusy(button, false);
    switchImportSource(state.importSource);
  }
}

async function pickArchive() {
  const button = $("#pick-archive");
  setBusy(button, true, "等待选择…");
  try {
    const payload = await api("/api/import/archive/pick", { method: "POST", body: {} });
    if (payload.path) {
      $("#archive-path").value = payload.path;
      state.importInspection = null;
      $("#archive-review").classList.add("hidden");
      await inspectArchive();
    }
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
  const diagnosis = data.diagnosis;
  $("#diagnosis-card").classList.toggle("hidden", !diagnosis);
  if (diagnosis) {
    $("#diagnosis-title").textContent = diagnosis.title;
    $("#diagnosis-detail").textContent = diagnosis.detail;
    $("#diagnosis-suggestions").innerHTML = (diagnosis.suggestions || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
    $("#diagnosis-source").textContent = `依据 · ${diagnosis.source || "logs/latest.log"}`;
  }
  const preflight = data.preflight || { ready: false, checks: [] };
  $("#preflight-state").textContent = preflight.ready ? "可以启动" : "需要处理";
  $("#preflight-state").className = `state-badge ${preflight.ready ? "enabled" : "disabled"}`;
  $("#preflight-list").innerHTML = (preflight.checks || []).map((check) => `<article class="${escapeHtml(check.level)}"><i></i><div><strong>${escapeHtml(check.title)}</strong><span>${escapeHtml(check.detail)}</span></div></article>`).join("");

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
    `Lodestar 诊断摘要`,
    `服务端：${server?.name || "未知"}`,
    `生成时间：${formatDate(data.generated_at)}`,
    `Java：${data.java_version || "无法读取"}`,
    `加载器：${data.loader || "unknown"} ${data.game_version || ""}`.trim(),
    `Mod：启用 ${data.mod_count || 0}，停用 ${data.disabled_mod_count || 0}`,
    `崩溃报告：${data.crash_reports?.length || 0}`,
    `磁盘：可用 ${data.storage?.free_gb ?? "—"} GB，已用 ${data.storage?.used_percent ?? "—"}%`,
    ...(data.diagnosis ? [
      `自动结论：${data.diagnosis.title}`,
      `原因：${data.diagnosis.detail}`,
      ...((data.diagnosis.suggestions || []).map((item) => `建议：${item}`)),
    ] : []),
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
  $("#mobile-menu").addEventListener("click", () => setMobileMenu(!$("#sidebar").classList.contains("open")));
  $("#mobile-scrim").addEventListener("click", () => setMobileMenu(false));
  $("#import-open").addEventListener("click", openImportModal);
  $("#empty-import").addEventListener("click", openImportModal);
  $$('[data-modal-close]').forEach((button) => button.addEventListener("click", closeImportModal));
  $("#import-modal").addEventListener("click", (event) => { if (event.target === event.currentTarget) closeImportModal(); });
  $("#import-form").addEventListener("submit", importServer);
  $$("[data-import-source]").forEach((button) => button.addEventListener("click", () => switchImportSource(button.dataset.importSource)));
  $("#pick-archive").addEventListener("click", pickArchive);
  $("#archive-path").addEventListener("input", () => {
    state.importInspection = null;
    $("#archive-review").classList.add("hidden");
    $("#import-submit").textContent = "检查压缩包";
  });
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
  $("#performance-refresh").addEventListener("click", () => refreshPerformance());
  $("#log-clear").addEventListener("click", () => { state.logCleared = true; $("#console-output").textContent = "显示已清空；点击刷新可重新载入日志。"; });
  $("#command-form").addEventListener("submit", sendConsoleCommand);
  $("#command-input").addEventListener("keydown", commandHistoryKey);
  $("#command-search").addEventListener("input", renderCommandLibrary);
  $("#command-categories").addEventListener("click", (event) => {
    const button = event.target.closest("[data-command-category]");
    if (!button) return;
    $$("button", event.currentTarget).forEach((item) => item.classList.toggle("active", item === button));
    renderCommandLibrary();
  });
  $("#command-reference").addEventListener("click", (event) => {
    const button = event.target.closest("[data-command-insert]");
    if (button) insertCommand(button.dataset.commandInsert);
  });
  $("#rules-board").addEventListener("click", (event) => {
    const button = event.target.closest("[data-server-command]");
    if (button) runPresetCommand(button);
  });
  $("#broadcast-form").addEventListener("submit", sendBroadcast);
  $("#mod-search").addEventListener("input", renderMods);
  $("#content-kind-switch").addEventListener("click", (event) => {
    const button = event.target.closest("[data-content-kind]");
    if (!button || button.dataset.contentKind === state.contentKind) return;
    state.contentKind = button.dataset.contentKind;
    state.modFilter = "all";
    $$("button", event.currentTarget).forEach((item) => item.classList.toggle("active", item === button));
    $$("button", $("#mod-filter")).forEach((item) => item.classList.toggle("active", item.dataset.modState === "all"));
    refreshMods();
  });
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
  $("#file-refresh").addEventListener("click", () => refreshFiles(state.filePath));
  $("#file-up").addEventListener("click", (event) => refreshFiles(event.currentTarget.dataset.filePath || ""));
  $("#file-breadcrumb").addEventListener("click", (event) => {
    const button = event.target.closest("[data-file-path]");
    if (button) refreshFiles(button.dataset.filePath);
  });
  $("#file-new-folder").addEventListener("click", async () => {
    const name = window.prompt("新文件夹名称");
    if (!name) return;
    try { await fileAction("mkdir", state.filePath, name); } catch (error) { toast(error.message, "error"); }
  });
  $("#file-upload").addEventListener("change", (event) => uploadServerFiles([...event.currentTarget.files]));
  $("#file-table").addEventListener("click", async (event) => {
    const row = event.target.closest("tr[data-path]");
    if (!row) return;
    try {
      if (event.target.closest("[data-file-open]")) await refreshFiles(row.dataset.path);
      else if (event.target.closest("[data-file-edit]")) await openTextFile(row.dataset.path);
      else if (event.target.closest("[data-file-rename]")) {
        const name = window.prompt("输入新名称", row.dataset.name);
        if (name && name !== row.dataset.name) await fileAction("rename", row.dataset.path, name);
      } else if (event.target.closest("[data-file-trash]")) {
        if (window.confirm(`把 ${row.dataset.name} 移入面板回收站吗？`)) await fileAction("trash", row.dataset.path);
      }
    } catch (error) { toast(error.message, "error", 6500); }
  });
  $("#file-editor-save").addEventListener("click", saveTextFile);
  $("#file-editor-close").addEventListener("click", () => { state.editingFile = null; $("#file-editor").classList.add("hidden"); });
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
    const restore = event.target.closest("[data-backup-restore]");
    if (restore) return restoreBackup(restore);
    const button = event.target.closest("[data-backup-remove]");
    if (button) removeBackup(button);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeImportModal();
    setMobileMenu(false);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refreshStatus(); refreshCurrentView(); } });
}

async function initialize() {
  initializeTheme();
  bindEvents();
  renderCommandLibrary();
  try {
    await bootstrap();
  } catch (error) {
    toast(error.message, "error", 8000);
  }
  state.timers.push(window.setInterval(() => refreshStatus(), 5000));
  state.timers.push(window.setInterval(() => refreshLogs(), 2200));
  state.timers.push(window.setInterval(() => refreshJobs(), 2800));
  state.timers.push(window.setInterval(() => { if (state.view === "performance") refreshPerformance(false); }, 5000));
}

initialize();

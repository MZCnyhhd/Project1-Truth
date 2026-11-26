const $ = (selector) => document.querySelector(selector);

let currentTaskId = null;
let currentFilepath = "";
let pollTimer = null;
let loginTimer = null;
let platformTimer = null;
let currentPlatform = null;
let loggedIn = false;
let hasUrl = false;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function extractUrl(text) {
  const match = String(text || "").match(/https?:\/\/[^\s\u4e00-\u9fff，。；、！？）)】]+/);
  return match ? match[0] : "";
}

function basename(path) {
  return String(path || "").split(/[\\/]/).pop() || "";
}

function mediaUrl(path) {
  return "/media/" + encodeURIComponent(basename(path));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({
    ok: false,
    error: "服务响应解析失败",
  }));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || payload.detail || `HTTP ${response.status}`);
  }
  return payload.data;
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), 3000);
}

function setStatus(stage, progress, tag) {
  $("#status-text").textContent = stage;
  $("#status-tag").textContent = tag;
  $("#progress-fill").style.width = `${Math.max(0, Math.min(100, Number(progress) || 0))}%`;
}

function updateControls() {
  const needLogin = Boolean(currentPlatform && currentPlatform.login_required);
  const ready = hasUrl && (!needLogin || loggedIn);
  $("#download-btn").disabled = !ready;
  $("#login-btn").classList.toggle("hidden", !needLogin);
}

function resetMedia() {
  const player = $("#player");
  player.pause();
  player.removeAttribute("src");
  player.load();
  player.classList.add("hidden");
  $("#result-actions").classList.add("hidden");
  $("#explain-box").classList.add("hidden");
  $("#explain-text").textContent = "";
  $("#explain-title").textContent = "视频文字";
  currentFilepath = "";
}

function showFile(filepath, title) {
  if (!filepath) return;
  const url = mediaUrl(filepath);
  const name = basename(filepath);
  currentFilepath = filepath;

  $("#result").classList.remove("hidden");
  $("#error").classList.add("hidden");
  $("#status-tag").style.color = "var(--success)";
  setStatus(title || "已就绪", 100, "完成");
  $("#detail").textContent = title || name;

  const player = $("#player");
  player.src = url;
  player.classList.remove("hidden");

  $("#result-actions").classList.remove("hidden");
  $("#explain-box").classList.add("hidden");
  $("#explain-title").textContent = "视频文字";
}

async function refreshAuth() {
  if (!currentPlatform) return;
  if (!currentPlatform.login_required) {
    loggedIn = false;
    $("#platform-text").textContent = `${currentPlatform.name} · 可直接下载`;
    $("#auth-dot").className = "dot online";
    updateControls();
    return;
  }
  try {
    const state = await api(`/api/auth/status?platform=${encodeURIComponent(currentPlatform.key)}`);
    loggedIn = Boolean(state.logged_in);
    $("#platform-text").textContent = `${currentPlatform.name} · ${state.message}`;
    $("#auth-dot").className = "dot " + (loggedIn ? "online" : "offline");
    $("#login-btn").textContent = loggedIn ? "重新登录" : "扫码登录";
    $("#login-tip").textContent = `在弹出的 Edge 窗口里用${currentPlatform.name} App 扫码`;
    updateControls();
  } catch (error) {
    console.error(error);
  }
}

async function detectPlatform() {
  const url = extractUrl($("#url-input").value.trim());
  hasUrl = Boolean(url);
  if (!url) {
    currentPlatform = null;
    loggedIn = false;
    $("#platform-text").textContent = "粘贴链接后自动识别平台";
    $("#auth-dot").className = "dot offline";
    $("#login-btn").classList.add("hidden");
    updateControls();
    return;
  }
  try {
    const info = await api("/api/platform?url=" + encodeURIComponent(url));
    currentPlatform = info;
    if (info.login_required) {
      $("#platform-text").textContent = `${info.name} · 检查登录状态…`;
    }
    await refreshAuth();
  } catch (error) {
    console.error(error);
  }
}

async function poll() {
  if (!currentTaskId) return;
  try {
    const task = await api(`/api/tasks/${currentTaskId}`);
    const tagMap = { queued: "排队中", running: "下载中", done: "完成", error: "失败" };
    const tag = tagMap[task.status] || task.status;
    $("#status-tag").style.color = "var(--muted)";
    setStatus(task.stage || tag, task.progress, tag);
    $("#detail").textContent = task.title || "";

    if (task.status === "done") {
      clearInterval(pollTimer);
      pollTimer = null;
      updateControls();
      showFile(task.filepath, task.title);
      if (task.elapsed) {
        $("#status-tag").textContent = `完成 · ${task.elapsed}s`;
      }
    } else if (task.status === "error") {
      clearInterval(pollTimer);
      pollTimer = null;
      updateControls();
      $("#status-tag").style.color = "var(--danger)";
      $("#error").textContent = task.message || "下载失败";
      $("#error").classList.remove("hidden");
      if ((task.message || "").includes("登录")) refreshAuth();
    }
  } catch (error) {
    console.error(error);
  }
}

async function checkLogin() {
  if (!currentPlatform) return;
  try {
    const state = await api(`/api/auth/status?platform=${encodeURIComponent(currentPlatform.key)}`);
    $("#login-status").textContent = state.login_message || state.message;
    if (state.status === "success") {
      stopLoginPolling();
      $("#login-panel").classList.add("hidden");
      toast("登录成功");
      await refreshAuth();
    } else if (["error", "timeout", "idle"].includes(state.status)) {
      stopLoginPolling();
      $("#login-panel").classList.add("hidden");
      if (state.login_message) toast(state.login_message, true);
      await refreshAuth();
    }
  } catch (error) {
    console.error(error);
  }
}

function stopLoginPolling() {
  if (loginTimer) {
    clearInterval(loginTimer);
    loginTimer = null;
  }
  $("#login-btn").disabled = false;
}

async function loadHistory() {
  const list = $("#history-list");
  list.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const items = await api("/api/history");
    if (!items.length) {
      list.innerHTML = '<div class="empty">还没有下载记录</div>';
      return;
    }
    list.innerHTML = items
      .map(
        (item) => `
        <button class="history-item" data-file="${escapeHtml(item.filepath)}" data-title="${escapeHtml(item.title || "")}">
          <span class="history-title">${escapeHtml(item.title || "未命名视频")}</span>
          <span class="history-meta">${escapeHtml(item.extractor || "")} · ${escapeHtml((item.created_at || "").replace("T", " "))}</span>
        </button>`
      )
      .join("");
  } catch (error) {
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function openHistory() {
  $("#history-mask").classList.remove("hidden");
  $("#history-drawer").classList.remove("hidden");
  loadHistory();
}

function closeHistory() {
  $("#history-mask").classList.add("hidden");
  $("#history-drawer").classList.add("hidden");
}

$("#url-input").addEventListener("input", () => {
  clearTimeout(platformTimer);
  platformTimer = setTimeout(detectPlatform, 350);
});

$("#login-btn").addEventListener("click", async () => {
  if (!currentPlatform) return;
  try {
    await api("/api/auth/login/start", {
      method: "POST",
      body: JSON.stringify({ platform: currentPlatform.key }),
    });
    $("#login-btn").disabled = true;
    $("#login-panel").classList.remove("hidden");
    $("#login-status").textContent = "正在打开浏览器…";
    if (!loginTimer) loginTimer = setInterval(checkLogin, 1500);
  } catch (error) {
    toast(error.message, true);
  }
});

$("#login-cancel-btn").addEventListener("click", async () => {
  stopLoginPolling();
  try {
    await api("/api/auth/login/cancel", {
      method: "POST",
      body: JSON.stringify({ platform: currentPlatform?.key || "douyin" }),
    });
  } catch (error) {
    console.error(error);
  }
  $("#login-panel").classList.add("hidden");
});

$("#download-btn").addEventListener("click", async () => {
  const url = extractUrl($("#url-input").value.trim());
  if (!url) {
    toast("没找到链接，请粘贴完整的视频链接", true);
    return;
  }
  if (currentPlatform?.login_required && !loggedIn) {
    toast(`请先扫码登录${currentPlatform.name}`, true);
    return;
  }

  $("#download-btn").disabled = true;
  $("#result").classList.remove("hidden");
  $("#error").classList.add("hidden");
  $("#error").textContent = "";
  resetMedia();
  setStatus("正在提交…", 0, "排队中");

  try {
    const task = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    currentTaskId = task.id;
    await poll();
    if (!pollTimer) pollTimer = setInterval(poll, 900);
  } catch (error) {
    updateControls();
    setStatus("提交失败", 0, "失败");
    $("#status-tag").style.color = "var(--danger)";
    $("#error").textContent = error.message;
    $("#error").classList.remove("hidden");
  }
});

$("#history-btn").addEventListener("click", openHistory);
$("#history-close").addEventListener("click", closeHistory);
$("#history-mask").addEventListener("click", closeHistory);

$("#history-list").addEventListener("click", (event) => {
  const item = event.target.closest(".history-item");
  if (!item) return;
  showFile(item.dataset.file, item.dataset.title || "");
  closeHistory();
});

$("#explain-btn").addEventListener("click", async () => {
  if (!currentFilepath) return;
  const button = $("#explain-btn");
  button.disabled = true;
  button.textContent = "转写中…";
  $("#explain-box").classList.remove("hidden");
  $("#explain-title").textContent = "视频文字";
  $("#explain-text").textContent = "正在识别画面文字和语音，长视频可能需要一分钟…";
  try {
    const data = await api("/api/explain", {
      method: "POST",
      body: JSON.stringify({ filepath: currentFilepath }),
    });
    const node = $("#explain-text");
    node.style.color = "";
    node.textContent = data.text || "";
    $("#explain-title").textContent = data.elapsed ? `视频文字 · ${data.elapsed}s` : "视频文字";
  } catch (error) {
    const node = $("#explain-text");
    node.style.color = "var(--danger)";
    node.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "解释视频";
  }
});

$("#explain-copy").addEventListener("click", async () => {
  const text = $("#explain-text").textContent;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    toast("文字已复制");
  } catch {
    toast("复制失败", true);
  }
});

detectPlatform();

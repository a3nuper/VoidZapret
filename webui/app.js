/* VoidZapret WebView — фронт-логика и мост к Python (pywebview.api). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const body = document.body;
  let api = null;
  let appVer = "3.2.4";

  function call(method, ...args) {
    if (api && api[method]) { try { return api[method](...args); } catch (e) {} }
    return Promise.resolve();
  }
  function setText(el, t) {
    if (!el || el.textContent === t) return;
    el.classList.add("fade-out");
    setTimeout(() => { el.textContent = t; el.classList.remove("fade-out"); }, 150);
  }
  function bump(el) { el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump"); }

  // ---------------- роутер ----------------
  const navBtns = document.querySelectorAll(".nav-btn");
  function showView(name) {
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
    navBtns.forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    if (name === "warp") warpRefreshNow();
    if (name === "dns") dnsRefreshNow();
    if (name === "settings") zapretUpdateCheck();
  }
  navBtns.forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

  // ---------------- главная ----------------
  $("power").addEventListener("click", () => call("toggle"));
  $("find").addEventListener("click", () => call("find_best"));

  // ---------------- журнал ----------------
  const consoleEl = $("console");
  function logLine(text, tag) {
    const div = document.createElement("div");
    div.className = "line " + (tag || "system");
    div.textContent = text;
    consoleEl.appendChild(div);
    while (consoleEl.childElementCount > 4000) consoleEl.removeChild(consoleEl.firstChild);
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }
  $("log-clear").addEventListener("click", () => (consoleEl.innerHTML = ""));

  // ---------------- WARP ----------------
  const warpOrb = $("warp-orb");
  warpOrb.addEventListener("click", () => call("warp_toggle"));
  $("warp-dl").addEventListener("click", () => call("warp_download"));
  function applyWarp(d) {
    const c = d && d.connected;
    warpOrb.dataset.state = c ? "running" : "off";
    setText($("warp-status"), c ? "Подключён" : "Отключён");
    setText($("warp-sub"), c ? "Защищённый туннель активен · нажмите, чтобы отключить"
                             : "Нажмите, чтобы подключить");
  }
  async function warpRefreshNow() { const d = await call("warp_refresh"); applyWarp(d); }
  setInterval(() => { if ($("view-warp").classList.contains("active")) warpRefreshNow(); }, 3000);

  // ---------------- DNS ----------------
  function markDns(provider) {
    document.querySelectorAll(".dns-row").forEach((r) =>
      r.classList.toggle("active", r.dataset.provider === provider));
  }
  function setForceText(on) {
    $("dns-force-status").textContent = on
      ? "Принудительный DNS включён · Google DNS + OpenDNS"
      : "Принудительный DNS отключён";
  }
  $("dns-force").addEventListener("change", (e) => {
    const on = e.target.checked;            // мгновенный отклик UI
    setForceText(on);
    call("dns_force", on);                  // применение — в фоне (provider не трогаем)
  });
  $("dns-reset").addEventListener("click", () => {
    $("dns-force").checked = false; setForceText(false); markDns("dhcp");
    call("dns_reset");
  });
  document.querySelectorAll(".dns-row").forEach((row) => {
    row.addEventListener("click", () => {
      markDns(row.dataset.provider);        // переключаем сразу, не ждём PowerShell
      $("dns-force").checked = false; setForceText(false);  // выбор снимает «принудительный»
      call("dns_set", row.dataset.provider);
    });
  });
  function applyDns(d) {
    if (!d) return;
    $("dns-force").checked = !!d.force;
    setForceText(!!d.force);
    markDns(d.provider || "dhcp");
  }
  async function dnsRefreshNow() { const d = await call("dns_status"); applyDns(d); }

  // ---------------- настройки ----------------
  function bindToggle(id, method) {
    $(id).addEventListener("change", (e) => call(method, e.target.checked));
  }
  bindToggle("set-autostart", "set_autostart");
  bindToggle("set-autoconnect", "set_autoconnect");
  bindToggle("set-autoupdate", "set_autoupdate");
  bindToggle("set-tray", "set_tray");
  $("upd-btn").addEventListener("click", () => {
    if ($("upd-btn").disabled) return;
    $("upd-btn").disabled = true; $("upd-btn").textContent = "Обновление…";
    $("upd-progress").classList.add("show"); $("upd-bar").style.width = "0%";
    $("upd-text").style.color = ""; $("upd-text").textContent = "Запуск…";
    call("update_zapret");
  });
  // Кнопка «Обновить» неактивна, если новой версии zapret от Flowseal нет.
  async function zapretUpdateCheck() {
    const b = $("upd-btn");
    b.disabled = true; b.textContent = "Проверка…";
    const d = await call("check_zapret_update");
    if (d && d.available) {
      b.disabled = false; b.textContent = "Обновить";
      $("upd-ver").textContent = `Доступна версия ${d.latest} (текущая ${d.current})`;
    } else {
      b.disabled = true; b.textContent = "Актуально";
      $("upd-ver").textContent = `Установлена последняя версия: ${d ? d.current : "—"}`;
    }
  }
  // обновление самого приложения
  $("app-upd-btn").addEventListener("click", async () => {
    const btn = $("app-upd-btn"), t = $("app-upd-text");
    btn.disabled = true; t.style.color = ""; t.textContent = "Проверка…";
    const d = await call("check_app_update");
    if (d && d.available) {
      t.textContent = `Новая версия ${d.latest} — загрузка…`;
      call("app_update_now");           // прогресс → onAppUpdate, затем перезапуск
    } else {
      t.textContent = d ? "Установлена последняя версия" : "Не удалось проверить";
      btn.disabled = false;
    }
  });

  // ---------------- панель «Дополнительно» ----------------
  function applyAdv(d) {
    if (!d) return;
    $("adv-quic").checked = !!d.quic;
  }
  async function advRefresh() { applyAdv(await call("advanced_status")); }
  function openAdv() { $("adv-drawer").classList.add("open"); $("adv-backdrop").classList.add("open"); advRefresh(); }
  function closeAdv() { $("adv-drawer").classList.remove("open"); $("adv-backdrop").classList.remove("open"); }
  $("adv-open").addEventListener("click", openAdv);
  $("adv-close").addEventListener("click", closeAdv);
  $("adv-backdrop").addEventListener("click", closeAdv);
  $("adv-quic").addEventListener("change", async (e) => applyAdv(await call("set_quic", e.target.checked)));

  // ---------------- попапы чипов (Соединение / Пинг) ----------------
  const backdrop = $("modal-backdrop");
  let connTimer = null;
  let pingModalOpen = false;
  function closeModals() {
    $("conn-modal").classList.remove("open");
    $("ping-modal").classList.remove("open");
    backdrop.classList.remove("open");
    if (connTimer) { clearInterval(connTimer); connTimer = null; }
    pingModalOpen = false;
  }
  backdrop.addEventListener("click", closeModals);
  document.querySelectorAll("[data-modal-close]").forEach((b) => b.addEventListener("click", closeModals));

  // — Соединение —
  async function connRefresh() {
    const d = await call("connection_details");
    const list = $("conn-list");
    if (!d || !d.length) { list.innerHTML = '<div class="conn-empty">Нет данных</div>'; return; }
    list.innerHTML = d.map((s) =>
      `<div class="conn-row"><span class="conn-dot ${s.ok ? "ok" : "bad"}"></span>` +
      `<span class="conn-name">${s.name}</span>` +
      `<span class="conn-ms">${s.ok ? (s.ms ? s.ms + " мс" : "OK") : "нет связи"}</span></div>`).join("");
  }
  $("chip-conn").addEventListener("click", () => {
    $("conn-list").innerHTML = '<div class="conn-empty">Проверка…</div>';
    $("conn-modal").classList.add("open"); backdrop.classList.add("open");
    connRefresh(); connTimer = setInterval(connRefresh, 4000);
  });

  // — Пинг (живой график + плавно «бегущая» цифра, и в чипе, и в попапе) —
  const pCanvas = $("ping-canvas"), pCtx = pCanvas.getContext("2d");
  let pingData = [];
  function stepDelay(d) { d = Math.abs(d); return d <= 5 ? 90 : d <= 15 ? 55 : d <= 30 ? 30 : d <= 50 ? 18 : 9; }
  // Плавный счётчик: считает по единичке к цели, скорость шага зависит от дельты.
  function makeCounter(el, suffix) {
    let shown = 0, target = 0, timer = null;
    function tick() {
      if (shown === target) { timer = null; return; }
      shown += Math.sign(target - shown);
      el.textContent = shown + suffix;
      timer = setTimeout(tick, stepDelay(target - shown));
    }
    return {
      set(v) { target = Math.round(v); if (!timer) tick(); },
      reset() { shown = 0; target = 0; if (timer) { clearTimeout(timer); timer = null; } },
    };
  }
  const chipPing = makeCounter($("v-ping"), " мс");   // на главной
  const bigPing = makeCounter($("ping-num"), "");      // в попапе
  function pingColor(ms) { return ms <= 0 ? "" : ms < 80 ? "#30D158" : ms < 160 ? "#FF9F0A" : "#FF453A"; }
  function drawPing() {
    const w = pCanvas.width, h = pCanvas.height; pCtx.clearRect(0, 0, w, h);
    const vals = pingData.filter((v) => v > 0);
    const min = vals.length ? Math.min(...vals) : 0, max = vals.length ? Math.max(...vals) : 0;
    $("ping-min").textContent = vals.length ? min + " мс" : "—";
    $("ping-max").textContent = vals.length ? max + " мс" : "—";
    pCtx.strokeStyle = "rgba(255,255,255,0.05)"; pCtx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) { const y = h * i / 4; pCtx.beginPath(); pCtx.moveTo(0, y); pCtx.lineTo(w, y); pCtx.stroke(); }
    if (pingData.length < 2) return;
    const lo = Math.max(0, min - 10), hi = max + 10, range = Math.max(1, hi - lo);
    const stepX = w / 59;
    const grad = pCtx.createLinearGradient(0, 0, w, 0);
    grad.addColorStop(0, "#5E5CE6"); grad.addColorStop(1, "#8B4DF7");
    pCtx.strokeStyle = grad; pCtx.lineWidth = 2.5; pCtx.lineJoin = "round";
    pCtx.beginPath(); let started = false;
    const n = pingData.length;
    pingData.forEach((v, i) => {
      const x = w - (n - 1 - i) * stepX;
      if (v <= 0) { started = false; return; }
      const y = h - ((v - lo) / range) * (h - 14) - 7;
      if (!started) { pCtx.moveTo(x, y); started = true; } else pCtx.lineTo(x, y);
    });
    pCtx.stroke();
  }
  $("chip-ping").addEventListener("click", () => {
    bigPing.reset(); $("ping-num").textContent = "…"; drawPing();
    pingModalOpen = true;
    $("ping-modal").classList.add("open"); backdrop.classList.add("open");
  });

  // ---------------- титулбар ----------------
  $("btn-min").addEventListener("click", () => call("minimize"));
  $("btn-close").addEventListener("click", () => call("close_window"));

  // ---------------- тост ----------------
  let toastTimer = null;
  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 5000);
  }

  // ================= вызовы из Python =================
  window.vz = {
    onState(state, detail) {
      body.dataset.state = state;
      const T = { off:"Остановлен", connecting:"Подключение…", switching:"Переключение…",
                  running:"Защита включена", error:"Не удалось" };
      const S = { off:"Нажмите, чтобы включить защиту", running:"Обход блокировок активен" };
      setText($("status"), T[state] || state);
      setText($("subtitle"), S[state] || detail || "");
      if (state === "off" || state === "error") {
        chipPing.reset();
        $("v-conn").textContent = "—"; $("v-ping").textContent = "—"; $("v-time").textContent = "—";
      }
    },
    onStats(ok, total, lat, uptime) {
      const conn = total ? `${ok}/${total}` : "—";
      if ($("v-conn").textContent !== conn) { $("v-conn").textContent = conn; bump($("v-conn")); }
      // пинг ведёт onPing (живой, с анимацией); здесь только соединение и время
      const h = Math.floor(uptime/3600), m = Math.floor((uptime%3600)/60), s = uptime%60;
      $("v-time").textContent = `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    },
    onLog(text, tag) { logLine(text, tag); },
    onTestStart() {
      $("find").textContent = "Остановить подбор";
      $("progress").classList.add("show"); $("progress-bar").style.width = "0%";
      $("progress-text").style.color = ""; $("progress-text").textContent = "Подготовка…";
    },
    onTestProgress(idx, total, name) {
      $("progress-bar").style.width = `${Math.round(((idx-1)/total)*100)}%`;
      $("progress-text").textContent = `[${idx}/${total}]  ${name}`;
    },
    onTestDone(ok, name, oksum, total) {
      $("find").textContent = "Подобрать лучший метод";
      $("progress-bar").style.width = "100%";
      if (ok) { $("progress-text").style.color = "#30D158"; $("progress-text").textContent = `✓ Лучший: ${name} (${oksum}/${total})`; }
      else { $("progress-text").style.color = "#FF6B6B"; $("progress-text").textContent = "Рабочий метод не найден"; }
      setTimeout(() => $("progress").classList.remove("show"), 2500);
    },
    onUpdate(text, frac) {
      if (frac >= 0) $("upd-bar").style.width = `${Math.round(frac*100)}%`;
      if (text) $("upd-text").textContent = text;
    },
    onUpdateDone(ok, info, version) {
      $("upd-btn").disabled = false; $("upd-btn").textContent = "Обновить";
      $("upd-bar").style.width = ok ? "100%" : "0%";
      $("upd-text").style.color = ok ? "#30D158" : "#FF6B6B";
      $("upd-text").textContent = ok ? `Установлена версия ${info}` : info;
      $("upd-ver").textContent = `Текущая версия: ${version}`;
    },
    onMeta(version, strat) {
      $("upd-ver").textContent = `Текущая версия: ${version}`;
      $("about-ver").textContent = `Приложение v${appVer} · zapret ${version}`;
    },
    onAppUpdate(text, frac) {
      const t = $("app-upd-text");
      if (frac === -2) { t.style.color = "#FF6B6B"; }
      else if (frac >= 0 && frac < 1) { t.style.color = ""; text = `Загрузка ${Math.round(frac*100)}%`; }
      else { t.style.color = ""; }
      if (text) t.textContent = text;
    },
    onDnsStatus(d) { applyDns(d); },
    onPing(ms) {
      pingData.push(ms); if (pingData.length > 60) pingData.shift();
      // главный чип — всегда (с анимацией и цветом)
      if (ms > 0) { chipPing.set(ms); $("v-ping").style.color = pingColor(ms); }
      else { $("v-ping").textContent = "✕"; $("v-ping").style.color = "#FF453A"; }
      // попап — только пока открыт
      if (pingModalOpen) {
        drawPing();
        if (ms > 0) bigPing.set(ms); else $("ping-num").textContent = "✕";
      }
    },
    onNotify(msg) { toast(msg); },
  };

  // ================= init =================
  async function init() {
    api = (window.pywebview && window.pywebview.api) || null;
    const meta = await call("get_meta");
    if (meta) {
      appVer = meta.app || appVer;
      window.vz.onMeta(meta.version, meta.strategy);
      const s = meta.settings || {};
      $("set-autostart").checked = !!s.autostart;
      $("set-autoconnect").checked = !!s.autoconnect;
      $("set-autoupdate").checked = !!s.autoupdate;
      $("set-tray").checked = !!s.tray;
    }
    warpRefreshNow();
    call("ready");
  }
  if (window.pywebview && window.pywebview.api) init();
  else window.addEventListener("pywebviewready", init);
})();

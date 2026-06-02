/* VoidZapret WebView — фронт-логика и мост к Python (pywebview.api). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const body = document.body;
  let api = null;
  let appVer = "3.0";

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
    $("upd-btn").disabled = true; $("upd-btn").textContent = "Обновление…";
    $("upd-progress").classList.add("show"); $("upd-bar").style.width = "0%";
    $("upd-text").style.color = ""; $("upd-text").textContent = "Запуск…";
    call("update_zapret");
  });
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
    $("adv-ipset").checked = !!d.ipset;
    document.querySelectorAll("#adv-game button").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === (d.game || "off")));
  }
  async function advRefresh() { applyAdv(await call("advanced_status")); }
  function openAdv() { $("adv-drawer").classList.add("open"); $("adv-backdrop").classList.add("open"); advRefresh(); }
  function closeAdv() { $("adv-drawer").classList.remove("open"); $("adv-backdrop").classList.remove("open"); }
  $("adv-open").addEventListener("click", openAdv);
  $("adv-close").addEventListener("click", closeAdv);
  $("adv-backdrop").addEventListener("click", closeAdv);
  $("adv-quic").addEventListener("change", async (e) => applyAdv(await call("set_quic", e.target.checked)));
  $("adv-ipset").addEventListener("change", async (e) => applyAdv(await call("set_ipset", e.target.checked)));
  document.querySelectorAll("#adv-game button").forEach((b) => {
    b.addEventListener("click", async () => {
      document.querySelectorAll("#adv-game button").forEach((x) => x.classList.toggle("active", x === b));
      applyAdv(await call("set_game_filter", b.dataset.mode));
    });
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
        $("v-conn").textContent = "—"; $("v-ping").textContent = "—"; $("v-time").textContent = "—";
      }
    },
    onStats(ok, total, lat, uptime) {
      const conn = total ? `${ok}/${total}` : "—";
      if ($("v-conn").textContent !== conn) { $("v-conn").textContent = conn; bump($("v-conn")); }
      const ping = lat ? `${lat} мс` : "—";
      if ($("v-ping").textContent !== ping) { $("v-ping").textContent = ping; bump($("v-ping")); }
      $("v-ping").style.color = !lat ? "" : lat < 80 ? "#30D158" : lat < 160 ? "#FF9F0A" : "#FF453A";
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

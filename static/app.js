/* DJ666 app shell: in-site navigation swaps only <main>, so the global player keeps playing. */
(function () {
  "use strict";
  var KEY = "dj666.player.v1";
  var audio = document.getElementById("audio");
  var bar = document.getElementById("playerBar");
  if (!audio || !bar) return;
  var titleEl = document.getElementById("nowPlaying");
  var subEl = document.getElementById("playerSub");
  var btnToggle = document.getElementById("plToggle");
  var btnPrev = document.getElementById("plPrev");
  var btnNext = document.getElementById("plNext");
  var btnMode = document.getElementById("plMode");
  var btnClose = document.getElementById("plClose");
  var MODES = [["loop", "🔁", "列表循环"], ["one", "🔂", "单曲循环"], ["shuffle", "🔀", "随机播放"]];

  var st = { queue: [], index: -1, time: 0, playing: false, volume: 1, muted: false, mode: "loop" };
  try {
    var saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (saved && Array.isArray(saved.queue)) st = Object.assign(st, saved);
  } catch (e) {}

  var lastSave = 0;
  function save(force) {
    var now = Date.now();
    if (!force && now - lastSave < 1000) return;
    lastSave = now;
    if (st.index >= 0 && !isNaN(audio.currentTime)) st.time = audio.currentTime;
    st.volume = audio.volume; st.muted = audio.muted;
    try { sessionStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {}
  }
  function cur() { return st.queue[st.index] || null; }
  function sameSrc(a, b) {
    try { return new URL(a, location.href).pathname === new URL(b, location.href).pathname; } catch (e) { return a === b; }
  }
  // The player bar is hidden until a track is played (or the user opens it).
  // Closing / collapsing the bar hides the UI only — playback continues.
  // The green FAB always opens the page menu; use 「展开播放器」 to show the bar again.
  var barWanted = false;
  var fabBtn = document.getElementById("fabBtn");
  function syncPlayerH() {
    document.documentElement.style.setProperty("--player-h", bar.hidden ? "0px" : bar.offsetHeight + "px");
  }
  function syncTopbarH() {
    var tb = document.querySelector("header.topbar");
    document.documentElement.style.setProperty("--site-topbar-h", tb ? tb.offsetHeight + "px" : "0px");
  }
  function syncComposerH() {
    var el = document.getElementById("chatForm") || document.querySelector('body[data-page="chat"] .chat-guest-hint');
    document.documentElement.style.setProperty("--chat-composer-h", el ? el.offsetHeight + "px" : "0px");
  }
  var LIFT_KEY = "dj666.chat.composerLift";
  var composerLift = 0;
  try { composerLift = Math.max(0, parseInt(sessionStorage.getItem(LIFT_KEY), 10) || 0); } catch (e) {}
  var liftDrag = null;
  function composerEl() {
    return document.getElementById("chatForm") || document.querySelector('body[data-page="chat"] .chat-guest-hint');
  }
  function maxComposerLift() {
    var panel = document.querySelector('body[data-page="chat"] .chat-page-panel');
    var el = composerEl();
    if (!panel || !el) return 0;
    var head = panel.querySelector(".chat-head");
    var pr = panel.getBoundingClientRect();
    var headBottom = head ? head.getBoundingClientRect().bottom : pr.top;
    return Math.max(0, Math.floor(pr.bottom - el.offsetHeight - headBottom - 96));
  }
  function applyComposerLift(next) {
    if (document.body.getAttribute("data-page") !== "chat") {
      document.documentElement.style.setProperty("--chat-composer-lift", "0px");
      return;
    }
    if (typeof next === "number" && !isNaN(next)) composerLift = next;
    var max = maxComposerLift();
    composerLift = Math.max(0, Math.min(max, composerLift));
    document.documentElement.style.setProperty("--chat-composer-lift", composerLift + "px");
    try { sessionStorage.setItem(LIFT_KEY, String(composerLift)); } catch (e) {}
    var handle = document.querySelector(".chat-composer-handle");
    if (handle) {
      handle.setAttribute("aria-valuemin", "0");
      handle.setAttribute("aria-valuemax", String(max));
      handle.setAttribute("aria-valuenow", String(composerLift));
    }
  }
  function syncChromeH() { syncPlayerH(); syncTopbarH(); syncComposerH(); applyComposerLift(); applyFabPos(); }
  document.addEventListener("pointerdown", function (e) {
    var handle = e.target.closest && e.target.closest(".chat-composer-handle");
    if (!handle || e.button) return;
    e.preventDefault();
    try { handle.setPointerCapture(e.pointerId); } catch (err) {}
    handle.classList.add("is-dragging");
    liftDrag = { id: e.pointerId, y: e.clientY, start: composerLift, handle: handle };
  });
  document.addEventListener("pointermove", function (e) {
    if (!liftDrag || e.pointerId !== liftDrag.id) return;
    e.preventDefault();
    applyComposerLift(liftDrag.start + (liftDrag.y - e.clientY));
  });
  function endLiftDrag(e) {
    if (!liftDrag || (e && e.pointerId !== liftDrag.id)) return;
    liftDrag.handle.classList.remove("is-dragging");
    liftDrag = null;
    syncComposerH();
  }
  document.addEventListener("pointerup", endLiftDrag);
  document.addEventListener("pointercancel", endLiftDrag);
  document.addEventListener("keydown", function (e) {
    if (!e.target || !e.target.closest || !e.target.closest(".chat-composer-handle")) return;
    var step = e.shiftKey ? 48 : 16;
    if (e.key === "ArrowUp") { e.preventDefault(); applyComposerLift(composerLift + step); }
    else if (e.key === "ArrowDown") { e.preventDefault(); applyComposerLift(composerLift - step); }
    else if (e.key === "Home") { e.preventDefault(); applyComposerLift(0); }
    else if (e.key === "End") { e.preventDefault(); applyComposerLift(maxComposerLift()); }
  });
  function setBar(on) {
    barWanted = !!on;
    bar.hidden = !barWanted;
    document.body.classList.toggle("has-player", barWanted);
    syncChromeH(); markFab();
  }
  function markFab() {
    if (!fabBtn) return;
    fabBtn.classList.toggle("is-playing", !audio.paused && bar.hidden);
    fabBtn.setAttribute("aria-label", !audio.paused && bar.hidden ? "页面导航（正在播放）" : "页面导航");
    fabBtn.setAttribute("title", fabBtn.getAttribute("aria-label"));
    var expand = document.getElementById("fabExpandPlayer");
    if (expand) expand.hidden = !(cur() && bar.hidden);
  }
  window.addEventListener("resize", syncChromeH);
  syncChromeH();
  var btnMute = document.getElementById("plMute");
  function renderMute() {
    if (!btnMute) return;
    var m = audio.muted || audio.volume === 0;
    btnMute.textContent = m ? "🔇" : "🔊"; btnMute.title = m ? "取消静音" : "静音";
    btnMute.setAttribute("aria-label", btnMute.title); btnMute.setAttribute("aria-pressed", m ? "true" : "false");
  }
  if (btnMute) {
    btnMute.addEventListener("click", function () { audio.muted = !audio.muted; if (!audio.muted && audio.volume === 0) audio.volume = 1; });
    audio.addEventListener("volumechange", renderMute);
  }
  function render() {
    var t = cur();
    titleEl.textContent = t ? t.title : "未选择曲目";
    subEl.textContent = t && st.queue.length > 1 ? "第 " + (st.index + 1) + " / " + st.queue.length + " 首" : "";
    var on = !audio.paused;
    btnToggle.classList.toggle("is-playing", on);
    btnToggle.setAttribute("aria-pressed", on ? "true" : "false");
    btnToggle.title = on ? "暂停" : "播放"; btnToggle.setAttribute("aria-label", on ? "暂停" : "播放");
    var ic = document.getElementById("plToggleIcon"); if (ic) ic.textContent = on ? "⏸" : "▶";
    renderMute();
    var m = MODES.filter(function (x) { return x[0] === st.mode; })[0] || MODES[0];
    btnMode.textContent = m[1]; btnMode.title = m[2];
    var many = st.queue.length > 1;
    btnPrev.disabled = !many; btnNext.disabled = !many;
    markPlaying(); markFab();
    if ("mediaSession" in navigator && t) {
      try { navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: "DJ666" }); } catch (e) {}
    }
  }
  function markPlaying() {
    var t = cur();
    document.querySelectorAll("[data-playlist] .track-item").forEach(function (el) {
      el.classList.toggle("is-playing", !!t && sameSrc(el.dataset.src, t.src));
    });
  }
  function load(index, autoplay, startAt) {
    if (index < 0 || index >= st.queue.length) return;
    st.index = index;
    var t = cur();
    if (!sameSrc(audio.getAttribute("src") || "", t.src)) audio.src = t.src;
    if (startAt) {
      var seek = function () { try { audio.currentTime = startAt; } catch (e) {} };
      if (audio.readyState >= 1) seek(); else audio.addEventListener("loadedmetadata", seek, { once: true });
    }
    render();
    if (autoplay) {
      var p = audio.play();
      if (p && p.catch) p.catch(function () { st.playing = false; render(); save(true); });
    }
    save(true);
  }
  function playQueue(items, index) {
    st.queue = items; load(index, true, 0); setBar(true);
  }
  function step(dir, auto) {
    if (!st.queue.length) return;
    if (st.mode === "one" && auto) { audio.currentTime = 0; audio.play().catch(function () {}); return; }
    var n = st.queue.length, i;
    if (st.mode === "shuffle" && n > 1) {
      do { i = Math.floor(Math.random() * n); } while (i === st.index);
    } else {
      i = (st.index + dir + n) % n;
    }
    load(i, true, 0);
  }
  function listItems(listEl) {
    return Array.prototype.map.call(listEl.querySelectorAll(".track-item[data-src]:not([hidden])"), function (el) {
      return { src: el.dataset.src, title: el.dataset.title || "未命名" };
    });
  }

  // Music search: instant, case-insensitive title filter (works after pjax swaps too).
  function norm(s) { return (s || "").toLowerCase().replace(/\s+/g, " ").trim(); }
  function applySearch(inputOrId) {
    var id = typeof inputOrId === "string" ? inputOrId : (inputOrId && inputOrId.dataset && inputOrId.dataset.trackSearch);
    var listEl = id && document.getElementById(id);
    if (!listEl) return;
    var input = document.querySelector('[data-track-search="' + id + '"]');
    var q = norm(input ? input.value : "");
    var catRoot = document.querySelector('[data-track-cats="' + id + '"]');
    var active = catRoot && catRoot.querySelector(".track-cat.is-active");
    var cat = active ? (active.getAttribute("data-track-cat") || "") : "";
    var shown = 0, total = 0;
    listEl.querySelectorAll(".track-item").forEach(function (el) {
      total++;
      var textHit = !q || norm(el.dataset.title).indexOf(q) !== -1;
      var catHit = !cat || (el.getAttribute("data-track-cat") || "") === cat;
      var hit = textHit && catHit;
      el.hidden = !hit; if (hit) shown++;
    });
    var cnt = document.querySelector('[data-track-search-count="' + id + '"]');
    if (cnt) { cnt.hidden = !q && !cat; cnt.textContent = shown + " / " + total; }
    var empty = document.querySelector('[data-track-search-empty="' + id + '"]');
    if (empty) empty.hidden = shown > 0 || (!q && !cat);
  }
  function annFilterUrl() {
    var textInput = document.querySelector("[data-ann-search]");
    var dateInput = document.querySelector("[data-ann-date-filter]");
    var u = new URL("/", location.origin);
    var q = textInput ? textInput.value.trim() : "";
    var day = dateInput && dateInput.value ? dateInput.value : "";
    if (q) u.searchParams.set("q", q);
    if (day) u.searchParams.set("date", day);
    return u.pathname + u.search + "#announce";
  }
  function goAnnFilter() {
    var url = annFilterUrl();
    if (url === location.pathname + location.search + "#announce" || url === location.pathname + location.search) return;
    location.assign(url);
  }
  var annFilterTimer;
  function applyAnnSearch(immediate) {
    var feed = document.getElementById("annFeed") || document.querySelector("#announce .tg-feed");
    if (!feed) return;
    var textInput = document.querySelector("[data-ann-search]");
    var dateInput = document.querySelector("[data-ann-date-filter]");
    var clearBtn = document.querySelector("[data-ann-date-clear]");
    if (clearBtn) clearBtn.hidden = !(dateInput && dateInput.value);
    if (feed.getAttribute("data-ann-paged") === "1") {
      if (immediate) goAnnFilter();
      else {
        clearTimeout(annFilterTimer);
        annFilterTimer = setTimeout(goAnnFilter, 400);
      }
      return;
    }
    var q = norm(textInput ? textInput.value : "");
    var day = dateInput && dateInput.value ? dateInput.value : "";
    var shown = 0, total = 0;
    feed.querySelectorAll(".tg-post").forEach(function (el) {
      total++;
      var textHit = !q || norm(el.getAttribute("data-ann-text") || "").indexOf(q) !== -1;
      var dateHit = !day || (el.getAttribute("data-ann-date") || "") === day;
      var hit = textHit && dateHit;
      el.hidden = !hit; if (hit) shown++;
    });
    var cnt = document.querySelector("[data-ann-search-count]");
    if (cnt) { cnt.hidden = !q && !day; cnt.textContent = shown + " / " + total; }
    var empty = document.querySelector("[data-ann-search-empty]");
    if (empty) empty.hidden = shown > 0 || (!q && !day);
  }
  (function jumpAnnHash() {
    var m = (location.hash || "").match(/^#ann-(\d+)$/);
    if (!m || document.getElementById("ann-" + m[1])) return;
    var u = new URL(location.href);
    if (u.searchParams.get("ann") === m[1]) return;
    u.searchParams.set("ann", m[1]);
    u.searchParams.delete("page");
    location.replace(u.pathname + "?" + u.searchParams.toString() + u.hash);
  })();
  document.addEventListener("input", function (e) {
    if (e.target.matches && e.target.matches("[data-track-search]")) applySearch(e.target);
    if (e.target.matches && e.target.matches("[data-ann-search]")) applyAnnSearch(false);
  });
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest(".track-cat[data-track-cat]");
    if (!btn) return;
    var root = btn.closest("[data-track-cats]");
    if (!root) return;
    var already = btn.classList.contains("is-active");
    var goAll = already || !btn.getAttribute("data-track-cat");
    root.querySelectorAll(".track-cat").forEach(function (b) {
      var on = goAll ? !b.getAttribute("data-track-cat") : b === btn;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    applySearch(root.getAttribute("data-track-cats"));
  });
  document.addEventListener("change", function (e) {
    if (e.target.matches && e.target.matches("[data-ann-date-filter]")) applyAnnSearch(true);
  });
  document.addEventListener("click", function (e) {
    var c = e.target.closest && e.target.closest("[data-ann-date-clear]");
    if (!c) return;
    e.preventDefault();
    var dateInput = document.querySelector("[data-ann-date-filter]");
    if (dateInput) dateInput.value = "";
    applyAnnSearch(true);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && e.target.matches && e.target.matches("[data-track-search]")) { e.target.value = ""; applySearch(e.target); }
    if (e.key === "Escape" && e.target.matches && e.target.matches("[data-ann-search]")) { e.target.value = ""; applyAnnSearch(true); }
    if (e.key === "Escape" && e.target.matches && e.target.matches("[data-ann-date-filter]")) { e.target.value = ""; applyAnnSearch(true); }
  });

  // Rename my own uploads inline (server checks ownership + CSRF).
  function renameUi(item, open) {
    var f = item.querySelector(".track-rename-inline"), b = item.querySelector(".track-rename-btn");
    if (!f) return;
    f.hidden = !open; item.classList.toggle("is-renaming", open);
    if (b) b.hidden = open;
    var msg = f.querySelector(".track-rename-msg"); if (msg) msg.textContent = "";
    var inp = f.querySelector('input[name="title"]');
    if (open) { inp.value = item.dataset.title || inp.value; inp.focus(); inp.select(); }
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-track-rename]");
    if (b) { e.preventDefault(); e.stopPropagation(); var it = b.closest(".track-item"); if (it && !it.classList.contains("is-renaming")) renameUi(it, true); return; }
    var c = e.target.closest("[data-track-rename-cancel]");
    if (c) { e.preventDefault(); e.stopPropagation(); renameUi(c.closest(".track-item"), false); return; }
    if (e.target.closest(".track-rename-inline")) e.stopPropagation();
  }, true);
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if ((e.key === "Enter" || e.key === " ") && t.matches && t.matches("[data-track-rename]")) { e.preventDefault(); renameUi(t.closest(".track-item"), true); }
    if (e.key === "Escape" && t.closest && t.closest(".track-rename-inline")) { renameUi(t.closest(".track-item"), false); }
  });
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!(f instanceof HTMLFormElement) || !f.classList.contains("track-rename-inline")) return;
    e.preventDefault();
    var item = f.closest(".track-item"), msg = f.querySelector(".track-rename-msg");
    var inp = f.querySelector('input[name="title"]'), title = inp.value.trim();
    if (!title) { msg.textContent = "曲目名称不能为空"; return; }
    var btn = f.querySelector('button[type="submit"]'); btn.disabled = true;
    fetch(f.action, { method: "POST", body: new FormData(f), credentials: "same-origin", headers: { "X-Requested-With": "dj666-rename", "Accept": "application/json" } })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: r.status === 400 ? "页面已过期，请刷新后重试" : "保存失败（" + r.status + "）" }; }); })
      .then(function (d) {
        btn.disabled = false;
        if (!d || !d.ok) { msg.textContent = (d && d.error) || "保存失败"; return; }
        var src = item.dataset.src, nt = d.title;
        document.querySelectorAll(".track-item[data-src]").forEach(function (el) {
          if (!sameSrc(el.dataset.src, src)) return;
          el.dataset.title = nt;
          var s = el.querySelector(".track-title-text") || el.querySelector(".track-title, .track-meta strong"); if (s) s.textContent = nt;
          el.querySelectorAll("[data-track-rename][aria-label]").forEach(function (tt) { if (tt.getAttribute("role") === "button") tt.setAttribute("aria-label", "修改曲名：" + nt); });
          var pb = el.querySelector(".play-btn"); if (pb) pb.setAttribute("aria-label", "播放 " + nt);
          var ti = el.querySelector('.track-rename-inline input[name="title"]'); if (ti) ti.value = nt;
        });
        var changed = false;
        st.queue.forEach(function (q) { if (sameSrc(q.src, src)) { q.title = nt; changed = true; } });
        if (changed) { render(); save(true); }
        renameUi(item, false);
        var ok = item.querySelector(".track-rename-ok");
        if (ok) { ok.hidden = false; clearTimeout(ok._t); ok._t = setTimeout(function () { ok.hidden = true; }, 2200); }
        var sInput = document.querySelector("[data-track-search]"); if (sInput) applySearch(sInput);
      })
      .catch(function () { btn.disabled = false; msg.textContent = "网络错误，请重试"; });
  });

  // Track clicks anywhere (music page, home "最新音乐", settings "我的曲目").
  document.addEventListener("click", function (e) {
    var all = e.target.closest("[data-play-all]");
    if (all) {
      var listEl = document.querySelector('[data-playlist="' + all.dataset.playAll + '"]');
      if (!listEl) return;
      var items = listItems(listEl);
      if (!items.length) return;
      if (all.dataset.shuffle) { st.mode = "shuffle"; playQueue(items, Math.floor(Math.random() * items.length)); }
      else { if (st.mode === "shuffle") st.mode = "loop"; playQueue(items, 0); }
      return;
    }
    var item = e.target.closest("[data-playlist] .track-item[data-src]");
    if (!item) return;
    if (e.target.closest("form, input, textarea, select, a, button.btn, [data-track-rename]")) return;
    var list = item.closest("[data-playlist]");
    var els = Array.prototype.slice.call(list.querySelectorAll(".track-item[data-src]:not([hidden])"));
    var t = cur();
    if (t && sameSrc(item.dataset.src, t.src)) {  // same track: toggle
      if (audio.paused) { audio.play().catch(function () {}); setBar(true); } else audio.pause();
      return;
    }
    playQueue(listItems(list), els.indexOf(item));
  });
  btnToggle.addEventListener("click", function () {
    if (!cur()) return;
    if (audio.paused) audio.play().catch(function () {}); else audio.pause();
  });
  btnPrev.addEventListener("click", function () { step(-1, false); });
  btnNext.addEventListener("click", function () { step(1, false); });
  btnMode.addEventListener("click", function () {
    var i = MODES.map(function (x) { return x[0]; }).indexOf(st.mode);
    st.mode = MODES[(i + 1) % MODES.length][0]; render(); save(true);
  });
  btnClose.addEventListener("click", function () {
    // Hide the player UI only; do not pause or clear the queue.
    setBar(false);
    render();
  });
  audio.addEventListener("play", function () { st.playing = true; render(); save(true); });
  audio.addEventListener("pause", function () { if (!audio.ended) { st.playing = false; } render(); save(true); });
  audio.addEventListener("ended", function () { step(1, true); });
  audio.addEventListener("timeupdate", function () { save(false); });
  audio.addEventListener("volumechange", function () { save(true); });
  audio.addEventListener("error", function () {
    if (cur()) { subEl.textContent = "无法播放此曲目（可能已下架或需要登录）"; }
  });
  window.addEventListener("pagehide", function () { save(true); });
  if ("mediaSession" in navigator) {
    try {
      navigator.mediaSession.setActionHandler("previoustrack", function () { step(-1, false); });
      navigator.mediaSession.setActionHandler("nexttrack", function () { step(1, false); });
    } catch (e) {}
  }

  // Restore after a hard reload / direct visit (same browser tab session).
  audio.volume = typeof st.volume === "number" ? st.volume : 1;
  audio.muted = !!st.muted;
  if (st.queue.length && st.index >= 0) {
    load(st.index, !!st.playing, st.time || 0);
    setBar(true);
  } else {
    render();
  }

  window.DJPlayer = { playQueue: playQueue, state: function () { return st; }, audio: audio, showBar: setBar };

  // ------------------------------------------------------------------
  // Floating page menu (首页 / 音乐 / 公屏). Links go through pjax below.
  // ------------------------------------------------------------------
  var fabNav = document.getElementById("fabNav");
  var fabMenu = document.getElementById("fabMenu");
  function fabOpen(on) {
    if (!fabNav) return;
    fabMenu.hidden = !on;
    fabNav.classList.toggle("open", on);
    fabBtn.setAttribute("aria-expanded", on ? "true" : "false");
  }
  function fabActive() {
    if (!fabMenu) return;
    var path = location.pathname;
    fabMenu.querySelectorAll("a[data-fab]").forEach(function (a) {
      var on = new URL(a.href, location.href).pathname === path;
      a.classList.toggle("is-active", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  var FAB_KEY = "dj666.fab.pos";
  var FAB_SIZE = 60;
  var FAB_PAD = 8;
  var FAB_DRAG_THRESHOLD = 8;
  var fabPos = null;
  var fabDrag = null;
  var fabSuppressClick = false;
  function cssPx(name) {
    var n = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
    return isNaN(n) ? 0 : n;
  }
  function loadFabPos() {
    try {
      var o = JSON.parse(localStorage.getItem(FAB_KEY) || "null");
      if (o && typeof o.l === "number" && typeof o.t === "number") fabPos = { l: o.l, t: o.t };
    } catch (e) {}
  }
  function fabClamp(l, t) {
    var minL = FAB_PAD + cssPx("--safe-l");
    var maxL = window.innerWidth - FAB_SIZE - FAB_PAD - cssPx("--safe-r");
    var minT = Math.max(cssPx("--site-topbar-h"), cssPx("--safe-t")) + FAB_PAD;
    var bottomClear = cssPx("--player-h") + cssPx("--safe-b");
    if (document.body.getAttribute("data-page") === "chat") {
      bottomClear += cssPx("--chat-composer-h") + cssPx("--chat-composer-lift");
    }
    var maxT = window.innerHeight - FAB_SIZE - FAB_PAD - bottomClear;
    if (maxL < minL) maxL = minL;
    if (maxT < minT) maxT = minT;
    return {
      l: Math.round(Math.max(minL, Math.min(maxL, l))),
      t: Math.round(Math.max(minT, Math.min(maxT, t)))
    };
  }
  function fabFlipMenu() {
    if (!fabNav || !fabMenu) return;
    var r = fabNav.getBoundingClientRect();
    var menuW = 180, menuH = 260;
    fabNav.classList.toggle("menu-below", r.top < menuH + 8);
    fabNav.classList.toggle("menu-left", r.left < menuW + 8);
  }
  function applyFabPos() {
    if (!fabNav) return;
    if (!fabPos) {
      fabNav.classList.remove("is-moved");
      fabNav.style.left = "";
      fabNav.style.top = "";
      fabFlipMenu();
      return;
    }
    var p = fabClamp(fabPos.l, fabPos.t);
    fabNav.classList.add("is-moved");
    fabNav.style.left = p.l + "px";
    fabNav.style.top = p.t + "px";
    fabFlipMenu();
  }
  if (fabNav) {
    loadFabPos();
    applyFabPos();
    fabBtn.addEventListener("pointerdown", function (e) {
      if (!fabBtn || e.button) return;
      e.stopPropagation();
      try { fabBtn.setPointerCapture(e.pointerId); } catch (err) {}
      var r = fabNav.getBoundingClientRect();
      fabDrag = { id: e.pointerId, x: e.clientX, y: e.clientY, sl: r.left, st: r.top, moved: false };
    });
    fabBtn.addEventListener("pointermove", function (e) {
      if (!fabDrag || e.pointerId !== fabDrag.id) return;
      var dx = e.clientX - fabDrag.x, dy = e.clientY - fabDrag.y;
      if (!fabDrag.moved && Math.abs(dx) + Math.abs(dy) < FAB_DRAG_THRESHOLD) return;
      if (!fabDrag.moved) {
        fabDrag.moved = true;
        fabOpen(false);
        fabNav.classList.add("is-dragging");
      }
      e.preventDefault();
      fabPos = fabClamp(fabDrag.sl + dx, fabDrag.st + dy);
      applyFabPos();
    });
    function endFabDrag(e) {
      if (!fabDrag || (e && e.pointerId !== fabDrag.id)) return;
      var wasDrag = fabDrag.moved;
      fabNav.classList.remove("is-dragging");
      fabDrag = null;
      if (wasDrag) {
        fabSuppressClick = true;
        try { localStorage.setItem(FAB_KEY, JSON.stringify(fabPos)); } catch (err) {}
      }
    }
    fabBtn.addEventListener("pointerup", endFabDrag);
    fabBtn.addEventListener("pointercancel", endFabDrag);
    fabBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (fabSuppressClick) { fabSuppressClick = false; e.preventDefault(); return; }
      fabOpen(fabMenu.hidden);
    });
    fabMenu.addEventListener("click", function (e) {
      var a = e.target.closest("[data-fab]");
      if (!a) return;
      if (a.dataset.fab === "player") {
        e.preventDefault();
        setBar(true);
        fabOpen(false);
        return;
      }
      fabOpen(false);
      if (a.tagName === "A" && new URL(a.href, location.href).pathname === location.pathname) e.preventDefault();  // already here
    });
    document.addEventListener("click", function (e) { if (!fabMenu.hidden && !fabNav.contains(e.target)) fabOpen(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !fabMenu.hidden) { fabOpen(false); fabBtn.focus(); } });
  }
  fabActive();

  // ------------------------------------------------------------------
  // In-site navigation (pjax): real URLs, but only #appMain is replaced.
  // ------------------------------------------------------------------
  var supported = !!(window.history && history.pushState && window.fetch && window.DOMParser);
  var SKIP = /^\/(admin|uploads|media|static|logout|login|register)(\/|$)/;
  function onAdmin() { return /^\/admin/.test(location.pathname); }
  function eligible(url) {
    if (!supported || onAdmin()) return false;
    if (url.origin !== location.origin) return false;
    if (SKIP.test(url.pathname)) return false;
    return true;
  }
  var progress = document.getElementById("pjaxBar");
  function busy(on) { document.body.classList.toggle("pjax-loading", on); if (progress) progress.classList.toggle("on", on); }

  function runLeave() {
    var fns = window.DJPageLeave || [];
    window.DJPageLeave = [];
    fns.forEach(function (fn) { try { fn(); } catch (e) {} });
  }
  function execScripts(container) {
    var scripts = Array.prototype.slice.call(container.querySelectorAll("script"));
    var chain = Promise.resolve();
    scripts.forEach(function (old) {
      chain = chain.then(function () {
        return new Promise(function (resolve) {
          var s = document.createElement("script");
          Array.prototype.forEach.call(old.attributes, function (a) { if (a.name !== "defer") s.setAttribute(a.name, a.value); });
          if (old.src) { s.async = false; s.onload = s.onerror = function () { resolve(); }; }
          else { s.textContent = old.textContent; }
          old.parentNode.replaceChild(s, old);
          if (!old.src) resolve();
        });
      });
    });
    return chain;
  }
  function swap(doc, url, push, hash) {
    var main = doc.getElementById("appMain");
    if (!main) return false;
    runLeave();
    document.title = doc.title;
    var pairs = [["siteNav", "innerHTML"], ["flashArea", "innerHTML"], ["appMain", "innerHTML"]];
    pairs.forEach(function (p) {
      var src = doc.getElementById(p[0]), dst = document.getElementById(p[0]);
      if (src && dst) dst.innerHTML = src.innerHTML;
    });
    document.body.setAttribute("data-page", doc.body.getAttribute("data-page") || "other");
    var ps = document.getElementById("pageScripts"), nps = doc.getElementById("pageScripts");
    if (push) history.pushState({ pjax: true }, "", url);
    if (ps) { ps.innerHTML = nps ? nps.innerHTML : ""; execScripts(ps); }
    markPlaying(); fabActive(); fabOpen(false); syncChromeH();
    var target = hash ? document.getElementById(hash.slice(1)) : null;
    if (target) target.scrollIntoView({ block: "start" }); else window.scrollTo(0, 0);
    return true;
  }
  function visit(url, opts) {
    opts = opts || {};
    var u = new URL(url, location.href);
    busy(true);
    var init = { credentials: "same-origin", headers: { "X-Requested-With": "dj666-pjax" }, redirect: "follow" };
    if (opts.method === "POST") { init.method = "POST"; init.body = opts.body; }
    return fetch(u.href, init).then(function (res) {
      var ct = res.headers.get("content-type") || "";
      var finalUrl = new URL(res.url || u.href);
      if (ct.indexOf("text/html") === -1) {
        return res.text().then(function (txt) {
          var msg = "";
          try { msg = JSON.parse(txt).detail || ""; } catch (e) {}
          if (opts.method === "POST" && msg) { alert(msg); return; }
          location.href = finalUrl.href;
        });
      }
      return res.text().then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        if (/^\/admin/.test(finalUrl.pathname) || !doc.getElementById("appMain")) { location.href = finalUrl.href; return; }
        var push = opts.push !== false;
        var dest = finalUrl.pathname + finalUrl.search + (opts.method === "POST" ? "" : u.hash);
        if (!swap(doc, dest, push, opts.method === "POST" ? "" : u.hash)) location.href = finalUrl.href;
      });
    }).catch(function () {
      if (opts.fallback) opts.fallback(); else location.href = u.href;
    }).then(function () { busy(false); });
  }

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest("a[href]");
    if (!a || a.target || a.hasAttribute("download") || a.hasAttribute("data-no-pjax")) return;
    var url = new URL(a.href, location.href);
    if (!eligible(url)) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) {
      return; // in-page anchor
    }
    e.preventDefault();
    visit(url.href);
  });

  document.addEventListener("submit", function (e) {
    if (e.defaultPrevented) return;
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || form.hasAttribute("data-no-pjax")) return;
    var url = new URL(form.getAttribute("action") || location.href, location.href);
    if (!eligible(url)) return;
    var method = (form.getAttribute("method") || "GET").toUpperCase();
    var submitter = e.submitter || null;
    var fd;
    try { fd = submitter ? new FormData(form, submitter) : new FormData(form); }
    catch (err) { fd = new FormData(form); if (submitter && submitter.name) fd.append(submitter.name, submitter.value); }
    e.preventDefault();
    var btns = form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]');
    btns.forEach(function (b) { b.disabled = true; });
    if (submitter && submitter.tagName === "BUTTON" && form.enctype === "multipart/form-data") {
      submitter.dataset.label = submitter.textContent; submitter.textContent = "提交中…";
    }
    var fallback = function () { HTMLFormElement.prototype.submit.call(form); };
    if (method === "GET") {
      url.search = new URLSearchParams(fd).toString();
      visit(url.href, { fallback: fallback });
    } else {
      visit(url.href, { method: "POST", body: fd, fallback: fallback }).then(function () {
        if (document.body.contains(form)) btns.forEach(function (b) { b.disabled = false; if (b.dataset.label) b.textContent = b.dataset.label; });
      });
    }
  });

  window.addEventListener("popstate", function () {
    if (onAdmin()) { location.reload(); return; }
    visit(location.href, { push: false });
  });
  if (supported) history.replaceState({ pjax: true }, "", location.href);

  // Old links: /#music and /#chat now have their own pages.
  if (location.pathname === "/" && (location.hash === "#music" || location.hash === "#chat")) {
    var dest = location.hash === "#music" ? "/music" : "/chat";
    if (supported) { history.replaceState({ pjax: true }, "", dest); visit(dest, { push: false }); }
    else location.replace(dest);
  }
})();

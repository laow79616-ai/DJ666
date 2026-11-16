/* First-party analytics: heartbeat + share. No third-party trackers. */
(function () {
  "use strict";
  var ENDPOINT = "/api/analytics/beacon";
  var HEARTBEAT_MS = 15000;
  var MAX_DELTA = 60000;

  function csrfToken() {
    var el = document.querySelector('input[name="csrf_token"]');
    return el ? el.value : "";
  }
  function formatStat(n) {
    n = parseInt(n, 10) || 0;
    if (n < 0) n = 0;
    if (n < 1000) return String(n);
    if (n < 100000) return String((n / 1000).toFixed(1).replace(/\.0$/, "")) + "k";
    if (n < 10000) return String(n);
    var wan = n / 10000;
    return (Math.round(wan * 10) / 10) + "万";
  }
  function updateCounts(id, d) {
    if (!id || !d) return;
    if (d.play_label != null || d.play_count != null) {
      document.querySelectorAll('[data-play-count="' + id + '"]').forEach(function (el) {
        el.textContent = d.play_label || formatStat(d.play_count);
      });
    }
    if (d.favorite_label != null || d.favorite_count != null) {
      document.querySelectorAll('[data-fav-count="' + id + '"]').forEach(function (el) {
        el.textContent = d.favorite_label || formatStat(d.favorite_count);
      });
    }
  }
  function skipPath() {
    var path = location.pathname || "/";
    return path.indexOf("/admin") === 0;
  }
  function post(payload, beacon) {
    payload = payload || {};
    payload.csrf = csrfToken();
    if (!payload.csrf) return Promise.resolve(null);
    var body = JSON.stringify(payload);
    if (beacon && navigator.sendBeacon) {
      try {
        var blob = new Blob([body], { type: "application/json" });
        if (navigator.sendBeacon(ENDPOINT, blob)) return Promise.resolve({ ok: true, beacon: true });
      } catch (e) {}
    }
    return fetch(ENDPOINT, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: body,
      keepalive: !!beacon
    }).then(function (r) { return r.json().catch(function () { return { ok: false }; }); }).catch(function () { return null; });
  }

  var lastTick = Date.now();
  var timer = null;
  function flushHeartbeat(useBeacon) {
    if (skipPath()) { lastTick = Date.now(); return; }
    var now = Date.now();
    var delta = now - lastTick;
    lastTick = now;
    if (delta < 1000) return;
    if (delta > MAX_DELTA) delta = MAX_DELTA;
    post({ type: "heartbeat", dwell_ms: delta }, useBeacon);
  }
  function startHeart() {
    if (timer) clearInterval(timer);
    lastTick = Date.now();
    timer = setInterval(function () {
      if (document.visibilityState === "hidden") return;
      flushHeartbeat(false);
    }, HEARTBEAT_MS);
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") flushHeartbeat(true);
    else lastTick = Date.now();
  });
  window.addEventListener("pagehide", function () { flushHeartbeat(true); });
  if (!skipPath()) startHeart();

  function trackUrl(id, title) {
    var u = new URL("/music", location.origin);
    if (id) u.searchParams.set("t", String(id));
    if (title) u.searchParams.set("title", title);
    // keep share URL clean: id only
    u.searchParams.delete("title");
    return u.toString();
  }
  var PLATFORMS = [
    { id: "telegram", label: "Telegram", href: function (url, title) { return "https://t.me/share/url?url=" + encodeURIComponent(url) + "&text=" + encodeURIComponent(title || ""); } },
    { id: "whatsapp", label: "WhatsApp", href: function (url, title) { return "https://wa.me/?text=" + encodeURIComponent((title ? title + " " : "") + url); } },
    { id: "x", label: "X", href: function (url, title) { return "https://twitter.com/intent/tweet?url=" + encodeURIComponent(url) + "&text=" + encodeURIComponent(title || ""); } },
    { id: "facebook", label: "Facebook", href: function (url) { return "https://www.facebook.com/sharer/sharer.php?u=" + encodeURIComponent(url); } }
  ];
  var pop = null, toast = null, popOwner = null;
  function ensurePop() {
    if (pop) return pop;
    pop = document.createElement("div");
    pop.className = "share-pop";
    pop.hidden = true;
    pop.setAttribute("role", "menu");
    document.body.appendChild(pop);
    toast = document.createElement("div");
    toast.className = "share-toast-lite";
    toast.hidden = true;
    document.body.appendChild(toast);
    document.addEventListener("click", function (e) {
      if (!pop || pop.hidden) return;
      if (pop.contains(e.target) || (popOwner && popOwner.contains(e.target))) return;
      hidePop();
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") hidePop(); });
    return pop;
  }
  function hidePop() {
    if (pop) pop.hidden = true;
    if (popOwner) popOwner.setAttribute("aria-expanded", "false");
    popOwner = null;
  }
  function showToast(text) {
    if (!toast) return;
    toast.textContent = text;
    toast.hidden = false;
    setTimeout(function () { toast.hidden = true; }, 1800);
  }
  function reportShare(platform, id, source) {
    post({ type: "share", platform: platform, track_id: id || 0, source: source || "" }, false);
  }
  function openShare(btn, id, title, source) {
    ensurePop();
    var url = trackUrl(id, title);
    pop.innerHTML = "";
    PLATFORMS.forEach(function (p) {
      var a = document.createElement("a");
      a.href = p.href(url, title || "");
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = p.label;
      a.addEventListener("click", function () { reportShare(p.id, id, source); hidePop(); });
      pop.appendChild(a);
    });
    var copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "复制链接";
    copy.addEventListener("click", function () {
      var done = function () { reportShare("copy", id, source); hidePop(); showToast("链接已复制"); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done).catch(function () {
          try { prompt("复制链接", url); done(); } catch (e) { done(); }
        });
      } else {
        try { prompt("复制链接", url); } catch (e) {}
        done();
      }
    });
    pop.appendChild(copy);
    if (navigator.share) {
      var nat = document.createElement("button");
      nat.type = "button";
      nat.textContent = "系统分享";
      nat.addEventListener("click", function () {
        navigator.share({ title: title || "DJ666", url: url }).then(function () {
          reportShare("native", id, source); hidePop();
        }).catch(function () {});
      });
      pop.appendChild(nat);
    }
    var r = btn.getBoundingClientRect();
    pop.hidden = false;
    pop.style.top = (window.scrollY + r.bottom + 6) + "px";
    var left = window.scrollX + r.right - pop.offsetWidth;
    if (left < 8) left = 8;
    pop.style.left = left + "px";
    if (popOwner) popOwner.setAttribute("aria-expanded", "false");
    popOwner = btn;
    btn.setAttribute("aria-expanded", "true");
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-share-track], #plShare");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var id = btn.getAttribute("data-share-track") || btn.getAttribute("data-track-id") || "";
    var title = btn.getAttribute("data-share-title") || btn.getAttribute("data-title") || "";
    if (btn.id === "plShare") {
      id = btn.getAttribute("data-track-id") || id;
      title = btn.getAttribute("data-title") || title;
    }
    openShare(btn, id, title, btn.id === "plShare" ? "player" : "row");
  }, true);

  window.dj666Analytics = {
    reportPlay: function (trackId) {
      if (!trackId) return Promise.resolve(null);
      return post({ type: "play", track_id: Number(trackId) }, false).then(function (d) {
        var list = (d && d.plays) || [];
        list.forEach(function (row) {
          if (!row) return;
          updateCounts(row.track_id, row);
        });
        return d;
      });
    },
    reportShare: reportShare,
    updateCounts: updateCounts,
    formatStat: formatStat,
    trackUrl: trackUrl
  };
})();

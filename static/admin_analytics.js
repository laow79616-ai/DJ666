/* Admin first-party analytics dashboard (tab #stats). */
(function () {
  "use strict";
  var root = document.getElementById("anDash");
  if (!root) return;
  var range = 7;
  var labels = { telegram: "Telegram", whatsapp: "WhatsApp", x: "X", facebook: "Facebook", copy: "复制链接", native: "系统分享", inbound: "直接访问 / 其他" };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function barsH(el, rows, empty) {
    if (!el) return;
    rows = rows || [];
    if (!rows.length) { el.innerHTML = "<li class='muted'>" + esc(empty || "还没有数据。统计从本次上线开始记录真实访问。") + "</li>"; return; }
    el.innerHTML = rows.map(function (r) {
      var name = labels[r.platform] || r.label || r.platform || r.key || "";
      return "<li><span>" + esc(name) + "</span><i><b style='width:" + (r.pct || 0) + "%'></b></i><em>" + esc(r.count) + "（" + esc(r.pct) + "%）</em></li>";
    }).join("");
  }
  function trend(el, foot, days, all) {
    if (!el) return;
    var rows = (all || []).slice(-days);
    var max = 1;
    rows.forEach(function (r) { if ((r.unique || 0) > max) max = r.unique; });
    el.innerHTML = rows.map(function (r) {
      var h = Math.max(2, Math.round(((r.unique || 0) / max) * 100));
      var lab = (r.day || "").slice(5);
      return "<span title='" + esc(r.day) + "：" + (r.unique || 0) + " 位访客'><i style='height:" + h + "%'></i><em>" + esc(lab) + "</em></span>";
    }).join("");
    if (foot) foot.textContent = "按马来西亚时间（MYT，UTC+8）自然日统计独立访客。";
  }
  function render(d) {
    if (!d) return;
    function set(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }
    set("anTodayUv", d.today_unique || 0);
    set("anTotalUv", d.total_unique || 0);
    set("anKpiToday", d.today_unique || 0);
    set("anKpiTotal", d.total_unique || 0);
    set("anKpiVisits", d.today_visits || 0);
    set("anKpiNew", (d.today_new_pct || 0) + "%");
    var newEl = document.getElementById("anKpiNew");
    if (newEl && newEl.nextSibling) { /* leave label in parent */ }
    var newWrap = newEl && newEl.parentNode && newEl.parentNode.querySelector("span");
    if (newWrap) newWrap.textContent = "今日新访客 " + (d.today_new || 0) + " · 回访 " + (d.today_returning || 0) + "（" + (d.today_returning_pct || 0) + "%）";
    barsH(document.getElementById("anFreq"), (d.freq && d.freq.buckets) || []);
    set("anDwellAvg", d.dwell_avg || "0 秒");
    set("anDwellMed", d.dwell_median || "0 秒");
    set("anDwellN", d.dwell_sample || 0);
    barsH(document.getElementById("anShareClicks"), d.share_clicks, "还没有分享点击。");
    barsH(document.getElementById("anShareIn"), d.share_inbound, "还没有站外来路。直接打开记为「直接访问 / 其他」。");
    trend(document.getElementById("anTrend"), document.getElementById("anTrendFoot"), range, d.trend || []);
    var tb = document.querySelector("#anSessTable tbody");
    if (tb) {
      var sess = d.sessions || [];
      if (!sess.length) tb.innerHTML = '<tr><td colspan="6" class="muted">还没有会话数据。统计从本次上线开始记录真实访问。</td></tr>';
      else tb.innerHTML = sess.map(function (s) {
        return "<tr><td>" + esc(s.who) + "</td><td>" + esc(s.dwell) + "</td><td>" + esc(s.started) + "</td><td>" + esc(s.last) + "</td><td class='muted'>" + esc(s.landing) + "</td><td class='muted'>" + esc(s.referrer) + "</td></tr>";
      }).join("");
    }
  }
  function load() {
    fetch("/admin/analytics.json", { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () {});
  }
  document.querySelectorAll("[data-an-range]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      range = parseInt(btn.getAttribute("data-an-range"), 10) || 7;
      document.querySelectorAll("[data-an-range]").forEach(function (b) {
        var on = b === btn;
        b.classList.toggle("is-on", on);
        b.classList.toggle("btn-ghost", !on);
      });
      load();
    });
  });
  document.addEventListener("click", function (e) {
    var tab = e.target.closest && e.target.closest('a.admin-tab[href="#stats"], a.admin-tab[data-tab="stats"]');
    if (tab) load();
  });
  if (location.hash === "#stats" || (root.offsetParent !== null)) load();
  setInterval(function () {
    if (location.hash === "#stats") load();
  }, 30000);
})();


  (function settingsForm() {
    var form = document.getElementById("anAdminPlaysForm");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var csrfEl = form.querySelector('input[name="csrf_token"]');
      var box = document.getElementById("anCountAdminPlays");
      var body = { csrf_token: csrfEl ? csrfEl.value : "", count_admin_plays: box && box.checked ? "1" : "0" };
      fetch("/admin/analytics/settings", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": body.csrf_token },
        body: JSON.stringify(body)
      }).then(function (r) { return r.json(); }).then(function (d) {
        var msg = document.getElementById("anAdminPlaysMsg");
        if (msg) msg.textContent = (d && d.message) || (d && d.ok ? "已保存" : "保存失败");
      }).catch(function () {
        var msg = document.getElementById("anAdminPlaysMsg");
        if (msg) msg.textContent = "保存失败，请重试";
      });
    });
  })();

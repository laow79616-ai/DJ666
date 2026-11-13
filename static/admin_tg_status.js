(function () {
  var statusEl = document.getElementById("tgCollectStatus");
  if (!statusEl) return;
  var wasRunning = statusEl.getAttribute("data-running") === "1";
  var live = document.getElementById("tgCollectLive");
  var fetchBtn = document.getElementById("tgFetchBtn");
  var pubBtn = document.getElementById("tgPublishAllBtn");
  var pubForm = document.getElementById("tgPublishAllForm");
  var refreshBtn = document.getElementById("tgRefreshList");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () { location.reload(); });
  }

  function setTotals(n) {
    document.querySelectorAll("[data-tg-new-total]").forEach(function (el) {
      el.textContent = String(n);
    });
    var badge = document.getElementById("tgTabBadge");
    if (badge) badge.hidden = !n;
    var head = document.getElementById("tgHeadCount");
    if (head) head.hidden = !n;
    if (pubBtn && pubBtn.tagName === "BUTTON" && pubForm) {
      pubBtn.textContent = "一键发布（" + n + "）";
    }
    if (pubForm) {
      pubForm.onsubmit = function () {
        var tpl = pubForm.getAttribute("data-confirm-tpl") || "将把当前全部 {n} 条已过滤的待处理内容发布到公告首页。确定发布？";
        return confirm(tpl.replace("{n}", String(n)));
      };
    }
  }

  function apply(d) {
    var run = !!d.running;
    var line = "上次采集：" + (d.last_run || "尚未采集");
    if (d.last_result) line += " · " + d.last_result;
    if (run) line += " · 正在采集（进度会自动更新，页面不再整页刷新）";
    statusEl.textContent = line;
    statusEl.setAttribute("data-running", run ? "1" : "0");
    var autoEl = document.getElementById("tgAutoStatus");
    if (autoEl) {
      var ap = d.auto_publish ? ("已开启（延迟 " + (d.auto_publish_delay || 60) + " 分钟）") : "已关闭";
      autoEl.textContent = "自动发布：" + ap
        + " · 上次自动发布：" + (d.last_auto_publish || "尚未自动发布")
        + " · 上次互动同步：" + (d.last_eng_sync || "尚未同步");
    }
    var cmtEl = document.getElementById("tgCmtStatus");
    if (cmtEl && d.cmt_status) cmtEl.textContent = d.cmt_status;
    setTotals(d.new_total || 0);
    if (fetchBtn) {
      fetchBtn.disabled = run;
      if (!run && fetchBtn.textContent === "采集中…") fetchBtn.textContent = "立即采集";
    }
    if (pubBtn && pubForm) pubBtn.disabled = run || !(d.new_total);
    if (live) {
      if (run) {
        live.hidden = false;
        if (!live.querySelector("#tgRefreshList")) {
          /* keep default running copy */
        }
      } else if (wasRunning && !run) {
        live.hidden = false;
        live.innerHTML = "采集已结束。待处理现为 " + (d.new_total || 0) + " 条。<button type=\"button\" class=\"link-btn\" id=\"tgRefreshList\">刷新列表</button>";
        var b = document.getElementById("tgRefreshList");
        if (b) b.addEventListener("click", function () { location.reload(); });
      }
    }
    wasRunning = run;
  }

  function tick() {
    fetch("/admin/telegram/status", {
      credentials: "same-origin",
      headers: { "Accept": "application/json", "X-Requested-With": "dj666-ajax" }
    })
      .then(function (r) {
        if (!r.ok) return null;
        return r.json();
      })
      .then(function (d) { if (d && d.ok) apply(d); })
      .catch(function () {});
  }

  tick();
  setInterval(tick, 5000);
})();

(function () {
  function linkRow(text, url) {
    var row = document.createElement("div"); row.className = "tg-edit-link-row";
    var t = document.createElement("input"); t.type = "text"; t.name = "link_text"; t.maxLength = 80; t.placeholder = "文案（如：点击查看详情）"; t.value = text || "";
    var u = document.createElement("input"); u.type = "text"; u.name = "link_url"; u.maxLength = 500; u.placeholder = "链接 https://…"; u.inputMode = "url"; u.autocomplete = "off"; u.value = url || "";
    var d = document.createElement("button"); d.type = "button"; d.className = "link-btn danger"; d.setAttribute("data-tg-link-del", ""); d.textContent = "删除";
    row.appendChild(t); row.appendChild(u); row.appendChild(d); return row;
  }
  document.addEventListener("click", function (e) {
    var add = e.target.closest("[data-tg-link-add]");
    if (add) {
      var box = add.parentNode.querySelector("[data-tg-links]");
      if (box && box.children.length < 10) { var r = linkRow(); box.appendChild(r); r.querySelector("input").focus(); }
      return;
    }
    var del = e.target.closest("[data-tg-link-del]");
    if (del) { var rr = del.closest(".tg-edit-link-row"); if (rr) rr.remove(); return; }
  });
})();

(function () {
  document.addEventListener("click", function (e) {
    var openBtn = e.target.closest("[data-tg-text-edit]");
    if (openBtn) {
      var id = openBtn.getAttribute("data-tg-text-edit");
      var form = document.getElementById("tg-edit-" + id);
      if (!form) return;
      var show = form.hasAttribute("hidden");
      form.hidden = !show;
      openBtn.setAttribute("aria-expanded", show ? "true" : "false");
      if (show) {
        var ta = form.querySelector("textarea");
        if (ta) ta.focus();
      }
      return;
    }
    var cancel = e.target.closest("[data-tg-text-edit-cancel]");
    if (cancel) {
      var cid = cancel.getAttribute("data-tg-text-edit-cancel");
      var cform = document.getElementById("tg-edit-" + cid);
      if (cform) cform.hidden = true;
      var btn = document.querySelector('[data-tg-text-edit="' + cid + '"]');
      if (btn) btn.setAttribute("aria-expanded", "false");
    }
  });
})();

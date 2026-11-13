/* Likes & comments under announcements. Delegated on document so it survives pjax page swaps. */
(function () {
  function ajax(form) {
    return fetch(form.action, {
      method: "POST", body: new FormData(form), credentials: "same-origin",
      headers: { "X-Requested-With": "dj666-ajax", "Accept": "application/json" }
    }).then(function (res) {
      return res.json().catch(function () { return { ok: false, error: "请求失败，请刷新页面后重试" }; })
        .then(function (data) { data._status = res.status; return data; });
    });
  }
  function toLogin(data) { location.href = (data && data.login) || "/login"; }
  function setCount(bar, sel, n) { var el = bar && bar.querySelector(sel); if (el) el.textContent = n; }
  function csrfOf(form) { var i = form.querySelector("input[name=csrf_token]"); return i ? i.value : ""; }
  function showMsg(form, text, ok) {
    var m = form.querySelector(".ann-cmt-msg"); if (!m) return;
    m.textContent = text || ""; m.classList.toggle("ok", !!ok);
  }
  function buildComment(c, csrf) {
    var li = document.createElement("li"); li.className = "ann-cmt"; li.dataset.cmtId = c.id;
    var head = document.createElement("div"); head.className = "ann-cmt-head";
    var b = document.createElement("b"); b.textContent = c.username;
    var t = document.createElement("span"); t.textContent = c.time;
    head.appendChild(b); head.appendChild(t);
    if (c.can_delete) {
      var f = document.createElement("form"); f.className = "ann-cmt-del"; f.method = "post";
      f.action = "/announcements/comments/" + encodeURIComponent(c.id) + "/delete"; f.setAttribute("data-no-pjax", "");
      var h = document.createElement("input"); h.type = "hidden"; h.name = "csrf_token"; h.value = csrf;
      var btn = document.createElement("button"); btn.type = "submit"; btn.className = "link-btn danger"; btn.textContent = "删除";
      f.appendChild(h); f.appendChild(btn); head.appendChild(f);
    }
    var p = document.createElement("p"); p.className = "ann-cmt-text"; p.textContent = c.content;
    li.appendChild(head); li.appendChild(p); return li;
  }
  function openPanel(post, open) {
    var btn = post.querySelector(".ann-cmt-toggle"), panel = post.querySelector(".ann-comments");
    if (!btn || !panel) return;
    panel.hidden = !open; btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.classList.toggle("is-open", open);
    if (open) loadSynced(post, false);
  }
  /* Synced source comments (labelled dj666), loaded lazily when the panel opens; merged by time. */
  function buildSynced(c) {
    var li = document.createElement("li"); li.className = "ann-cmt ann-cmt-synced"; li.dataset.ts = c.ts || 0;
    var head = document.createElement("div"); head.className = "ann-cmt-head";
    var b = document.createElement("b"); b.textContent = c.author || "网友";
    var tag = document.createElement("em"); tag.className = "ann-cmt-src"; tag.textContent = "dj666";
    var t = document.createElement("span"); t.textContent = c.time || "";
    head.appendChild(b); head.appendChild(tag); head.appendChild(t);
    var p = document.createElement("p"); p.className = "ann-cmt-text";
    if (c.reply) { var r = document.createElement("span"); r.className = "ann-cmt-re"; r.textContent = "回复 " + c.reply + "："; p.appendChild(r); }
    p.appendChild(document.createTextNode(c.text || ""));
    li.appendChild(head); li.appendChild(p); return li;
  }
  function insertByTime(list, li) {
    var ts = Number(li.dataset.ts) || 0, kids = list.children;
    for (var i = 0; i < kids.length; i++) {
      var k = Number(kids[i].dataset.ts);
      if (!isNaN(k) && k > ts) { list.insertBefore(li, kids[i]); return; }
    }
    list.appendChild(li);
  }
  function loadSynced(post, more) {
    var panel = post.querySelector(".ann-comments"); if (!panel) return;
    if (panel._tgLoading || (panel._tgLoaded && !more)) return;
    var bar = post.querySelector(".ann-bar"); var id = bar && bar.getAttribute("data-ann-id"); if (!id) return;
    panel._tgLoading = true;
    var offset = panel._tgNext || 0;
    var moreBtn = panel.querySelector(".ann-cmt-more-btn");
    if (moreBtn) moreBtn.disabled = true;
    fetch("/announcements/" + encodeURIComponent(id) + "/tg-comments?offset=" + offset, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        panel._tgLoaded = true;
        if (!d || !d.ok) return;
        var list = panel.querySelector(".ann-cmt-list");
        (d.items || []).forEach(function (c) { insertByTime(list, buildSynced(c)); });
        if ((d.items || []).length) { var empty = panel.querySelector(".ann-cmt-empty"); if (empty) empty.hidden = true; }
        panel._tgNext = d.next;
        if (d.next != null) {
          if (!moreBtn) {
            moreBtn = document.createElement("button"); moreBtn.type = "button"; moreBtn.className = "btn btn-sm btn-ghost ann-cmt-more-btn";
            moreBtn.addEventListener("click", function () { loadSynced(post, true); });
            list.parentNode.insertBefore(moreBtn, list.nextSibling);
          }
          moreBtn.textContent = "更多评论（还有 " + (d.total - d.next) + " 条）";
          moreBtn.disabled = false; moreBtn.hidden = false;
        } else if (moreBtn) { moreBtn.hidden = true; }
      })
      .catch(function () { panel._tgLoaded = false; if (moreBtn) moreBtn.disabled = false; })
      .then(function () { panel._tgLoading = false; });
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest(".ann-cmt-toggle");
    if (!t) return;
    var post = t.closest(".tg-post"); if (!post) return;
    var panel = post.querySelector(".ann-comments");
    openPanel(post, panel && panel.hidden);
    if (panel && !panel.hidden) { var ta = panel.querySelector("textarea"); if (ta && window.matchMedia("(hover: hover)").matches) ta.focus(); }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.matches && e.target.matches(".ann-cmt-form textarea")) {
      e.preventDefault(); var f = e.target.form; if (f.requestSubmit) f.requestSubmit(); else f.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    }
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || !window.fetch) return;
    var post = form.closest(".tg-post"); if (!post) return;
    var bar = post.querySelector(".ann-bar");
    if (form.classList.contains("ann-like-form")) {
      e.preventDefault();
      var btn = form.querySelector("button"); if (btn.disabled) return; btn.disabled = true;
      ajax(form).then(function (d) {
        if (d._status === 401) return toLogin(d);
        if (!d.ok) { alert(d.error || "操作失败"); return; }
        btn.classList.toggle("is-liked", d.liked); btn.setAttribute("aria-pressed", d.liked ? "true" : "false");
        btn.title = d.liked ? "取消点赞" : "点赞";
        if (d.liked) { btn.classList.remove("pop"); void btn.offsetWidth; btn.classList.add("pop"); }
        setCount(bar, ".ann-like-count", d.likes_text != null ? d.likes_text : d.likes);
      }).catch(function () { HTMLFormElement.prototype.submit.call(form); })
        .then(function () { btn.disabled = false; });
    } else if (form.classList.contains("ann-cmt-form")) {
      e.preventDefault();
      var ta = form.querySelector("textarea"), send = form.querySelector("button[type=submit]");
      if (!ta.value.trim()) { showMsg(form, "评论不能为空"); return; }
      if (send.disabled) return; send.disabled = true; showMsg(form, "发送中…", true);
      ajax(form).then(function (d) {
        if (d._status === 401) return toLogin(d);
        if (!d.ok) { showMsg(form, d.error || "发送失败"); return; }
        var list = post.querySelector(".ann-cmt-list");
        list.appendChild(buildComment(d.comment, csrfOf(form)));
        var empty = post.querySelector(".ann-cmt-empty"); if (empty) empty.hidden = true;
        setCount(bar, ".ann-cmt-count", d.comments_text != null ? d.comments_text : d.comments);
        ta.value = ""; showMsg(form, "已发送", true);
        setTimeout(function () { showMsg(form, ""); }, 1800);
      }).catch(function () { showMsg(form, "网络错误，请重试"); })
        .then(function () { send.disabled = false; });
    } else if (form.classList.contains("ann-cmt-del")) {
      e.preventDefault();
      if (!confirm("确定删除这条评论？")) return;
      ajax(form).then(function (d) {
        if (d._status === 401) return toLogin(d);
        if (!d.ok) { alert(d.error || "删除失败"); return; }
        var li = form.closest(".ann-cmt"); if (li) li.remove();
        setCount(bar, ".ann-cmt-count", d.comments_text != null ? d.comments_text : d.comments);
        var empty = post.querySelector(".ann-cmt-empty");
        if (empty && !post.querySelector(".ann-cmt")) empty.hidden = false;
      }).catch(function () { alert("网络错误，请重试"); });
    }
  });

  // Open the comment panel when arriving at /#ann-ID (e.g. after login or no-JS fallback).
  function fromHash() {
    var m = /^#ann-(\d+)$/.exec(location.hash || ""); if (!m) return;
    var post = document.getElementById("ann-" + m[1]); if (!post) return;
    openPanel(post, true);
    try { post.scrollIntoView({ block: "start" }); } catch (e) {}
  }
  fromHash();
  window.addEventListener("hashchange", fromHash);
  new MutationObserver(function () { if (/^#ann-\d+$/.test(location.hash)) { var p = document.getElementById(location.hash.slice(1)); if (p && !p.dataset.annSeen) { p.dataset.annSeen = "1"; fromHash(); } } })
    .observe(document.getElementById("appMain") || document.body, { childList: true });
})();

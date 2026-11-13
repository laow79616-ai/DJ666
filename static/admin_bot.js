/* DJ666 admin 机器人配置: ad image picker preview + inline keyboard editor. */
(function () {
  // --- ad images: pick → preview + explicit 「上传并保存」 → saved list updates in place
  (function () {
    var form = document.getElementById("botAdForm"), files = document.getElementById("botAdFiles");
    var pend = document.getElementById("botAdPending"), btn = document.getElementById("botAdSubmit");
    var clr = document.getElementById("botAdClear"), stat = document.getElementById("botAdStatus");
    var info = document.getElementById("botAdPickInfo"), grid = document.getElementById("botAdGrid");
    var empty = document.getElementById("botAdEmpty");
    if (!form || !files || !btn) return;
    var MAXB = +form.dataset.maxBytes || 10485760, OK = /\.(jpe?g|png|gif|webp)$/i, busy = false;
    var csrf = (form.querySelector('[name="csrf_token"]') || {}).value || "";
    function kb(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
    function picked() { return Array.prototype.slice.call(files.files || []); }
    function say(msg, kind) { stat.textContent = msg || ""; stat.className = "bot-ad-status" + (kind ? " is-" + kind : ""); }
    function refresh() {
      var list = picked(), good = 0;
      pend.innerHTML = "";
      pend.hidden = !list.length;
      list.forEach(function (f) {
        var fig = document.createElement("figure"), cap = document.createElement("figcaption");
        var why = !OK.test(f.name) ? "格式不支持，将跳过" : f.size > MAXB ? "超过 10MB，将跳过" : "";
        if (!why) good++;
        cap.textContent = f.name + " · " + kb(f.size) + (why ? "（" + why + "）" : "");
        if (why) fig.className = "is-bad";
        if (!why && /^image\//.test(f.type)) { var img = document.createElement("img"); img.alt = ""; img.src = URL.createObjectURL(f); fig.appendChild(img); }
        else { var ph = document.createElement("div"); ph.className = "bot-ad-ph"; ph.textContent = why ? "✕" : "🖼"; fig.appendChild(ph); }
        fig.appendChild(cap); pend.appendChild(fig);
      });
      form.classList.toggle("has-files", list.length > 0);
      info.textContent = list.length ? "已选择 " + list.length + " 个文件，点下方「上传并保存」" : "点这里选择一张或多张图片";
      btn.textContent = list.length ? "② 上传并保存（" + list.length + " 张）" : "② 上传并保存";
      if (clr) clr.hidden = !list.length;
      if (list.length) say(good ? "还没有保存，请点「上传并保存」。" : "所选文件都不是支持的图片。", good ? "warn" : "error");
      else say("");
    }
    function adItem(ad) {
      var li = document.createElement("li"); li.className = "bot-ad"; li.dataset.file = ad.file;
      var a = document.createElement("a"); a.className = "bot-ad-thumb"; a.href = ad.url; a.target = "_blank"; a.rel = "noopener"; a.title = "查看原图";
      var img = document.createElement("img"); img.src = ad.url; img.alt = ad.name; img.loading = "lazy"; a.appendChild(img);
      var meta = document.createElement("div"); meta.className = "bot-ad-meta";
      var nm = document.createElement("span"); nm.className = "bot-ad-name"; nm.title = ad.name; nm.textContent = ad.name;
      var sub = document.createElement("span"); sub.className = "muted";
      sub.textContent = (ad.w ? ad.w + "×" + ad.h + " · " : "") + kb(ad.size) + (ad.uploaded ? " · " + ad.uploaded : "");
      meta.appendChild(nm); meta.appendChild(sub);
      var del = document.createElement("form"); del.className = "inline-form"; del.method = "post";
      del.action = "/admin/bot/ads/" + encodeURIComponent(ad.file) + "/delete";
      del.addEventListener("submit", function (e) { if (!confirm("确定删除这张广告图片？")) e.preventDefault(); });
      var h = document.createElement("input"); h.type = "hidden"; h.name = "csrf_token"; h.value = csrf;
      var b = document.createElement("button"); b.type = "submit"; b.className = "btn btn-sm btn-ghost"; b.textContent = "删除";
      del.appendChild(h); del.appendChild(b);
      li.appendChild(a); li.appendChild(meta); li.appendChild(del);
      return li;
    }
    function renderAds(ads, fresh) {
      if (!grid) return;
      grid.innerHTML = "";
      ads.forEach(function (ad) { var li = adItem(ad); if (fresh[ad.file]) li.classList.add("is-new"); grid.appendChild(li); });
      grid.hidden = !ads.length; if (empty) empty.hidden = !!ads.length;
      ["botAdCount", "botAdCount2"].forEach(function (id) { var c = document.getElementById(id); if (c) c.textContent = ads.length + " 张"; });
    }
    files.addEventListener("change", refresh);
    if (clr) clr.addEventListener("click", function () { files.value = ""; refresh(); });
    form.addEventListener("submit", function (e) {
      if (!window.fetch || !window.FormData) return;          // old browsers: normal form post + redirect
      e.preventDefault();
      if (busy) return;
      var list = picked();
      if (!list.length) { say("请先点「选择图片」选择要上传的图片。", "error"); files.click(); return; }
      var before = {}; if (grid) Array.prototype.forEach.call(grid.querySelectorAll(".bot-ad"), function (li) { before[li.dataset.file] = 1; });
      busy = true; btn.disabled = true; btn.textContent = "上传中…"; say("正在上传 " + list.length + " 个文件…", "warn");
      var fd = new FormData(form);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", form.action);
      xhr.setRequestHeader("X-Requested-With", "fetch");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) say("正在上传… " + Math.round(ev.loaded * 100 / ev.total) + "%", "warn"); };
      xhr.onload = function () {
        busy = false; btn.disabled = false;
        var data = null; try { data = JSON.parse(xhr.responseText); } catch (err) {}
        if (xhr.status !== 200 || !data || !data.ads) {
          var d = data && data.detail; btn.textContent = "② 上传并保存（" + list.length + " 张）";
          say("上传失败：" + (typeof d === "string" ? d : xhr.status === 413 ? "文件太大" : xhr.status === 403 || xhr.status === 401 ? "没有权限，请重新登录" : "服务器错误（" + xhr.status + "），请刷新后重试"), "error");
          return;
        }
        var fresh = {}; data.ads.forEach(function (ad) { if (!before[ad.file]) fresh[ad.file] = 1; });
        renderAds(data.ads, fresh);
        files.value = ""; refresh();
        var msg = data.added ? "✓ 已保存 " + data.added + " 张图片，已加入下方列表。" : "没有保存任何图片。";
        if (data.bad && data.bad.length) msg += " 未上传：" + data.bad.slice(0, 5).join("；") + (data.bad.length > 5 ? " 等 " + data.bad.length + " 个" : "");
        say(msg, data.added ? (data.bad && data.bad.length ? "warn" : "ok") : "error");
        if (data.added && grid) { var n = grid.querySelector(".is-new"); if (n && n.scrollIntoView) n.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
      };
      xhr.onerror = function () { busy = false; btn.disabled = false; btn.textContent = "② 上传并保存（" + list.length + " 张）"; say("网络错误，上传失败，请重试。", "error"); };
      xhr.send(fd);
    });
    refresh();
  })();

  // --- inline buttons editor
  var form = document.getElementById("botBtnForm"), rows = document.getElementById("botBtnRows");
  var add = document.getElementById("botBtnAdd"), prev = document.getElementById("botKbPreview");
  if (!form || !rows) return;
  var MAX = +form.dataset.max || 30;
  function safeUrl(u) {
    u = (u || "").trim();
    if (/^(t\.me|telegram\.me)\//i.test(u)) u = "https://" + u;
    return /^https?:\/\/[A-Za-z0-9.-]+\.[A-Za-z0-9.-]+(:\d{1,5})?([\/?#]\S*)?$/i.test(u) ? u : "";
  }
  function all() { return Array.prototype.slice.call(rows.querySelectorAll(".bot-btn-row")); }
  function blank() {
    var tpl = all()[0];
    var r = tpl ? tpl.cloneNode(true) : null;
    if (!r) return null;
    r.querySelectorAll("input").forEach(function (i) { i.value = ""; i.classList.remove("is-bad"); });
    r.querySelector("select").value = "new";
    return r;
  }
  var tplRow = all()[0] ? all()[0].cloneNode(true) : null;
  function render() {
    var list = all();
    prev.innerHTML = "";
    var line = null, n = 0, count = 0;
    list.forEach(function (r, idx) {
      var t = r.querySelector('[name="btn_text"]').value.trim(), uRaw = r.querySelector('[name="btn_url"]').value, u = safeUrl(uRaw);
      var sel = r.querySelector("select"); sel.disabled = false;
      r.querySelector('[name="btn_url"]').classList.toggle("is-bad", !!uRaw.trim() && !u);
      if (!t || !u) return;
      count++;
      if (!line || sel.value !== "same" || n >= 8) { line = document.createElement("div"); line.className = "bot-kb-row"; prev.appendChild(line); n = 0; }
      var b = document.createElement("a"); b.className = "bot-kb-btn"; b.href = u; b.target = "_blank"; b.rel = "noopener noreferrer";
      b.textContent = t; b.title = u; line.appendChild(b); n++;
    });
    if (!count) { var e = document.createElement("span"); e.className = "muted"; e.textContent = "（还没有有效的按钮）"; prev.appendChild(e); }
    if (add) add.disabled = list.length >= MAX;
  }
  if (add) add.addEventListener("click", function () {
    if (all().length >= MAX) return;
    var r = blank() || (tplRow && tplRow.cloneNode(true));
    if (!r) return;
    r.querySelectorAll("input").forEach(function (i) { i.value = ""; });
    rows.appendChild(r); r.querySelector("input").focus(); render();
  });
  rows.addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b) return;
    var r = b.closest(".bot-btn-row");
    if (b.hasAttribute("data-btn-del")) {
      if (all().length === 1) { r.querySelectorAll("input").forEach(function (i) { i.value = ""; }); r.querySelector("select").value = "new"; }
      else r.remove();
    } else if (b.hasAttribute("data-btn-up") && r.previousElementSibling) rows.insertBefore(r, r.previousElementSibling);
    else if (b.hasAttribute("data-btn-down") && r.nextElementSibling) rows.insertBefore(r.nextElementSibling, r);
    render();
  });
  rows.addEventListener("input", render);
  rows.addEventListener("change", render);
  form.addEventListener("submit", function (e) {
    var list = all();
    for (var i = 0; i < list.length; i++) {
      var t = list[i].querySelector('[name="btn_text"]').value.trim(), u = list[i].querySelector('[name="btn_url"]').value.trim();
      if (!t && !u) continue;
      if (!t) { e.preventDefault(); alert("第 " + (i + 1) + " 行缺少按钮文字。"); return; }
      if (!safeUrl(u)) { e.preventDefault(); alert("第 " + (i + 1) + " 行链接无效，只支持 http:// 或 https:// 开头的网址。"); return; }
    }
  });
  render();
})();

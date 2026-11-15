/* DJ666 admin: Telegram-style announcement composer. */
(function () {
  var form = document.getElementById("annForm");
  if (!form) return;
  var MAX = 10;
  var csrf = form.querySelector('input[name="csrf_token"]').value;
  var fileInput = document.getElementById("annFiles");
  var tray = document.getElementById("annTray");
  var inputs = document.getElementById("annMediaInputs");
  var countEl = document.getElementById("annCount");
  var sendBtn = document.getElementById("annSend");
  var titleEl = document.getElementById("annTitle");
  var contentEl = document.getElementById("annContent");
  var modeEl = document.getElementById("annMode");
  var cancelBtn = document.getElementById("annCancelEdit");
  var tgIdEl = document.getElementById("annTgId");
  var drop = document.getElementById("annDrop");
  var pMedia = document.getElementById("annPreviewMedia");
  var pText = document.getElementById("annPreviewText");
  var linkRows = document.getElementById("annLinkRows");
  var linkAdd = document.getElementById("annLinkAdd");
  var MAX_LINKS = 10;
  var annData = {}, tgData = {};
  try { JSON.parse(document.getElementById("annData").textContent).forEach(function (a) { annData[a.id] = a; }); } catch (e) {}
  try { tgData = JSON.parse(document.getElementById("tgData").textContent) || {}; } catch (e) {}

  var items = []; // {key, file, type, url, poster, status:'up'|'ok'|'err', progress, xhr, local}
  var seq = 0;
  var IMG = /\.(jpe?g|png|gif|webp)$/i, VID = /\.(mp4|webm|mov|m4v)$/i;

  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
  function linkify(s) {
    return esc(s).replace(/(https?:\/\/[^\s<>"'）)】，。]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  }
  function fmtSize(n) {
    if (n > 1073741824) return (n / 1073741824).toFixed(2) + " GB";
    if (n > 1048576) return (n / 1048576).toFixed(1) + " MB";
    return Math.max(1, Math.round(n / 1024)) + " KB";
  }

  function linkLabel(u) {
    var l = u.replace(/^https?:\/\//i, "").replace(/^www\./i, "").replace(/\/$/, "");
    return l.length > 60 ? l.slice(0, 57) + "…" : l;
  }
  function safeUrl(u) {
    u = (u || "").trim();
    if (!u) return "";
    if (!/^[a-z][a-z0-9+.-]*:/i.test(u)) { if (/^[^\s\/]+\.[^\s]+$/.test(u)) u = "https://" + u; else return ""; }
    return /^https?:\/\/[^\s<>"'`]+$/i.test(u) ? u : "";
  }
  function addLinkRow(text, url) {
    if (!linkRows) return;
    if (linkRows.children.length >= MAX_LINKS) { alert("每条公告最多 " + MAX_LINKS + " 行文案链接。"); return; }
    var row = document.createElement("div"); row.className = "ann-link-row";
    var t = document.createElement("input"); t.type = "text"; t.name = "link_text"; t.maxLength = 80; t.placeholder = "文案（如：点击收听新歌）"; t.value = text || "";
    var u = document.createElement("input"); u.type = "text"; u.name = "link_url"; u.maxLength = 500; u.placeholder = "链接 https://…"; u.value = url || ""; u.inputMode = "url"; u.autocomplete = "off";
    var x = document.createElement("button"); x.type = "button"; x.className = "ann-link-x"; x.title = "删除这一行"; x.textContent = "×";
    x.addEventListener("click", function () { row.remove(); renderPreview(); });
    t.addEventListener("input", renderPreview); u.addEventListener("input", renderPreview);
    row.appendChild(t); row.appendChild(u); row.appendChild(x); linkRows.appendChild(row);
    return row;
  }
  function setLinks(list) {
    if (!linkRows) return;
    linkRows.innerHTML = "";
    (list || []).forEach(function (l) { addLinkRow(l.text, l.url); });
  }
  function currentLinks() {
    if (!linkRows) return [];
    return Array.prototype.map.call(linkRows.querySelectorAll(".ann-link-row"), function (r) {
      return { text: r.querySelector('[name="link_text"]').value.trim(), url: r.querySelector('[name="link_url"]').value.trim() };
    }).filter(function (l) { return l.text || l.url; });
  }
  if (linkAdd) linkAdd.addEventListener("click", function () { var r = addLinkRow("", ""); if (r) r.querySelector("input").focus(); });

  function render() {
    countEl.textContent = items.length;
    countEl.parentNode.classList.toggle("full", items.length >= MAX);
    tray.hidden = items.length === 0;
    tray.innerHTML = "";
    inputs.innerHTML = "";
    items.forEach(function (it, i) {
      var el = document.createElement("div");
      el.className = "tg-thumb" + (it.status === "err" ? " err" : "");
      el.draggable = true;
      el.dataset.index = i;
      var src = it.url || it.local || "";
      var inner = it.type === "video"
        ? '<video src="' + src + '" muted preload="metadata"' + (it.poster ? ' poster="' + it.poster + '"' : '') + '></video><span class="tg-thumb-badge">▶ 视频</span>'
        : '<img src="' + src + '" alt="">';
      inner += '<button type="button" class="tg-thumb-x" title="移除" data-act="rm">×</button>';
      inner += '<div class="tg-thumb-nav"><button type="button" data-act="left" title="前移">◀</button><span>' + (i + 1) + '</span><button type="button" data-act="right" title="后移">▶</button></div>';
      if (it.status === "up") inner += '<div class="tg-thumb-prog"><i style="width:' + (it.progress || 0) + '%"></i><span>' + (it.progress || 0) + '%</span></div>';
      if (it.status === "err") inner += '<div class="tg-thumb-err">' + esc(it.error || "上传失败") + '</div>';
      el.innerHTML = inner;
      tray.appendChild(el);
      if (it.status === "ok") {
        var h = document.createElement("input");
        h.type = "hidden"; h.name = "media"; h.value = it.file;
        inputs.appendChild(h);
      }
    });
    var uploading = items.some(function (it) { return it.status === "up"; });
    sendBtn.disabled = uploading;
    sendBtn.title = uploading ? "等待上传完成…" : "发布";
    renderPreview();
  }

  function renderPreview() {
    var ok = items.filter(function (it) { return it.status !== "err"; });
    var c = ok.length;
    if (!c) { pMedia.innerHTML = ""; }
    else {
      var big = c <= 4 ? 0 : (c % 3 === 0 ? 0 : (c % 3 === 1 ? 4 : 2));
      var html = '<div class="tg-media tg-n' + (c <= 4 ? c : "many") + '">';
      ok.forEach(function (it, i) {
        var src = it.url || it.local || "";
        var cls = "tg-cell" + (i < big ? " big" : "");
        if (it.type === "video") html += '<div class="' + cls + ' is-video"><video src="' + src + '" controls preload="none" playsinline' + (it.poster ? ' poster="' + it.poster + '"' : '') + '></video></div>';
        else html += '<a class="' + cls + '" href="' + src + '" data-lightbox><img src="' + src + '" alt=""></a>';
      });
      pMedia.innerHTML = html + "</div>";
    }
    var t = titleEl.value.trim(), body = contentEl.value.trim();
    var links = currentLinks(), lh = "";
    if (links.length) {
      lh = '<ul class="ann-links">';
      links.forEach(function (l) {
        var u = safeUrl(l.url);
        var label = esc(l.text || (u ? linkLabel(u) : l.url));
        lh += u
          ? '<li><a class="ann-link" href="' + esc(u) + '" target="_blank" rel="noopener noreferrer"><span class="ann-link-ico" aria-hidden="true">🔗</span><span class="ann-link-text">' + label + '</span><span class="ann-link-go" aria-hidden="true">↗</span></a></li>'
          : '<li><span class="ann-link is-bad"><span class="ann-link-ico" aria-hidden="true">⚠</span><span class="ann-link-text">' + label + '（链接无效）</span></span></li>';
      });
      lh += "</ul>";
    }
    if (!t && !body) pText.innerHTML = (c || links.length ? "" : '<span class="muted">在下方输入内容、添加图片或视频，这里实时预览公告页效果。</span>') + lh;
    else pText.innerHTML = (t ? '<strong class="tg-title">' + esc(t) + "</strong>" : "") + (body ? '<div class="ann-body">' + linkify(body) + "</div>" : "") + lh;
    document.getElementById("annPreview").classList.toggle("has-media", c > 0);
  }

  function addFiles(list) {
    var files = Array.prototype.slice.call(list || []);
    var room = MAX - items.length;
    if (files.length > room) {
      alert("每条公告最多 " + MAX + " 个图片或视频，已忽略多出的 " + (files.length - Math.max(room, 0)) + " 个。");
      files = files.slice(0, Math.max(room, 0));
    }
    files.forEach(function (f) {
      var type = IMG.test(f.name) || /^image\//.test(f.type) ? "image" : (VID.test(f.name) || /^video\//.test(f.type) ? "video" : null);
      if (!type) { alert("不支持的文件：" + f.name); return; }
      var it = { key: ++seq, type: type, status: "up", progress: 0, local: URL.createObjectURL(f), name: f.name };
      items.push(it);
      upload(it, f);
    });
    render();
  }

  function upload(it, f) {
    var xhr = new XMLHttpRequest();
    it.xhr = xhr;
    xhr.open("POST", "/admin/announcements/media");
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    var name = f.name;
    if (!/\.[a-z0-9]+$/i.test(name)) name += it.type === "video" ? ".mp4" : ".jpg";
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    var last = 0;
    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      it.progress = Math.floor(e.loaded * 100 / e.total);
      var now = Date.now();
      if (now - last > 250 || it.progress === 100) { last = now; render(); }
    };
    xhr.onload = function () {
      var data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status === 200 && data && data.ok) {
        it.status = "ok"; it.file = data.file; it.url = data.url; it.type = data.type;
      } else {
        it.status = "err";
        it.error = (data && data.error) || (xhr.status === 413 ? "文件超过服务器上限" : "上传失败（" + xhr.status + "）");
      }
      render();
    };
    xhr.onerror = function () { it.status = "err"; it.error = "网络中断，上传失败"; render(); };
    xhr.send(f);
  }

  tray.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-act]");
    if (!btn) return;
    var i = +btn.closest(".tg-thumb").dataset.index;
    var act = btn.dataset.act;
    if (act === "rm") {
      var it = items[i];
      if (it.xhr && it.status === "up") it.xhr.abort();
      items.splice(i, 1);
    } else if (act === "left" && i > 0) {
      items.splice(i - 1, 0, items.splice(i, 1)[0]);
    } else if (act === "right" && i < items.length - 1) {
      items.splice(i + 1, 0, items.splice(i, 1)[0]);
    }
    render();
  });
  var dragFrom = null;
  tray.addEventListener("dragstart", function (e) {
    var t = e.target.closest(".tg-thumb"); if (!t) return;
    dragFrom = +t.dataset.index; e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", "x"); } catch (err) {}
  });
  tray.addEventListener("dragover", function (e) { if (dragFrom !== null) e.preventDefault(); });
  tray.addEventListener("drop", function (e) {
    if (dragFrom === null) return;
    e.preventDefault();
    var t = e.target.closest(".tg-thumb");
    if (t) { var to = +t.dataset.index; items.splice(to, 0, items.splice(dragFrom, 1)[0]); render(); }
    dragFrom = null;
  });
  tray.addEventListener("dragend", function () { dragFrom = null; });

  fileInput.addEventListener("change", function () { addFiles(fileInput.files); fileInput.value = ""; });
  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) {
      if (dragFrom !== null) return;
      if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0) { e.preventDefault(); drop.classList.add("drag"); }
    });
  });
  ["dragleave", "drop"].forEach(function (ev) { drop.addEventListener(ev, function () { drop.classList.remove("drag"); }); });
  drop.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); }
  });
  contentEl.addEventListener("paste", function (e) {
    var files = e.clipboardData && e.clipboardData.files;
    if (files && files.length) { e.preventDefault(); addFiles(files); }
  });

  function autosize() { contentEl.style.height = "auto"; contentEl.style.height = Math.min(contentEl.scrollHeight, 360) + "px"; }
  contentEl.addEventListener("input", function () { autosize(); renderPreview(); });
  titleEl.addEventListener("input", renderPreview);
  contentEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (!sendBtn.disabled) form.requestSubmit ? form.requestSubmit() : form.submit(); }
  });

  form.addEventListener("submit", function (e) {
    if (items.some(function (it) { return it.status === "up"; })) { e.preventDefault(); alert("还有文件在上传，请稍候。"); return; }
    if (items.some(function (it) { return it.status === "err"; }) && !confirm("有上传失败的文件，将忽略它们继续发布？")) { e.preventDefault(); return; }
    var okCount = items.filter(function (it) { return it.status === "ok"; }).length;
    var lk = currentLinks();
    for (var li = 0; li < lk.length; li++) {
      if (!safeUrl(lk[li].url)) { e.preventDefault(); alert("第 " + (li + 1) + " 行链接无效，只支持 http:// 或 https:// 开头的网址。"); return; }
    }
    if (!okCount && !contentEl.value.trim() && !titleEl.value.trim() && !lk.length) { e.preventDefault(); alert("请填写内容或添加图片/视频。"); return; }
    sendBtn.disabled = true;
  });

  function openSection() {
    if (window.adminShowTab) window.adminShowTab("announce");
    document.getElementById("annComposer").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function load(mode, opts) {
    items = (opts.media || []).map(function (m) { return { key: ++seq, file: m.file, type: m.type, url: m.url, poster: m.poster, status: "ok" }; });
    titleEl.value = opts.title || "";
    contentEl.value = opts.content || "";
    tgIdEl.value = opts.tgId || "";
    setLinks(opts.links || []);
    if (mode === "edit") {
      form.action = "/admin/announcements/" + opts.id + "/edit";
      modeEl.textContent = "正在手动编辑公告 #" + opts.id + " · 保存后公告页同步，日后自动发布将保留此内容";
      cancelBtn.hidden = false;
    } else {
      form.action = form.dataset.createAction;
      modeEl.textContent = opts.tgId ? "从 Telegram 采集内容载入 · 修改后发布" : "新公告 · 发布后显示在公告页";
      cancelBtn.hidden = !opts.tgId;
    }
    render(); autosize(); openSection();
    contentEl.focus();
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-ann-edit]");
    if (b) { var a = annData[b.dataset.annEdit]; if (a) load("edit", a); return; }
    var t = e.target.closest("[data-tg-edit]");
    if (t) { var p = tgData[t.dataset.tgEdit]; if (p) load("create", { title: p.title || "", content: p.text, media: p.media, links: p.links || [], tgId: t.dataset.tgEdit }); }
  });
  cancelBtn.addEventListener("click", function () { load("create", {}); });
  render();
})();

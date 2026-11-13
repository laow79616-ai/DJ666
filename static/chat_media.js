/* DJ666 public chat: attach images/videos (max 10 per message). */
(function () {
  var form = document.getElementById("chatForm");
  if (!form) return;
  var MAX = 10;
  var csrf = form.querySelector('input[name="csrf_token"]').value;
  var fileInput = document.getElementById("chatFiles");
  var tray = document.getElementById("chatTray");
  var inputs = document.getElementById("chatMediaInputs");
  var hint = document.getElementById("chatMediaHint");
  var countEl = document.getElementById("chatCount");
  var sendBtn = document.getElementById("chatSend");
  var content = document.getElementById("chatContent");
  var IMG = /\.(jpe?g|png|gif|webp)$/i, VID = /\.(mp4|webm|mov|m4v)$/i;
  var items = [];

  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
  function render() {
    tray.hidden = !items.length; hint.hidden = !items.length;
    countEl.textContent = items.length;
    tray.innerHTML = ""; inputs.innerHTML = "";
    items.forEach(function (it, i) {
      var el = document.createElement("div");
      el.className = "tg-thumb" + (it.status === "err" ? " err" : "");
      el.dataset.index = i;
      var src = it.local || it.url;
      var h = it.type === "video" ? '<video src="' + src + '" muted preload="metadata"></video><span class="tg-thumb-badge">▶ 视频</span>' : '<img src="' + src + '" alt="">';
      h += '<button type="button" class="tg-thumb-x" title="移除" data-act="rm">×</button>';
      if (it.status === "up") h += '<div class="tg-thumb-prog"><i style="width:' + it.progress + '%"></i><span>' + it.progress + '%</span></div>';
      if (it.status === "err") h += '<div class="tg-thumb-err">' + esc(it.error) + '</div>';
      el.innerHTML = h;
      tray.appendChild(el);
      if (it.status === "ok") {
        var inp = document.createElement("input");
        inp.type = "hidden"; inp.name = "media"; inp.value = it.file; inputs.appendChild(inp);
      }
    });
    var busy = items.some(function (it) { return it.status === "up"; });
    sendBtn.disabled = busy;
    sendBtn.textContent = busy ? "上传中…" : "发送";
  }
  function upload(it, f) {
    var xhr = new XMLHttpRequest();
    it.xhr = xhr;
    xhr.open("POST", "/chat/media");
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    var name = f.name;
    if (!/\.[a-z0-9]+$/i.test(name)) name += it.type === "video" ? ".mp4" : ".jpg";
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    var last = 0;
    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      it.progress = Math.floor(e.loaded * 100 / e.total);
      if (Date.now() - last > 250) { last = Date.now(); render(); }
    };
    xhr.onload = function () {
      var d = null; try { d = JSON.parse(xhr.responseText); } catch (e) {}
      if (xhr.status === 200 && d && d.ok) { it.status = "ok"; it.file = d.file; it.url = d.url; }
      else { it.status = "err"; it.error = (d && d.error) || (xhr.status === 413 ? "文件超过服务器上限" : "上传失败"); }
      render();
    };
    xhr.onerror = function () { it.status = "err"; it.error = "网络中断"; render(); };
    xhr.send(f);
  }
  function addFiles(list) {
    var files = Array.prototype.slice.call(list || []);
    var room = MAX - items.length;
    if (files.length > room) { alert("每条消息最多 " + MAX + " 个图片或视频。"); files = files.slice(0, Math.max(room, 0)); }
    files.forEach(function (f) {
      var type = IMG.test(f.name) || /^image\//.test(f.type) ? "image" : (VID.test(f.name) || /^video\//.test(f.type) ? "video" : null);
      if (!type) { alert("不支持的文件：" + f.name); return; }
      var it = { type: type, status: "up", progress: 0, local: URL.createObjectURL(f) };
      items.push(it); upload(it, f);
    });
    render();
  }
  fileInput.addEventListener("change", function () { addFiles(fileInput.files); fileInput.value = ""; });
  content.addEventListener("paste", function (e) {
    var f = e.clipboardData && e.clipboardData.files;
    if (f && f.length) { e.preventDefault(); addFiles(f); }
  });
  form.addEventListener("dragover", function (e) { if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0) e.preventDefault(); });
  form.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); } });
  tray.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-act]"); if (!b) return;
    var i = +b.closest(".tg-thumb").dataset.index;
    var it = items[i]; if (it.xhr && it.status === "up") it.xhr.abort();
    items.splice(i, 1); render();
  });
  form.addEventListener("submit", function (e) {
    if (items.some(function (it) { return it.status === "up"; })) { e.preventDefault(); alert("还有文件在上传，请稍候。"); return; }
    var ok = items.filter(function (it) { return it.status === "ok"; }).length;
    if (!ok && !content.value.trim()) { e.preventDefault(); content.focus(); return; }
    sendBtn.disabled = true;
  });
})();

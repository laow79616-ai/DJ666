/* DJ666 admin: multi-select batch audio upload (with duplicate 跳过/覆盖) + inline track rename. */
(function () {
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-rename-toggle]");
    if (!t) return;
    var cell = t.closest("td");
    var f = cell && cell.querySelector(".admin-rename-form");
    if (!f) return;
    f.hidden = !f.hidden;
    t.textContent = f.hidden ? "改名" : "取消";
    if (!f.hidden) { var i = f.querySelector('input[name="title"]'); if (i) { i.focus(); i.select(); } }
  });

  var form = document.getElementById("musicUploadForm");
  if (!form || !window.FormData || !window.XMLHttpRequest) return;
  var input = document.getElementById("musicUploadFiles");
  var titleEl = document.getElementById("musicUploadTitle");
  var btn = document.getElementById("musicUploadBtn");
  var box = document.getElementById("musicUploadStatus");
  var summary = document.getElementById("musicUploadSummary");
  var list = document.getElementById("musicUploadList");
  var reload = document.getElementById("musicUploadReload");
  var csrf = form.querySelector('input[name="csrf_token"]').value;
  var MAX = 50, MAX_BYTES = 50 * 1024 * 1024;
  var OK_EXT = /\.(mp3|wav|ogg|m4a|flac)$/i;
  var busy = false;

  function syncTitle() {
    var n = input.files ? input.files.length : 0;
    titleEl.disabled = n > 1;
    titleEl.placeholder = n > 1 ? "已选 " + n + " 个文件，将用文件名作为标题" : "曲目标题（可选，仅单个文件时生效）";
  }
  input.addEventListener("change", syncTitle);
  reload.addEventListener("click", function () { location.href = "/admin#tracks"; location.reload(); });

  function row(name) {
    var li = document.createElement("li");
    var n = document.createElement("span"); n.className = "ub-name"; n.textContent = name;
    var s = document.createElement("span"); s.className = "ub-state"; s.textContent = "等待中";
    li.appendChild(n); li.appendChild(s); list.appendChild(li);
    return { li: li, state: s };
  }
  function setState(r, cls, text) { r.li.className = cls; r.state.textContent = text; }

  var dupBar = null;
  var dupJobs = [];
  function dupInfo(res) {
    var m = (res.matches || [])[0];
    if (!m) return "与曲库已有曲目重复";
    var more = res.matches.length > 1 ? "（另有 " + (res.matches.length - 1) + " 首相同）" : "";
    return "与《" + m.title + "》重复：" + m.reason + " · " + m.status_label + " · " + m.uploader + " 上传" + more;
  }
  function decide(job, action) {
    if (job.state !== "pending" || job.busy) return Promise.resolve();
    if (action === "skip") {
      job.state = "skipped";
      job.wrap.remove();
      setState(job.r, "dup", "已跳过（未新增）");
      refreshDupBar();
      return Promise.resolve();
    }
    job.busy = true;
    job.skipBtn.disabled = job.overBtn.disabled = true;
    return sendOne(job.file, "", job.r, { on_duplicate: "overwrite", target_id: job.target }).then(function (good) {
      job.busy = false;
      if (good) { job.state = "overwritten"; job.wrap.remove(); reload.hidden = false; }
      else { job.skipBtn.disabled = job.overBtn.disabled = false; }
      refreshDupBar();
    });
  }
  function addDupChoice(job) {
    var w = document.createElement("div"); w.className = "ub-dup";
    var info = document.createElement("span"); info.className = "ub-dup-info"; info.textContent = dupInfo(job.res);
    var sk = document.createElement("button"); sk.type = "button"; sk.className = "btn btn-sm btn-ghost active"; sk.textContent = "跳过";
    var ov = document.createElement("button"); ov.type = "button"; ov.className = "btn btn-sm"; ov.textContent = "覆盖";
    ov.title = "用这个文件替换《" + ((job.res.matches || [])[0] || {}).title + "》的音频，保留原名称";
    sk.addEventListener("click", function () { decide(job, "skip"); });
    ov.addEventListener("click", function () {
      if (confirm("确定用「" + job.file.name + "」覆盖已有曲目的音频？原曲目名称会保留。")) decide(job, "overwrite");
    });
    w.appendChild(info); w.appendChild(sk); w.appendChild(ov);
    job.r.li.appendChild(w);
    job.wrap = w; job.skipBtn = sk; job.overBtn = ov;
  }
  function refreshDupBar() {
    var pending = dupJobs.filter(function (j) { return j.state === "pending"; });
    if (!pending.length) { if (dupBar) { dupBar.remove(); dupBar = null; } return; }
    if (!dupBar) {
      dupBar = document.createElement("div"); dupBar.className = "upload-dup-bar";
      var t = document.createElement("span"); t.className = "ub-dup-count";
      var a = document.createElement("button"); a.type = "button"; a.className = "btn btn-sm btn-ghost"; a.textContent = "重复的全部跳过";
      var b = document.createElement("button"); b.type = "button"; b.className = "btn btn-sm"; b.textContent = "重复的全部覆盖";
      a.addEventListener("click", function () { dupJobs.forEach(function (j) { decide(j, "skip"); }); });
      b.addEventListener("click", function () {
        var p = dupJobs.filter(function (j) { return j.state === "pending"; });
        if (!p.length || !confirm("确定用新文件覆盖这 " + p.length + " 首已有曲目的音频？原曲目名称会保留。")) return;
        (function go(i) { if (i < p.length) decide(p[i], "overwrite").then(function () { go(i + 1); }); })(0);
      });
      dupBar.appendChild(t); dupBar.appendChild(a); dupBar.appendChild(b);
      list.parentNode.insertBefore(dupBar, list);
    }
    dupBar.querySelector(".ub-dup-count").textContent = "发现 " + pending.length + " 个重复文件，默认跳过（不新增曲目）；可逐个或批量选择：";
  }

  function sendOne(file, title, r, extra) {
    return new Promise(function (resolve) {
      var fd = new FormData();
      fd.append("csrf_token", csrf);
      if (title) fd.append("title", title);
      var catEl = document.getElementById("musicUploadCategory");
      if (catEl && catEl.value) fd.append("category", catEl.value);
      if (extra) Object.keys(extra).forEach(function (k) { if (extra[k] != null) fd.append(k, extra[k]); });
      fd.append("file", file, file.name);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/upload");
      xhr.setRequestHeader("X-Requested-With", "dj666-upload");
      xhr.upload.onprogress = function (ev) {
        if (ev.lengthComputable) setState(r, "up", "上传中 " + Math.round(ev.loaded / ev.total * 100) + "%");
      };
      xhr.onload = function () {
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) {}
        var res = data && data.results && data.results[0];
        if (xhr.status === 200 && res && res.ok) {
          setState(r, "ok", res.overwritten ? "✓ 已覆盖《" + res.title + "》" : "✓ 已上传"); resolve(true); return;
        }
        if (xhr.status === 200 && res && res.duplicate) {
          setState(r, "dup", "⚠ 重复，默认跳过");
          if (!extra) {
            var job = { file: file, r: r, res: res, state: "pending", target: (res.matches[0] || {}).id };
            dupJobs.push(job); addDupChoice(job); refreshDupBar();
          }
          resolve("dup"); return;
        }
        var msg = (res && res.error) || (data && (data.error || data.detail)) ||
          (xhr.status === 413 ? "文件过大" : xhr.status === 400 ? "请求无效，请刷新页面后重试" : "上传失败（" + xhr.status + "）");
        setState(r, "err", "✗ " + msg); resolve(false);
      };
      xhr.onerror = function () { setState(r, "err", "✗ 网络错误"); resolve(false); };
      setState(r, "up", "上传中 0%");
      xhr.send(fd);
    });
  }

  form.addEventListener("submit", function (e) {
    var files = input.files ? Array.prototype.slice.call(input.files) : [];
    if (!files.length) return; // let the browser show "required"
    e.preventDefault();
    if (busy) return;
    busy = true; btn.disabled = true; input.disabled = true; reload.hidden = true;
    list.innerHTML = ""; box.hidden = false;
    dupJobs = []; if (dupBar) { dupBar.remove(); dupBar = null; }
    var title = files.length === 1 ? titleEl.value.trim() : "";
    var jobs = files.map(function (f, i) {
      var r = row(f.name);
      if (i >= MAX) { setState(r, "err", "✗ 单次最多 " + MAX + " 个文件"); return null; }
      if (!OK_EXT.test(f.name)) { setState(r, "err", "✗ 格式不支持（仅 mp3 / wav / ogg / m4a / flac）"); return null; }
      if (f.size > MAX_BYTES) { setState(r, "err", "✗ 超过 50MB"); return null; }
      if (f.size === 0) { setState(r, "err", "✗ 文件是空的"); return null; }
      return { file: f, r: r };
    });
    var ok = 0, dup = 0, bad = jobs.filter(function (j) { return !j; }).length, done = 0;
    var todo = jobs.filter(Boolean);
    function upd(final) {
      summary.textContent = (final ? "上传完成：" : "正在上传 " + done + "/" + todo.length + " · ") +
        "成功 " + ok + " 个" + (dup ? "，重复 " + dup + " 个" : "") + (bad ? "，失败 " + bad + " 个" : "");
      summary.className = "upload-batch-summary" + (final ? (bad ? (ok ? " partial" : " fail") : " done") : "");
    }
    upd(false);
    (function next(i) {
      if (i >= todo.length) {
        upd(true);
        busy = false; btn.disabled = false; input.disabled = false;
        if (ok) { reload.hidden = false; form.reset(); syncTitle(); }
        return;
      }
      sendOne(todo[i].file, title, todo[i].r).then(function (good) {
        done++; if (good === "dup") dup++; else if (good) ok++; else bad++;
        upd(false); next(i + 1);
      });
    })(0);
  });
})();

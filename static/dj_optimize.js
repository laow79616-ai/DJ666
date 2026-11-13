(function () {
  var root = document.getElementById("djopt");
  if (!root) return;
  var CSRF = root.getAttribute("data-csrf") || "";
  var LOGGED = root.getAttribute("data-logged-in") === "1";
  var jobsEl = document.getElementById("djoptJobs");
  var cards = {};
  var pollTimer = null;
  var alive = true;
  var LS_KEY = "djopt-expanded";
  function loadExpanded() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function saveExpanded(map) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(map)); } catch (e) {}
  }
  var expandedMap = loadExpanded();
  function isImported(j) {
    return !!(j && j.state === "done" && j.track_id);
  }
  function canCollapse(j) {
    return !!(j && j.state === "done");
  }
  function applyCollapse(c, j) {
    var btn = c.el.querySelector(".djopt-toggle");
    var tag = c.el.querySelector(".djopt-imported-tag");
    var imported = isImported(j);
    var allow = canCollapse(j);
    c.el.classList.toggle("is-imported", imported);
    tag.hidden = !imported;
    if (!allow) {
      c.el.classList.remove("is-collapsed");
      btn.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      return;
    }
    btn.hidden = false;
    var stored = expandedMap[c.id];
    var open = stored === 1 || (stored == null && !imported);
    c.el.classList.toggle("is-collapsed", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.textContent = open ? "收起" : "展开";
    btn.setAttribute("title", open ? "收起详情" : "展开详情");
    if (!open) {
      var player = c.el.querySelector(".djopt-player");
      if (player && !player.paused) { try { player.pause(); } catch (e) {} }
    }
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function mb(n) { return n ? (n / 1048576).toFixed(n > 104857600 ? 0 : 1) + "MB" : "-"; }
  function dur(t) {
    if (!t || !isFinite(t)) return "-";
    t = Math.round(t);
    var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return (h ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }
  function num(v, unit) { return v == null ? "-" : (Math.round(v * 10) / 10).toFixed(1) + (unit || ""); }

  function api(method, url, body, isRaw) {
    var init = { method: method, credentials: "same-origin", headers: { "X-CSRF-Token": CSRF } };
    if (body != null) {
      if (isRaw) { init.body = body; init.headers["Content-Type"] = "application/octet-stream"; }
      else { init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json"; }
    }
    return fetch(url, init).then(function (res) {
      return res.text().then(function (txt) {
        var data = {};
        try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = { detail: txt.slice(0, 200) }; }
        if (!res.ok) {
          var err = new Error((data && data.detail) || ("请求失败 " + res.status));
          err.status = res.status; err.data = data;
          throw err;
        }
        return data;
      });
    });
  }

  function card(id) {
    if (cards[id]) return cards[id];
    var el = document.createElement("article");
    el.className = "djopt-job";
    el.setAttribute("data-job", id);
    el.innerHTML =
      '<div class="djopt-job-head"><b class="djopt-job-name"></b><span class="djopt-badge"></span>' +
      '<span class="djopt-imported-tag" hidden>已导入</span>' +
      '<button type="button" class="btn btn-sm btn-ghost djopt-toggle" hidden aria-expanded="false">展开</button>' +
      '<button type="button" class="btn btn-sm btn-ghost djopt-del" title="删除任务和文件">删除</button></div>' +
      '<div class="djopt-bar"><i></i></div><p class="djopt-stage muted"></p>' +
      '<div class="djopt-result" hidden></div>';
    jobsEl.insertBefore(el, jobsEl.firstChild);
    var c = { id: id, el: el, data: null, rendered: "" };
    el.querySelector(".djopt-del").addEventListener("click", function () {
      if (!confirm("删除这个任务及其文件？（已导入曲库的曲目不受影响）")) return;
      api("POST", "/dj/optimize/" + id + "/delete", {}).then(function () {
        el.remove(); delete cards[id];
        if (expandedMap[id]) { delete expandedMap[id]; saveExpanded(expandedMap); }
      }).catch(function (e) { alert(e.message); });
    });
    el.querySelector(".djopt-toggle").addEventListener("click", function () {
      var willOpen = c.el.classList.contains("is-collapsed");
      expandedMap[id] = willOpen ? 1 : 0;
      saveExpanded(expandedMap);
      applyCollapse(c, c.data);
    });
    cards[id] = c;
    return c;
  }

  function setBar(c, pct, text, cls) {
    c.el.querySelector(".djopt-bar i").style.width = Math.max(0, Math.min(100, pct)) + "%";
    c.el.querySelector(".djopt-stage").textContent = text || "";
    c.el.classList.remove("is-failed", "is-done", "is-busy");
    if (cls) c.el.classList.add(cls);
  }

  function renderResult(c, j) {
    var box = c.el.querySelector(".djopt-result");
    var key = j.state + "|" + (j.track_id || "") + "|" + (j.outputs ? 1 : 0);
    if (c.rendered === key) return;
    c.rendered = key;
    if (j.state !== "done") { box.hidden = true; box.innerHTML = ""; return; }
    var b = j.before || {}, a = j.after || {};
    var stem = (j.name || "audio").replace(/\.[^.]+$/, "");
    var imported = j.track_id
      ? '<p class="djopt-ok">✅ 已导入曲库（' + (j.track_status === "approved" ? "已发布" : "待管理员审核") + "，格式 " + esc((j.imported_format || "").toUpperCase()) + "）</p>"
      : '<div class="djopt-import"><input type="text" maxlength="200" class="djopt-title" value="' + esc(stem + "（优化版）") + '" aria-label="曲名">' +
        '<select class="djopt-fmt" aria-label="导入格式"><option value="mp3">MP3 320k</option><option value="flac">FLAC 无损</option></select>' +
        '<button type="button" class="btn btn-sm djopt-imp">导入曲库</button></div>';
    box.innerHTML =
      '<table class="djopt-stats"><thead><tr><th></th><th>整体响度</th><th>真峰值</th><th>动态范围</th></tr></thead><tbody>' +
      "<tr><td>原版</td><td>" + num(b.i, " LUFS") + "</td><td>" + num(b.tp, " dBTP") + "</td><td>" + num(b.lra, " LU") + "</td></tr>" +
      '<tr class="is-new"><td>优化版</td><td>' + num(a.i, " LUFS") + "</td><td>" + num(a.tp, " dBTP") + "</td><td>" + num(a.lra, " LU") + "</td></tr>" +
      "</tbody></table>" +
      '<p class="muted djopt-meta">时长 ' + dur(j.duration) + (j.trim_end ? "（录音自动停止：只保留前 " + dur(j.trim_end) + "，已裁掉重复/静音的尾巴）" : "") + " · 增益 " + (j.gain_db > 0 ? "+" : "") + num(j.gain_db, " dB") +
      " · 目标 " + esc(j.target) + " LUFS · FLAC " + mb(j.outputs && j.outputs.flac) + " · MP3 " + mb(j.outputs && j.outputs.mp3) +
      (a.mp3_tp != null ? " · MP3 峰值 " + num(a.mp3_tp, " dBTP") : "") + "</p>" +
      '<div class="djopt-ab"><div class="djopt-ab-btns" role="group" aria-label="A/B 对比">' +
      '<button type="button" class="btn btn-sm btn-ghost" data-ab="orig">A 原版</button>' +
      '<button type="button" class="btn btn-sm is-on" data-ab="opt">B 优化版</button></div>' +
      '<audio controls preload="none" class="djopt-player"></audio>' +
      '<p class="muted djopt-ab-tip">切换 A/B 会停在同一位置继续播放，方便对比。原版和优化版音量不同属正常。</p></div>' +
      '<div class="djopt-dl">' +
      '<a class="btn btn-sm" href="' + j.flac_url + '?download=1" download>下载 FLAC 无损</a>' +
      '<a class="btn btn-sm" href="' + j.mp3_url + '?download=1" download>下载 MP3 320k</a>' +
      '<a class="btn btn-sm btn-ghost" href="' + j.orig_url + '?download=1" download>下载原文件</a></div>' + imported +
      '<p class="djopt-msg muted" role="status"></p>';
    box.hidden = false;
    var player = box.querySelector(".djopt-player");
    var mode = "opt";
    player.src = j.mp3_url;
    Array.prototype.forEach.call(box.querySelectorAll("[data-ab]"), function (btn) {
      btn.addEventListener("click", function () {
        var want = btn.getAttribute("data-ab");
        if (want === mode) return;
        var t = player.currentTime || 0, playing = !player.paused;
        mode = want;
        player.src = want === "opt" ? j.mp3_url : j.orig_url;
        var seekBack = function () {
          try { player.currentTime = t; } catch (e) {}
          if (playing) player.play().catch(function () {});
          player.removeEventListener("loadedmetadata", seekBack);
        };
        player.addEventListener("loadedmetadata", seekBack);
        player.load();
        Array.prototype.forEach.call(box.querySelectorAll("[data-ab]"), function (b2) {
          var on = b2.getAttribute("data-ab") === want;
          b2.classList.toggle("is-on", on);
          b2.classList.toggle("btn-ghost", !on);
        });
      });
    });
    var imp = box.querySelector(".djopt-imp");
    if (imp) {
      imp.addEventListener("click", function () {
        imp.disabled = true;
        var msg = box.querySelector(".djopt-msg");
        msg.textContent = "正在导入…";
        api("POST", "/dj/optimize/" + j.id + "/import", {
          title: box.querySelector(".djopt-title").value,
          format: box.querySelector(".djopt-fmt").value
        }).then(function (r) {
          c.el.remove();
          delete cards[j.id];
          if (expandedMap[j.id]) { delete expandedMap[j.id]; saveExpanded(expandedMap); }
        }).catch(function (e) { imp.disabled = false; msg.textContent = e.message; });
      });
    }
  }

  function render(j) {
    var c = card(j.id);
    c.data = j;
    c.el.querySelector(".djopt-job-name").textContent = j.name || "音频";
    c.el.querySelector(".djopt-badge").textContent = j.source === "record" ? "录音" : j.source === "telegram" ? "Telegram" : "上传";
    var st = j.state;
    if (st === "uploading") {
      var p = j.size ? Math.round((j.received || 0) / j.size * 100) : 0;
      setBar(c, p, c.localUpload ? "上传中 " + p + "%（" + mb(j.received) + " / " + mb(j.size) + "）" : "上传未完成（页面刷新后需重新上传）", "is-busy");
    } else if (st === "downloading") {
      var dp = j.size ? Math.round((j.received || 0) / j.size * 100) : 0;
      setBar(c, dp, (j.stage || "从 Telegram 下载中") + " " + dp + "%（" + mb(j.received) + " / " + mb(j.size) + "）", "is-busy");
    } else if (st === "uploaded") {
      setBar(c, 100, "上传完成，等待开始…", "is-busy");
    } else if (st === "queued") {
      setBar(c, 0, "排队中" + (j.queue_pos ? "（第 " + j.queue_pos + " 位）" : "") + "…", "is-busy");
    } else if (st === "running") {
      setBar(c, j.progress || 0, (j.stage || "处理中") + " · " + (j.progress || 0) + "%", "is-busy");
    } else if (st === "done") {
      var doneMsg = "✅ 优化完成";
      if (j.track_id) {
        doneMsg += " · 已导入曲库（" + (j.track_status === "approved" ? "已发布" : "待管理员审核") +
          (j.imported_format ? "，" + String(j.imported_format).toUpperCase() : "") + "）";
      }
      setBar(c, 100, doneMsg, "is-done");
    } else if (st === "failed") {
      setBar(c, 100, "❌ " + (j.error || "处理失败"), "is-failed");
    }
    renderResult(c, j);
    applyCollapse(c, j);
  }

  function anyBusy() {
    return Object.keys(cards).some(function (k) {
      var d = cards[k].data;
      return d && (d.state === "queued" || d.state === "running" || d.state === "uploaded" || d.state === "downloading");
    });
  }
  function schedule() {
    clearTimeout(pollTimer);
    if (!alive || !anyBusy()) return;
    pollTimer = setTimeout(function () {
      var ids = Object.keys(cards).filter(function (k) {
        var d = cards[k].data;
        return d && (d.state === "queued" || d.state === "running" || d.state === "uploaded" || d.state === "downloading");
      });
      Promise.all(ids.map(function (id) { return refresh(id, true); })).then(schedule, schedule);
    }, 2000);
  }
  function refresh(id, noSchedule) {
    return api("GET", "/dj/optimize/" + id).then(function (j) {
      render(j);
      if (!noSchedule) schedule();
    }).catch(function (e) {
      if (e.status === 404 && cards[id]) { cards[id].el.remove(); delete cards[id]; }
    });
  }

  function uploadBlob(blob, name, source, target, onState, extra) {
    if (!LOGGED) return Promise.reject(new Error("请先登录后再上传优化"));
    var c = null;
    onState = onState || function () {};
    var body = { name: name, size: blob.size, source: source || "upload", target: target || "-9" };
    if (extra && extra.trim_end) body.trim_end = extra.trim_end;
    return api("POST", "/dj/optimize/init", body).then(function (r) {
      var id = r.id, chunk = r.chunk || 8388608, offset = 0;
      c = card(id);
      c.localUpload = true;
      render({ id: id, name: name, source: source, state: "uploading", size: blob.size, received: 0 });
      onState("uploading", id);
      function next(tries) {
        if (offset >= blob.size) return Promise.resolve();
        var part = blob.slice(offset, Math.min(blob.size, offset + chunk));
        return api("POST", "/dj/optimize/" + id + "/chunk?offset=" + offset, part, true).then(function (res) {
          offset = res.received;
          render({ id: id, name: name, source: source, state: "uploading", size: blob.size, received: offset });
          return next(0);
        }).catch(function (e) {
          if (e.status === 409 && e.data && typeof e.data.received === "number") { offset = e.data.received; return next(tries); }
          if ((tries || 0) < 3 && (!e.status || e.status >= 500)) {
            return new Promise(function (res) { setTimeout(res, 1500 * (tries + 1)); }).then(function () { return next((tries || 0) + 1); });
          }
          throw e;
        });
      }
      return next(0).then(function () {
        return api("POST", "/dj/optimize/" + id + "/start", {});
      }).then(function (j) {
        render(j);
        onState("queued", id);
        schedule();
        return id;
      });
    }).catch(function (e) {
      if (c) setBar(c, 100, "❌ " + e.message, "is-failed");
      throw e;
    });
  }
  window.DJOpt = { uploadBlob: uploadBlob, loggedIn: LOGGED, watch: function (id) { return refresh(id); } };

  if (!LOGGED) return;

  var fileEl = document.getElementById("djoptFile");
  var fnameEl = document.getElementById("djoptFname");
  var goBtn = document.getElementById("djoptGo");
  var targetEl = document.getElementById("djoptTarget");
  fileEl.addEventListener("change", function () {
    var f = fileEl.files && fileEl.files[0];
    fnameEl.textContent = f ? f.name + "（" + mb(f.size) + "）" : "未选择文件";
    goBtn.disabled = !f;
  });
  goBtn.addEventListener("click", function () {
    var f = fileEl.files && fileEl.files[0];
    if (!f) return;
    var maxMB = parseInt(root.getAttribute("data-max-mb") || "1024", 10) || 1024;
    if (f.size > maxMB * 1048576) { alert("文件不能超过 " + maxMB + "MB"); return; }
    goBtn.disabled = true;
    uploadBlob(f, f.name, "upload", targetEl.value).then(function () {
      fileEl.value = ""; fnameEl.textContent = "未选择文件";
    }).catch(function (e) { alert(e.message); goBtn.disabled = false; });
  });

  api("GET", "/dj/optimize/jobs").then(function (r) {
    (r.jobs || []).slice().reverse().forEach(function (j) { if (!j.track_id) render(j); });
    schedule();
  }).catch(function () {});

  window.DJPageLeave = window.DJPageLeave || [];
  window.DJPageLeave.push(function () { alive = false; clearTimeout(pollTimer); });
})();

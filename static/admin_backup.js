/* Admin backup records. Secrets are never requested or shown. */
(function () {
  var root = document.getElementById("bakPanel");
  if (!root) return;
  var CSRF = root.getAttribute("data-csrf") || "";
  var pollTimer = null;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function say(text, kind) {
    var el = $("bakMsg");
    el.textContent = text || "";
    el.hidden = !text;
    el.className = "bak-msg" + (kind ? " is-" + kind : "");
  }
  function clsFor(result) {
    return result === "ok" ? "ok" : result === "fail" ? "fail" : result === "skip" || result === "running" ? "skip" : "";
  }
  function resultLabel(r) {
    return r === "ok" ? "成功" : r === "fail" ? "失败" : r === "skip" ? "跳过" : r === "running" ? "进行中" : (r || "—");
  }

  function fmtMYT(ms) {
    var parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Kuala_Lumpur", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
    }).formatToParts(new Date(ms));
    var g = {};
    parts.forEach(function (p) { g[p.type] = p.value; });
    return g.year + "-" + g.month + "-" + g.day + " " + g.hour + ":" + g.minute + ":" + g.second + " MYT";
  }
  function nextSlot() {
    var slot = 10 * 60 * 1000;
    return fmtMYT(Math.ceil((Date.now() + 1000) / slot) * slot);
  }
  function paint(d) {
    var last = d.last || {};
    var lastEl = $("bakLast");
    if (d.running) {
      lastEl.textContent = "正在备份…";
      lastEl.className = "val is-run";
    } else {
      lastEl.textContent = (last.end_myt || last.start_myt || "尚未备份") +
        (last.result ? " · " + resultLabel(last.result) : "");
      lastEl.className = "val is-" + (last.result || "skip");
    }
    $("bakNext").textContent = d.next_myt || nextSlot();
    $("bakDisk").textContent = d.disk_free_human || "—";
    if (d.path) $("bakPath").textContent = d.path;
    var rows = $("bakRows");
    var items = d.recent || [];
    if (!items.length) {
      rows.innerHTML = '<tr><td colspan="5" class="muted">还没有备份记录。</td></tr>';
    } else {
      rows.innerHTML = items.map(function (it) {
        var err = it.error ? esc(it.error) : "";
        var size = it.size_human || "—";
        var note = err || it.note || (it.files != null ? it.files + " 个文件" : "");
        if (it.path && !err) note = (note ? note + " · " : "") + esc(it.path);
        return "<tr><td>" + esc(it.time_myt || "—") + "</td><td class=\"" + clsFor(it.result) + "\">" +
          esc(resultLabel(it.result)) + "</td><td>" + esc(size) + "</td><td>" +
          esc(it.seconds != null ? it.seconds + " 秒" : "—") + "</td><td class=\"muted\">" + note + "</td></tr>";
      }).join("");
    }
    $("bakRun").disabled = !!d.running;
    $("bakRunHint").textContent = d.running ? "备份正在进行，请稍候。" : "备份在后台独立运行，不会中断网站。";
    clearTimeout(pollTimer);
    if (d.running) pollTimer = setTimeout(load, 3000);
  }

  function load() {
    return fetch("/admin/backups/status", { credentials: "same-origin" }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "读取备份状态失败");
        paint(d);
        return d;
      });
    }).catch(function (e) { say(e.message, "err"); });
  }

  $("bakRun").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true;
    say("正在启动备份…");
    fetch("/admin/backups/run", {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRF-Token": CSRF, "Content-Type": "application/json" },
      body: "{}"
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "无法启动备份");
        say(d.message || "备份已开始", "ok");
        if (d && (d.running || d.last)) paint(d);
        else load();
        pollTimer = setTimeout(load, 2000);
      });
    }).catch(function (e) { say(e.message, "err"); btn.disabled = false; });
  });

  load();
})();

/* Telegram 音乐导入 (admin only). Secrets typed here are sent once and never shown again. */
(function () {
  var root = document.getElementById("tgaPanel");
  if (!root) return;
  var CSRF = root.getAttribute("data-csrf") || "";
  function $(s) { return root.querySelector(s); }
  var msgEl = $("#tgaMsg"), badge = $("#tgaBadge");
  var chatRef = "", chatKey = "", nextOffset = null, pollTimer = null, lastStatus = null, recent = [];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function mb(n) { return n ? (n / 1048576).toFixed(1) + "MB" : "-"; }
  function dur(t) {
    t = Math.round(t || 0); if (!t) return "-";
    var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return (h ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }
  function say(text, kind) {
    msgEl.textContent = text || "";
    msgEl.hidden = !text;
    msgEl.className = "tga-msg" + (kind ? " is-" + kind : "");
  }
  function api(method, url, body) {
    var init = { method: method, credentials: "same-origin", headers: { "X-CSRF-Token": CSRF } };
    if (body) { init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json"; }
    return fetch(url, init).then(function (r) {
      return r.text().then(function (t) {
        var d = {}; try { d = t ? JSON.parse(t) : {}; } catch (e) { d = { detail: "服务器返回异常（" + r.status + "）" }; }
        if (!r.ok) { var er = new Error(d.detail || ("请求失败 " + r.status)); er.status = r.status; throw er; }
        return d;
      });
    });
  }
  function busy(btn, on, label) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || "处理中…"; btn.disabled = true; }
    else { btn.textContent = btn.dataset.label || btn.textContent; btn.disabled = false; }
  }
  function act(btn, method, url, body, label) {
    busy(btn, true, label);
    return api(method, url, body).then(function (d) {
      busy(btn, false);
      if (d.message) say(d.message, "ok");
      if (d.configured !== undefined) apply(d);
      return d;
    }).catch(function (e) { busy(btn, false); say(e.message, "err"); throw e; });
  }
  function step(name) {
    Array.prototype.forEach.call(root.querySelectorAll(".tga-step"), function (f) { f.hidden = f.dataset.step !== name; });
  }

  function renderRecent(list) {
    if (list) recent = list;
    var box = $("#tgaRecent"), wrap = $("#tgaChips");
    box.hidden = !recent.length;
    wrap.innerHTML = "";
    recent.forEach(function (r) {
      var chip = document.createElement("div");
      chip.className = "tga-chip" + (r.pinned ? " is-pinned" : "") + (r.key === chatKey ? " is-active" : "");
      var open = document.createElement("button");
      open.type = "button"; open.className = "tga-chip-open";
      open.title = "打开 " + r.title;
      open.innerHTML = "<b>" + esc(r.title) + "</b><span>" + (r.username ? "@" + esc(r.username) : esc(r.type || "频道")) +
        (r.count != null ? " · " + esc(r.count) + " 个音频" : "") + (r.opened ? " · " + esc(r.opened) : "") + "</span>";
      open.addEventListener("click", function () {
        $("#tgaResults").hidden = true;
        $("#tgaChat").value = r.username ? "https://t.me/" + r.username : r.key;
        openChat(r.open, false, open);
      });
      var pin = document.createElement("button");
      pin.type = "button"; pin.className = "tga-chip-act"; pin.textContent = r.pinned ? "★" : "☆";
      pin.title = r.pinned ? "取消置顶" : "置顶";
      pin.setAttribute("aria-label", pin.title);
      pin.addEventListener("click", function () { editRecent(r.key, r.pinned ? "unpin" : "pin"); });
      var del = document.createElement("button");
      del.type = "button"; del.className = "tga-chip-act tga-chip-del"; del.textContent = "×";
      del.title = "从最近列表移除"; del.setAttribute("aria-label", del.title);
      del.addEventListener("click", function () { editRecent(r.key, "remove"); });
      chip.appendChild(open); chip.appendChild(pin); chip.appendChild(del);
      wrap.appendChild(chip);
    });
  }
  function editRecent(key, action) {
    api("POST", "/admin/tg-audio/recent", { key: key, action: action }).then(function (d) { renderRecent(d.recent); })
      .catch(function (e) { say(e.message, "err"); });
  }

  function apply(st) {
    lastStatus = st;
    if (st.recent) renderRecent(st.recent);
    $("#tgaCfgState").textContent = st.configured ? "api_id / api_hash：已配置 ✓" : "未配置 api_id / api_hash";
    $("#tgaCfgState").classList.toggle("is-ok", !!st.configured);
    if (!st.configured) { $("#tgaCfgBox").open = true; }
    $("#tgaLogin").hidden = !st.configured || st.logged_in;
    $("#tgaMain").hidden = !st.logged_in;
    badge.className = "tga-badge" + (st.logged_in ? " is-ok" : st.configured ? " is-warn" : "");
    badge.textContent = !st.configured ? "未配置 api_id / api_hash" : st.logged_in ? "已登录" : "未登录";
    if (st.configured && !st.logged_in) step(st.login_stage || "phone");
    if (st.logged_in && st.me) {
      $("#tgaMe").textContent = "当前账号：" + st.me.name + (st.me.username ? "（@" + st.me.username + "）" : "");
    }
    $("#tgaQuota").textContent = "今日已下载 " + (st.today || 0) + " / " + (st.daily_cap || 30) + (st.flood_wait ? " · Telegram 限流中，还需等待 " + st.flood_wait + " 秒" : "");
    if (st.error) say(st.error, "err");
    var dl = st.download, box = $("#tgaDl");
    if (dl && dl.job_id && (dl.active || dl.state === "failed")) {
      box.hidden = false;
      var p = dl.size ? Math.round((dl.received || 0) / dl.size * 100) : 0;
      box.querySelector(".djopt-bar i").style.width = p + "%";
      $("#tgaDlText").textContent = dl.state === "failed" ? "❌ " + dl.name + "：" + (dl.error || "下载失败")
        : "⬇️ " + dl.name + " · " + (dl.state === "downloading" ? "下载中" : dl.state) + " " + p + "%（" + mb(dl.received) + " / " + mb(dl.size) + "）";
    } else if (dl && dl.state === "done") {
      box.hidden = false; box.querySelector(".djopt-bar i").style.width = "100%";
      $("#tgaDlText").textContent = "✅ " + dl.name + " 已下载，正在下方「上传优化」中处理";
    } else box.hidden = true;
    paintBatch(st);
    var batchBusy = batchIsBusy(st.batch);
    clearTimeout(pollTimer);
    if ((dl && dl.active) || batchBusy) pollTimer = setTimeout(load, 2000);
  }
  function batchIsBusy(b) {
    if (!b) return false;
    if (b.active) return true;
    return (b.items || []).some(function (it) {
      return it.status === "queued" || it.status === "downloading" || it.status === "optimizing";
    });
  }
  function statusText(it) {
    if (!it || !it.status) return "";
    if (it.status === "queued") return "排队";
    if (it.status === "downloading") {
      var p = it.progress;
      if (p == null && it.size) p = Math.round((it.received || 0) / it.size * 100);
      return "下载中 " + (p || 0) + "%";
    }
    if (it.status === "optimizing") return "优化中";
    if (it.status === "done") return "完成";
    if (it.status === "failed") return "失败" + (it.error ? "：" + it.error : "");
    if (it.status === "cancelled") return "已取消";
    if (it.status === "skipped") return "已跳过";
    return "";
  }
  function statusClass(st) {
    return "tga-st" + (st === "downloading" ? " is-dl" : st === "optimizing" ? " is-opt" : st === "done" ? " is-done" : st === "failed" ? " is-fail" : st === "cancelled" || st === "skipped" ? " is-cancel" : st === "queued" ? " is-dl" : "");
  }
  function batchItemFor(msgId) {
    var b = (lastStatus && lastStatus.batch) || {};
    var items = b.items || [];
    var found = null;
    for (var i = 0; i < items.length; i++) {
      if (Number(items[i].msg_id) === Number(msgId)) found = items[i];
    }
    return found;
  }
  function paintRowStatus(tr) {
    var cell = tr.querySelector(".tga-st");
    if (!cell) return;
    var it = batchItemFor(tr.getAttribute("data-msg-id"));
    if (!it) { if (!cell.dataset.keep) cell.textContent = ""; cell.className = "tga-st"; tr.classList.remove("is-batch"); return; }
    cell.textContent = statusText(it);
    cell.className = statusClass(it.status);
    tr.classList.toggle("is-batch", it.status === "queued" || it.status === "downloading" || it.status === "optimizing");
  }
  function selectedRows() {
    var out = [];
    Array.prototype.forEach.call(root.querySelectorAll("#tgaRows tr[data-msg-id]"), function (tr) {
      var cb = tr.querySelector("input.tga-row-cb");
      if (cb && cb.checked && !cb.disabled) {
        out.push({
          msg_id: Number(tr.getAttribute("data-msg-id")),
          force: tr.getAttribute("data-done") === "1",
          title: tr.getAttribute("data-title") || ""
        });
      }
    });
    return out;
  }
  function syncSelUi() {
    var n = selectedRows().length;
    var go = $("#tgaBatchGo");
    if (go) { go.textContent = "下载并优化（" + n + "）"; go.disabled = n === 0; }
    var boxes = root.querySelectorAll("#tgaRows input.tga-row-cb:not(:disabled)");
    var all = $("#tgaSelAll");
    if (all && boxes.length) {
      var pick = 0, auto = 0;
      Array.prototype.forEach.call(boxes, function (cb) {
        var done = cb.closest("tr") && cb.closest("tr").getAttribute("data-done") === "1";
        if (!done) auto++;
        if (cb.checked) pick++;
      });
      all.checked = auto > 0 && Array.prototype.every.call(boxes, function (cb) {
        var done = cb.closest("tr") && cb.closest("tr").getAttribute("data-done") === "1";
        return done || cb.checked;
      });
      all.indeterminate = pick > 0 && !all.checked;
    } else if (all) { all.checked = false; all.indeterminate = false; }
  }
  function paintBatch(st) {
    var b = (st && st.batch) || {};
    var items = b.items || [];
    var queued = items.filter(function (it) { return it.status === "queued"; }).length;
    var busy = batchIsBusy(b);
    var cancel = $("#tgaBatchCancel");
    if (cancel) cancel.hidden = queued === 0;
    var info = $("#tgaBatchInfo");
    if (info) {
      var running = items.filter(function (it) { return it.status === "downloading" || it.status === "optimizing"; }).length;
      var done = items.filter(function (it) { return it.status === "done"; }).length;
      var fail = items.filter(function (it) { return it.status === "failed"; }).length;
      var bits = [];
      if (b.message) bits.push(b.message);
      if (busy || items.length) {
        bits.push("队列 " + done + " 完成 / " + fail + " 失败 / " + queued + " 排队" + (running ? " / " + running + " 进行中" : ""));
      }
      info.textContent = bits.join(" ");
    }
    Array.prototype.forEach.call(root.querySelectorAll("#tgaRows tr[data-msg-id]"), paintRowStatus);
    if (window.DJOpt && window.DJOpt.watch) {
      items.forEach(function (it) { if (it.job_id) window.DJOpt.watch(it.job_id); });
    }
  }
  function load(force) {
    return api("GET", "/admin/tg-audio/status" + (force ? "?refresh=1" : "")).then(apply).catch(function (e) {
      badge.textContent = "状态读取失败"; say(e.message, "err");
    });
  }

  // config
  $("[data-tga-cfg-save]").addEventListener("click", function () {
    var f = $("#tgaCfgForm");
    var body = { api_id: f.api_id.value.trim(), api_hash: f.api_hash.value.trim(), daily_cap: f.daily_cap.value.trim() };
    act(this, "POST", "/admin/tg-audio/config", body, "保存中…").then(function () {
      f.api_id.value = ""; f.api_hash.value = "";
    }).catch(function () {});
  });
  $("[data-tga-cfg-clear]").addEventListener("click", function () {
    if (!confirm("清除 api_id / api_hash，并退出 Telegram 登录、删除会话？")) return;
    act(this, "POST", "/admin/tg-audio/config", { action: "clear" }, "清除中…").catch(function () {});
  });
  // login
  $("[data-tga-send]").addEventListener("click", function () {
    var inp = root.querySelector("[data-step=phone] input[name=phone]");
    act(this, "POST", "/admin/tg-audio/login/send-code", { phone: inp.value.trim() }, "发送中…").then(function () {
      step("code"); var c = root.querySelector("[data-step=code] input"); c.value = ""; c.focus();
    }).catch(function () {});
  });
  $("[data-tga-code]").addEventListener("click", function () {
    var inp = root.querySelector("[data-step=code] input[name=code]");
    act(this, "POST", "/admin/tg-audio/login/code", { code: inp.value.trim() }, "登录中…").then(function (d) {
      inp.value = "";
      root.querySelector("[data-step=phone] input").value = "";
      if (d.stage === "password") { step("password"); root.querySelector("[data-step=password] input").focus(); }
    }).catch(function () {});
  });
  $("[data-tga-restart]").addEventListener("click", function () { step("phone"); });
  $("[data-tga-pw]").addEventListener("click", function () {
    var inp = root.querySelector("[data-step=password] input[name=password]");
    var v = inp.value; inp.value = "";
    act(this, "POST", "/admin/tg-audio/login/password", { password: v }, "验证中…").catch(function () {});
  });
  $("[data-tga-logout]").addEventListener("click", function () {
    if (!confirm("退出 Telegram 登录并删除服务器上的会话？")) return;
    act(this, "POST", "/admin/tg-audio/logout", {}, "退出中…").then(function () {
      $("#tgaList").hidden = true; $("#tgaResults").hidden = true;
    }).catch(function () {});
  });

  // browse
  function openChat(ref, more, btn) {
    if (!more) { chatRef = ref; nextOffset = null; }
    busy(btn, true, "读取中…");
    say(more ? "" : "正在读取频道音频…（请求之间至少间隔 5 秒）");
    return api("POST", "/admin/tg-audio/list", { chat: chatRef, offset_id: more ? nextOffset : 0 }).then(function (d) {
      busy(btn, false); say("");
      chatKey = d.chat.key;
      if (d.recent) renderRecent(d.recent); else renderRecent();
      $("#tgaList").hidden = false;
      $("#tgaChatTitle").textContent = d.chat.title + (d.chat.username ? "（@" + d.chat.username + "）" : "") + (d.total != null ? " · 共 " + d.total + " 个音频" : "");
      var rows = $("#tgaRows");
      if (!more) rows.innerHTML = "";
      if (!d.items.length && !more) rows.innerHTML = '<tr><td colspan="8" class="muted">这个频道里没有找到音频文件。</td></tr>';
      d.items.forEach(function (it) {
        var tr = document.createElement("tr");
        var done = !!(it.downloaded || it.imported);
        tr.setAttribute("data-msg-id", String(it.id));
        tr.setAttribute("data-done", done ? "1" : "0");
        tr.setAttribute("data-title", it.title || "");
        var marks = (it.imported ? '<span class="tga-mark is-imp">已导入</span>' : "") + (it.downloaded && !it.imported ? '<span class="tga-mark">已下载</span>' : "");
        tr.innerHTML = '<td class="tga-col-check"></td><td>' + esc(it.title) + marks + "</td><td>" + esc(it.performer || "-") +
          '</td><td class="tga-num">' + dur(it.duration) + '</td><td class="tga-num">' + mb(it.size) +
          '</td><td class="tga-num">' + esc(it.date) + '</td><td class="tga-st"></td><td></td>';
        var cb = document.createElement("input");
        cb.type = "checkbox"; cb.className = "tga-row-cb"; cb.disabled = !it.ok;
        if (!it.ok) cb.title = "格式不支持或文件过大";
        cb.addEventListener("change", syncSelUi);
        tr.querySelector(".tga-col-check").appendChild(cb);
        var b = document.createElement("button");
        b.type = "button"; b.className = "btn btn-sm"; b.textContent = it.ok ? (done ? "重新下载" : "下载并优化") : "不支持";
        if (it.ok && done) b.className = "btn btn-sm btn-ghost";
        b.disabled = !it.ok;
        if (!it.ok) b.title = "格式不支持或文件过大";
        b.addEventListener("click", function () { download(it, b); });
        tr.lastChild.appendChild(b);
        rows.appendChild(tr);
        paintRowStatus(tr);
      });
      nextOffset = d.next_offset;
      $("#tgaMore").hidden = !nextOffset;
      syncSelUi();
    }).catch(function (e) { busy(btn, false); say(e.message, "err"); });
  }
  function download(it, btn) {
    var tsel = document.getElementById("djoptTarget");
    busy(btn, true, "准备中…");
    api("POST", "/admin/tg-audio/download", { chat: chatRef, msg_id: it.id, target: tsel ? tsel.value : "-9" }).then(function (d) {
      btn.textContent = "已加入"; btn.disabled = true;
      say("开始下载：" + d.name + "，完成后自动进入「上传优化」。", "ok");
      apply(d);
      if (window.DJOpt && window.DJOpt.watch) window.DJOpt.watch(d.job_id);
    }).catch(function (e) { busy(btn, false); say(e.message, "err"); });
  }
  $("[data-tga-open]").addEventListener("click", function () {
    var v = $("#tgaChat").value.trim(); if (!v) { say("请输入频道链接或 @用户名", "err"); return; }
    $("#tgaResults").hidden = true;
    openChat(v, false, this);
  });
  $("#tgaMore").addEventListener("click", function () { openChat(chatRef, true, this); });
  $("#tgaSelAll").addEventListener("change", function () {
    var on = this.checked;
    Array.prototype.forEach.call(root.querySelectorAll("#tgaRows tr[data-msg-id]"), function (tr) {
      var cb = tr.querySelector("input.tga-row-cb");
      if (!cb || cb.disabled) return;
      // 全选默认跳过已下载/已导入；取消全选时清掉全部勾选
      cb.checked = on && tr.getAttribute("data-done") !== "1";
    });
    syncSelUi();
  });
  $("#tgaSelNone").addEventListener("click", function () {
    Array.prototype.forEach.call(root.querySelectorAll("#tgaRows input.tga-row-cb"), function (cb) { cb.checked = false; });
    var all = $("#tgaSelAll"); if (all) { all.checked = false; all.indeterminate = false; }
    syncSelUi();
  });
  $("#tgaBatchGo").addEventListener("click", function () {
    var items = selectedRows();
    if (!items.length) { say("请先勾选要下载的音频", "err"); return; }
    var tsel = document.getElementById("djoptTarget");
    var btn = this;
    busy(btn, true, "加入队列…");
    api("POST", "/admin/tg-audio/batch", { chat: chatRef, items: items, target: tsel ? tsel.value : "-9" }).then(function (d) {
      busy(btn, false);
      say(d.message || ("已加入 " + (d.queued || items.length) + " 首到下载队列"), "ok");
      Array.prototype.forEach.call(root.querySelectorAll("#tgaRows input.tga-row-cb"), function (cb) { cb.checked = false; });
      var all = $("#tgaSelAll"); if (all) { all.checked = false; all.indeterminate = false; }
      syncSelUi();
      apply(d);
      (d.batch && d.batch.items || []).forEach(function (it) {
        if (it.job_id && window.DJOpt && window.DJOpt.watch) window.DJOpt.watch(it.job_id);
      });
    }).catch(function (e) { busy(btn, false); say(e.message, "err"); });
  });
  $("#tgaBatchCancel").addEventListener("click", function () {
    var btn = this;
    busy(btn, true, "取消中…");
    api("POST", "/admin/tg-audio/batch/cancel", {}).then(function (d) {
      busy(btn, false);
      say(d.message || "已取消剩余排队", "ok");
      apply(d);
    }).catch(function (e) { busy(btn, false); say(e.message, "err"); });
  });
  $("[data-tga-search]").addEventListener("click", function () {
    var q = $("#tgaQ").value.trim(), btn = this;
    busy(btn, true, "搜索中…");
    api("POST", "/admin/tg-audio/search", { q: q }).then(function (d) {
      busy(btn, false);
      var ul = $("#tgaResults"); ul.innerHTML = ""; ul.hidden = false;
      if (!d.results.length) {
        ul.hidden = true;
        say(d.error || ("没有搜索到与「" + q + "」匹配的公开频道或群组。Telegram 的全局搜索只覆盖部分公开频道；如果你有频道链接或 @用户名，直接粘贴到上面的输入框点「打开频道」最准确。"), "err");
        return;
      }
      var m = q.match(/(?:t\.me|telegram\.me)\/(?:s\/)?([A-Za-z0-9_]{4,64})/i) || q.match(/^@([A-Za-z0-9_]{4,64})$/);
      var exact = m && d.results.filter(function (r) { return (r.username || "").toLowerCase() === m[1].toLowerCase(); })[0];
      if (exact || (/^\+|t\.me\/(\+|joinchat|c\/)/i.test(q) && d.results.length === 1)) {
        // a pasted link / @username: open that exact channel right away
        ul.hidden = true; $("#tgaChat").value = q; openChat((exact || d.results[0]).key, false, btn); return;
      }
      say("");
      d.results.forEach(function (r) {
        var li = document.createElement("li");
        var b = document.createElement("button"); b.type = "button"; b.className = "link-btn";
        b.textContent = r.title + (r.username ? "（@" + r.username + "）" : "") + " · " + r.type + (r.members ? " · " + r.members + " 人" : "");
        b.addEventListener("click", function () { ul.hidden = true; $("#tgaChat").value = r.username ? "@" + r.username : r.key; openChat(r.key, false, b); });
        li.appendChild(b); ul.appendChild(li);
      });
    }).catch(function (e) { busy(btn, false); say(e.message, "err"); });
  });
  ["#tgaChat", "#tgaQ"].forEach(function (sel) {
    $(sel).addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); root.querySelector(sel === "#tgaChat" ? "[data-tga-open]" : "[data-tga-search]").click(); }
    });
  });

  load();
})();

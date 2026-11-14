(function () {
  var root = document.getElementById("mixer");
  if (!root) return;

  function $(sel, el) { return (el || root).querySelector(sel); }
  function fmt(t) {
    if (!isFinite(t) || t < 0) return "0:00";
    t = Math.floor(t);
    var m = Math.floor(t / 60), s = t % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  var xf = document.getElementById("xfader");
  var hint = document.getElementById("xfadeHint");
  var objectUrls = { A: null, B: null };

  function xfGains(x) {
    // equal-power crossfade, x=0 full A, x=1 full B
    var a = Math.cos(x * Math.PI / 2);
    var b = Math.sin(x * Math.PI / 2);
    return { a: a, b: b };
  }

  function deck(id) {
    var audio = new Audio();
    audio.preload = "metadata";
    audio.playsInline = true;
    audio.crossOrigin = "anonymous";
    return {
      id: id,
      audio: audio,
      vol: 0.8,
      seeking: false,
      titleEl: $("[data-title='" + id + "']"),
      playBtn: $("[data-play='" + id + "']"),
      seek: $("[data-seek='" + id + "']"),
      timeEl: $("[data-time='" + id + "']"),
      volEl: $("[data-vol='" + id + "']"),
      volLab: $("[data-vol-lab='" + id + "']"),
      fileEl: $("[data-file='" + id + "']"),
      libEl: $("[data-lib='" + id + "']"),
      card: root.querySelector(".mixer-deck[data-deck='" + id + "']")
    };
  }

  var A = deck("A");
  var B = deck("B");

  // Web Audio graph (built only when recording is first used, so plain playback stays HTMLAudio as before).
  var graph = null;
  function clamp01(v) { return Math.max(0, Math.min(1, v)); }
  function ensureGraph() {
    if (graph) return graph;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    var ctx = new AC();
    var master = ctx.createGain();
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    var dest = ctx.createMediaStreamDestination ? ctx.createMediaStreamDestination() : null;
    var gA = ctx.createGain(), gB = ctx.createGain();
    ctx.createMediaElementSource(A.audio).connect(gA);
    ctx.createMediaElementSource(B.audio).connect(gB);
    gA.connect(master); gB.connect(master);
    master.connect(analyser);
    master.connect(ctx.destination);
    if (dest) master.connect(dest);
    graph = { ctx: ctx, gA: gA, gB: gB, master: master, analyser: analyser, dest: dest };
    applyGain();
    return graph;
  }
  function resumeCtx() {
    if (graph && graph.ctx.state === "suspended") graph.ctx.resume().catch(function () {});
  }

  function applyGain() {
    var x = (Number(xf.value) || 0) / 100;
    var g = xfGains(x);
    var va = clamp01(A.vol * g.a), vb = clamp01(B.vol * g.b);
    if (graph) {
      // Same gain law as before, applied in the graph; element volume stays at 1.
      A.audio.volume = 1; B.audio.volume = 1;
      var t = graph.ctx.currentTime;
      graph.gA.gain.setTargetAtTime(va, t, 0.01);
      graph.gB.gain.setTargetAtTime(vb, t, 0.01);
    } else {
      A.audio.volume = va;
      B.audio.volume = vb;
    }
    if (!hint) return;
    if (x <= 0.02) hint.textContent = "左侧 · 只听 A";
    else if (x >= 0.98) hint.textContent = "右侧 · 只听 B";
    else if (Math.abs(x - 0.5) < 0.04) hint.textContent = "居中 · 两轨同时可听";
    else hint.textContent = "偏" + (x < 0.5 ? "A" : "B") + " · A " + Math.round(g.a * 100) + "% / B " + Math.round(g.b * 100) + "%";
  }

  function setLoaded(d, url, title, revokePrev) {
    if (revokePrev && objectUrls[d.id]) {
      URL.revokeObjectURL(objectUrls[d.id]);
      objectUrls[d.id] = null;
    }
    d.audio.pause();
    d.audio.src = url;
    d.audio.load();
    d.titleEl.textContent = title || "未命名曲目";
    d.playBtn.disabled = false;
    d.seek.disabled = false;
    d.playBtn.textContent = "播放";
    d.card.classList.remove("is-playing");
    d.timeEl.textContent = "0:00 / 0:00";
    d.seek.value = "0";
  }

  function bind(d) {
    d.volEl.addEventListener("input", function () {
      d.vol = (Number(d.volEl.value) || 0) / 100;
      d.volLab.textContent = Math.round(d.vol * 100) + "%";
      applyGain();
    });
    d.playBtn.addEventListener("click", function () {
      if (d.audio.paused) {
        resumeCtx();
        d.audio.play().catch(function () { /* autoplay block */ });
      } else {
        d.audio.pause();
      }
    });
    d.audio.addEventListener("play", function () {
      d.playBtn.textContent = "暂停";
      d.card.classList.add("is-playing");
    });
    d.audio.addEventListener("pause", function () {
      d.playBtn.textContent = "播放";
      d.card.classList.remove("is-playing");
    });
    d.audio.addEventListener("ended", function () {
      d.playBtn.textContent = "播放";
      d.card.classList.remove("is-playing");
    });
    d.audio.addEventListener("timeupdate", function () {
      if (d.seeking) return;
      var dur = d.audio.duration || 0;
      var cur = d.audio.currentTime || 0;
      if (dur) d.seek.value = String(Math.round((cur / dur) * 1000));
      d.timeEl.textContent = fmt(cur) + " / " + fmt(dur);
    });
    d.audio.addEventListener("loadedmetadata", function () {
      d.timeEl.textContent = fmt(d.audio.currentTime) + " / " + fmt(d.audio.duration);
    });
    d.seek.addEventListener("input", function () {
      d.seeking = true;
      var dur = d.audio.duration || 0;
      if (dur) d.audio.currentTime = (Number(d.seek.value) / 1000) * dur;
      d.timeEl.textContent = fmt(d.audio.currentTime) + " / " + fmt(dur);
    });
    d.seek.addEventListener("change", function () { d.seeking = false; });
    if (d.fileEl) {
      d.fileEl.addEventListener("change", function () {
        var f = d.fileEl.files && d.fileEl.files[0];
        if (!f) return;
        if (objectUrls[d.id]) URL.revokeObjectURL(objectUrls[d.id]);
        var url = URL.createObjectURL(f);
        objectUrls[d.id] = url;
        if (d.libEl) d.libEl.value = "";
        setLoaded(d, url, f.name, false);
      });
    }
    if (d.libEl) {
      d.libEl.addEventListener("change", function () {
        var url = d.libEl.value;
        if (!url) return;
        var title = d.libEl.options[d.libEl.selectedIndex].textContent;
        setLoaded(d, url, title, true);
      });
    }
  }

  bind(A);
  bind(B);
  xf.addEventListener("input", applyGain);
  applyGain();

  document.getElementById("playBoth").addEventListener("click", function () {
    var jobs = [];
    resumeCtx();
    if (A.audio.src) jobs.push(A.audio.play());
    if (B.audio.src) jobs.push(B.audio.play());
    jobs.forEach(function (p) { if (p && p.catch) p.catch(function () {}); });
  });
  document.getElementById("pauseBoth").addEventListener("click", function () {
    A.audio.pause();
    B.audio.pause();
  });

  // ---- 录音：录下混音总输出 ----
  var recStart = document.getElementById("recStart");
  var recStop = document.getElementById("recStop");
  var recTimer = document.getElementById("recTimer");
  var recStatus = document.getElementById("recStatus");
  var recResult = document.getElementById("recResult");
  var recMeter = document.getElementById("recMeter");
  var recPeak = document.getElementById("recPeak");
  var recAuto = document.getElementById("recAuto");
  var recRepeat = document.getElementById("recRepeat");
  var recWarn = document.getElementById("recWarn");
  var recSysHint = document.getElementById("recSysHint");
  var recBox = document.getElementById("mixerRec");
  var recorder = null, recChunks = [], recT0 = 0, recTick = null, meterRaf = 0, recUrl = null, recMime = "";
  var REC_MAX_SEC = 3 * 3600;
  // per-recording state
  var rs = null;

  function recSource() {
    var el = document.querySelector("input[name='recSource']:checked");
    return el && el.value === "system" ? "system" : "deck";
  }
  function syncSourceUi() {
    var sys = recSource() === "system";
    if (recSysHint) recSysHint.hidden = !sys;
    var lbl = recStart && recStart.querySelector("span");
    if (lbl) lbl.textContent = sys ? "开始录电脑声音" : "开始录音";
  }
  Array.prototype.forEach.call(document.querySelectorAll("input[name='recSource']"), function (el) {
    el.addEventListener("change", syncSourceUi);
  });

  function pickMime() {
    if (!window.MediaRecorder) return null;
    var list = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4;codecs=mp4a.40.2", "audio/mp4", "audio/aac"];
    if (typeof MediaRecorder.isTypeSupported !== "function") return "";
    for (var i = 0; i < list.length; i++) {
      try { if (MediaRecorder.isTypeSupported(list[i])) return list[i]; } catch (e) {}
    }
    return "";
  }
  function extFor(mime) {
    mime = (mime || "").toLowerCase();
    if (mime.indexOf("ogg") >= 0) return ".ogg";
    if (mime.indexOf("mp4") >= 0 || mime.indexOf("aac") >= 0) return ".m4a";
    return ".webm";
  }
  function stamp() {
    var d = new Date();
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
  }
  function hms(sec) {
    sec = Math.floor(sec);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ":" : "") + (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }
  function curAnalyser() { return (rs && rs.analyser) || (graph && graph.analyser); }
  function meterLoop() {
    var an = curAnalyser();
    if (!an) return;
    var buf = new Float32Array(an.fftSize);
    function step() {
      var a2 = curAnalyser();
      if (a2 && a2.getFloatTimeDomainData) {
        if (a2.fftSize !== buf.length) buf = new Float32Array(a2.fftSize);
        a2.getFloatTimeDomainData(buf);
      }
      var peak = 0;
      for (var i = 0; i < buf.length; i++) { var v = Math.abs(buf[i]); if (v > peak) peak = v; }
      var db = peak > 0 ? 20 * Math.log10(peak) : -90;
      var pct = Math.max(0, Math.min(100, (db + 60) / 60 * 100));
      if (recMeter) {
        recMeter.style.width = pct + "%";
        recMeter.className = db > -1 ? "is-hot" : db > -6 ? "is-warm" : "";
      }
      if (recPeak) recPeak.textContent = db > -89 ? db.toFixed(1) + " dB" : "-∞";
      meterRaf = requestAnimationFrame(step);
    }
    cancelAnimationFrame(meterRaf);
    step();
  }
  function setRecUi(on) {
    if (recStart) recStart.disabled = on;
    if (recStop) recStop.disabled = !on;
    if (recBox) recBox.classList.toggle("is-recording", on);
    Array.prototype.forEach.call(document.querySelectorAll("input[name='recSource']"), function (el) { el.disabled = on; });
  }
  function showWarn(msg) {
    if (!recWarn) return;
    recWarn.textContent = msg || "";
    recWarn.hidden = !msg;
  }

  /* ---- auto-stop analysis: runs on the audio thread clock (ScriptProcessor), so it keeps working when the
     tab is in the background (timers / rAF get throttled there). ---- */
  var BAND_EDGES = (function () {
    var e = [], lo = 60, hi = 10000, n = 12;
    for (var i = 0; i <= n; i++) e.push(lo * Math.pow(hi / lo, i / n));
    return e;
  })();
  var SILENCE_DB = -55, SILENCE_SEC = 8, LOW_LEVEL_DB = -33, LOW_CHECK_SEC = 12, MUSIC_MIN_SEC = 10;
  function startAnalysis(ctx, srcNode) {
    var an = ctx.createAnalyser();
    an.fftSize = 2048;
    an.smoothingTimeConstant = 0.5;
    srcNode.connect(an);
    var hop = 4096;
    var sp = ctx.createScriptProcessor ? ctx.createScriptProcessor(hop, 2, 1) : null;
    rs.analyser = an;
    if (!sp) return;
    srcNode.connect(sp);
    var mute = ctx.createGain(); mute.gain.value = 0;
    sp.connect(mute); mute.connect(ctx.destination);
    rs.sp = sp; rs.mute = mute; rs.src = srcNode;
    var frameSec = hop / ctx.sampleRate;
    rs.det = window.DJRepeat ? window.DJRepeat.create({ frameSec: frameSec, refSec: 20, minElapsed: 60, confirmSec: 10 }) : null;
    var bins = new Float32Array(an.frequencyBinCount);
    var binHz = ctx.sampleRate / an.fftSize;
    var idx = [];
    for (var b = 0; b < 12; b++) {
      var a0 = Math.max(1, Math.floor(BAND_EDGES[b] / binHz)), a1 = Math.max(a0 + 1, Math.floor(BAND_EDGES[b + 1] / binHz));
      idx.push([a0, Math.min(a1, bins.length)]);
    }
    var bands = new Float32Array(12);
    var t0 = ctx.currentTime;
    sp.onaudioprocess = function (ev) {
      if (!rs || rs.done) return;
      var t = ctx.currentTime - t0;
      var inp = ev.inputBuffer, ch = inp.numberOfChannels, n = inp.length, sum = 0;
      for (var c = 0; c < ch; c++) { var d = inp.getChannelData(c); for (var i = 0; i < n; i += 2) sum += d[i] * d[i]; }
      var rms = Math.sqrt(sum / Math.max(1, ch * n / 2));
      var rmsDb = rms > 0 ? 20 * Math.log10(rms) : -120;
      an.getFloatFrequencyData(bins);
      for (var k = 0; k < 12; k++) {
        var p = 0, r = idx[k];
        for (var j = r[0]; j < r[1]; j++) p += Math.pow(10, bins[j] / 10);
        bands[k] = 10 * Math.log10(p / Math.max(1, r[1] - r[0]) + 1e-12);
      }
      onFrame(bands, rmsDb, t);
    };
  }
  function onFrame(bands, rmsDb, t) {
    // music presence / level bookkeeping
    if (rmsDb > SILENCE_DB) {
      if (rs.musicAt == null) rs.musicAt = t;
      rs.lastLoud = t;
      if (rmsDb > rs.maxDb) rs.maxDb = rmsDb;
    }
    if (rs.musicAt != null && !rs.levelChecked && t - rs.musicAt >= LOW_CHECK_SEC) {
      rs.levelChecked = true;
      if (rs.maxDb < LOW_LEVEL_DB) {
        rs.silenceOff = true;
        showWarn("音量太小，请把播放软件音量调到100（已关闭静音自动停止，重复检测仍然有效；请手动停止）");
      }
    }
    if (!rs.levelChecked && rs.musicAt == null && t > 20 && rs.source === "system") {
      // nothing at all after 20s: probably the wrong window / audio not shared
      if (!rs.warnedNoSignal) { rs.warnedNoSignal = true; showWarn("还没有检测到声音：请确认播放软件正在播放、音量不是 0，并且共享时勾选了「共享系统音频」。"); }
    }
    if (rs.warnedNoSignal && rs.musicAt != null && !rs.silenceOff) { rs.warnedNoSignal = false; showWarn(""); }
    // silence auto-stop
    if (!rs.silenceOff && rs.musicAt != null && rs.lastLoud - rs.musicAt >= MUSIC_MIN_SEC && t - rs.lastLoud > SILENCE_SEC) {
      autoStop("silence", rs.lastLoud + 1);
      return;
    }
    // repeat auto-stop
    if (rs.det && recRepeat && recRepeat.checked) {
      var hit = rs.det.push(bands, rmsDb, t);
      if (hit) autoStop("repeat", hit.trimAt);
    } else if (rs.det) {
      rs.det.push(bands, rmsDb, t); // keep the history so re-enabling mid-recording still works
    }
  }
  function autoStop(reason, trimAt) {
    if (!rs || rs.done) return;
    rs.reason = reason;
    rs.trimEnd = Math.max(1, trimAt);
    stopRec();
  }
  function teardown() {
    if (!rs) return;
    rs.done = true;
    try { if (rs.sp) { rs.sp.onaudioprocess = null; rs.sp.disconnect(); } } catch (e) {}
    try { if (rs.mute) rs.mute.disconnect(); } catch (e) {}
    try { if (rs.src && rs.source === "system") rs.src.disconnect(); else if (rs.src && rs.sp) rs.src.disconnect(rs.sp); } catch (e) {}
    try { if (rs.src && rs.analyser && rs.source !== "system") rs.src.disconnect(rs.analyser); } catch (e) {}
    if (rs.stream) rs.stream.getTracks().forEach(function (tr) { try { tr.stop(); } catch (e) {} });
  }

  function makeRecorder(stream) {
    var opts = { audioBitsPerSecond: 256000 };
    if (recMime) opts.mimeType = recMime;
    try { return new MediaRecorder(stream, opts); } catch (e) {
      try { return new MediaRecorder(stream); } catch (e2) {
        recStatus.textContent = "无法开始录音：" + (e2 && e2.message ? e2.message : "浏览器拒绝");
        return null;
      }
    }
  }
  function beginWith(stream, srcNode, source) {
    var g = graph;
    rs = { source: source, maxDb: -120, musicAt: null, lastLoud: 0, levelChecked: false, silenceOff: false, trimEnd: null, reason: "", stream: source === "system" ? stream : null };
    recorder = makeRecorder(stream);
    if (!recorder) { teardown(); rs = null; return; }
    startAnalysis(g.ctx, srcNode);
    recChunks = [];
    recorder.ondataavailable = function (ev) { if (ev.data && ev.data.size) recChunks.push(ev.data); };
    recorder.onstop = finishRec;
    recorder.start(1000);
    recT0 = Date.now();
    setRecUi(true);
    showWarn("");
    meterLoop();
    var auto = (recRepeat && recRepeat.checked) ? "检测到曲目从头重复、或音乐停止 8 秒以上会自动停止；" : "音乐停止 8 秒以上会自动停止；";
    recStatus.textContent = source === "system"
      ? "正在录电脑声音… " + auto + "也可以随时点「停止」。"
      : "录音中…（录的是打碟台的总输出，先在上面播放 A/B 两轨）" + auto + "也可以随时点「停止」。";
    if (recResult) { recResult.hidden = true; recResult.innerHTML = ""; }
    clearInterval(recTick);
    recTick = setInterval(function () {
      var sec = (Date.now() - recT0) / 1000;
      recTimer.textContent = hms(sec);
      if (sec >= REC_MAX_SEC) stopRec();
    }, 250);
  }

  function startRec() {
    recMime = pickMime();
    if (recMime === null) {
      recStatus.textContent = "这个浏览器不支持录音（MediaRecorder）。请用电脑版 Chrome / Edge / Firefox，或 iPhone 上升级到 iOS 14.5 以上的 Safari。";
      return;
    }
    var g = ensureGraph();
    if (!g || !g.dest) {
      recStatus.textContent = "这个浏览器不支持 Web Audio 录音，请换用电脑版 Chrome / Edge / Firefox。";
      return;
    }
    resumeCtx();
    if (recSource() !== "system") { beginWith(g.dest.stream, g.master, "deck"); return; }
    var md = navigator.mediaDevices;
    if (!md || !md.getDisplayMedia) {
      recStatus.textContent = "这个浏览器不支持录电脑声音。请用 Windows 电脑上的 Chrome 或 Edge。";
      return;
    }
    recStatus.textContent = "请在弹出的共享窗口里选择「整个屏幕」（或正在放歌的标签页），并勾选「共享系统音频 / 共享标签页音频」，然后点「共享」。";
    recStart.disabled = true;
    md.getDisplayMedia({
      video: true,
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      systemAudio: "include",
      selfBrowserSurface: "include"
    }).then(function (stream) {
      var at = stream.getAudioTracks();
      stream.getVideoTracks().forEach(function (tr) { try { tr.stop(); } catch (e) {} stream.removeTrack(tr); });
      if (!at.length) {
        stream.getTracks().forEach(function (tr) { try { tr.stop(); } catch (e) {} });
        recStart.disabled = false;
        recStatus.textContent = "";
        showWarn("没有拿到声音：共享时必须勾选「共享系统音频」（整个屏幕）或「共享标签页音频」（标签页）。只共享窗口是没有声音的；Mac 只能录浏览器标签页的声音。请重新点开始再试。");
        return;
      }
      var audioOnly = new MediaStream(at);
      var src = g.ctx.createMediaStreamSource(audioOnly);
      at[0].addEventListener("ended", function () { if (recorder && recorder.state === "recording") stopRec(); });
      beginWith(audioOnly, src, "system");
    }).catch(function (e) {
      recStart.disabled = false;
      var name = e && e.name;
      recStatus.textContent = name === "NotAllowedError" ? "已取消共享，没有开始录音。" : "无法录电脑声音：" + (e && e.message ? e.message : "浏览器拒绝");
    });
  }
  function stopRec() {
    if (recorder && recorder.state !== "inactive") {
      try { recorder.requestData(); } catch (e) {}
      recorder.stop();
    }
    clearInterval(recTick);
    setRecUi(false);
  }
  function finishRec() {
    var type = (recorder && recorder.mimeType) || recMime || "audio/webm";
    var blob = new Blob(recChunks, { type: type });
    recChunks = [];
    var secs = (Date.now() - recT0) / 1000;
    recorder = null;
    var st = rs || {};
    teardown();
    rs = null;
    cancelAnimationFrame(meterRaf);
    if (!blob.size || blob.size < 2048) {
      recStatus.textContent = "录音是空的，请先播放曲目再录。";
      return;
    }
    var trim = st.trimEnd && st.trimEnd < secs - 0.5 ? Math.round(st.trimEnd * 10) / 10 : null;
    if (recUrl) URL.revokeObjectURL(recUrl);
    recUrl = URL.createObjectURL(blob);
    var name = "DJ录音-" + stamp() + extFor(type);
    recResult.innerHTML = "";
    var player = document.createElement("audio");
    player.controls = true; player.src = recUrl; player.preload = "metadata";
    var a = document.createElement("a");
    a.className = "btn btn-sm btn-ghost"; a.href = recUrl; a.download = name; a.textContent = "下载原始录音";
    var info = document.createElement("span");
    info.className = "muted"; info.textContent = name + " · " + hms(secs) + " · " + (blob.size / 1048576).toFixed(1) + "MB";
    recResult.appendChild(player); recResult.appendChild(a); recResult.appendChild(info);
    var why = "";
    if (st.reason === "repeat") why = "检测到曲目从头开始重复，已自动停止。";
    else if (st.reason === "silence") why = "音乐已停止超过 8 秒，已自动停止。";
    if (trim) why += "优化版会只保留前 " + hms(trim) + "（裁掉后面重复/静音的部分）；原始录音仍是完整的。";
    if (why) {
      var note = document.createElement("p");
      note.className = "mixer-rec-why"; note.textContent = why;
      recResult.appendChild(note);
    }
    recResult.hidden = false;
    var auto = recAuto ? recAuto.checked : false;
    var prefix = st.reason ? why.split("。")[0] + "。" : "录音完成，";
    if (auto && window.DJOpt && window.DJOpt.loggedIn) {
      recStatus.textContent = prefix + "正在上传到「上传优化」…";
      var tsel = document.getElementById("djoptTarget");
      window.DJOpt.uploadBlob(blob, name, "record", tsel ? tsel.value : "-9", null, trim ? { trim_end: trim } : null).then(function () {
        recStatus.textContent = prefix + "已上传，正在生成优化版，进度见下方「上传优化」。";
        var opt = document.getElementById("djopt");
        if (opt && opt.scrollIntoView) opt.scrollIntoView({ behavior: "smooth", block: "start" });
      }).catch(function (e) {
        recStatus.textContent = "自动上传失败：" + e.message + "（可先下载原始录音，再手动上传）";
      });
    } else if (window.DJOpt && !window.DJOpt.loggedIn) {
      recStatus.textContent = prefix + "登录后可自动上传优化；现在可以先下载原始录音。";
    } else {
      recStatus.textContent = prefix + "可试听或下载原始录音。";
    }
  }

  if (recStart) {
    if (!window.MediaRecorder) {
      recStatus.textContent = "提示：这个浏览器不支持录音，请用电脑版 Chrome / Edge / Firefox，或 iOS 14.5+ 的 Safari。";
    }
    syncSourceUi();
    recStart.addEventListener("click", startRec);
    recStop.addEventListener("click", stopRec);
  }
  function onBeforeUnload(e) {
    if (recorder && recorder.state === "recording") { e.preventDefault(); e.returnValue = ""; return ""; }
  }
  window.addEventListener("beforeunload", onBeforeUnload);
  window.DJPageLeave = window.DJPageLeave || [];
  window.DJPageLeave.push(function () {
    window.removeEventListener("beforeunload", onBeforeUnload);
    if (recorder && recorder.state !== "inactive") { recorder.onstop = null; try { recorder.stop(); } catch (e) {} }
    teardown();
    rs = null;
    clearInterval(recTick);
    cancelAnimationFrame(meterRaf);
  });
  // test hook (used by the automated browser test only)
  window.DJMixerTest = { state: function () { return rs ? { musicAt: rs.musicAt, maxDb: rs.maxDb, silenceOff: rs.silenceOff, det: rs.det ? rs.det.info() : null } : null; } };
})();

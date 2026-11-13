/* Coarse "the track started again" detector for the mixer recorder.
 * Input: one frame every frameSec seconds = band energies in dB (any length, e.g. 12 log bands) + RMS dB.
 * Reference = the first refSec of audio after the signal starts. Later windows of the same length are
 * compared (per-band mean removed, Pearson correlation, so it is volume-insensitive). A candidate must
 * then keep matching for confirmSec more before it fires. Works in the browser (window.DJRepeat) and node.
 */
(function (root) {
  function create(opts) {
    opts = opts || {};
    var frameSec = opts.frameSec || 0.1;
    var refLen = Math.max(10, Math.round((opts.refSec || 20) / frameSec));
    var confLen = Math.max(5, Math.round((opts.confirmSec || 10) / frameSec));
    var minElapsed = opts.minElapsed || 60;
    var thr = opts.threshold || 0.78;
    var signalDb = opts.signalDb != null ? opts.signalDb : -60;
    var maxFrames = Math.round((opts.maxSec || 3 * 3600) / frameSec);
    var smooth = Math.max(1, opts.smooth || 4);
    var floorDb = 0, nPre = 0;
    var frames = [], times = [], raw = [], s0 = -1, cand = null, best = 0, lastCheck = -1;

    function corr(aStart, bStart, len) {
      var nb = frames[0].length, i, k, ma = new Float64Array(nb), mb = new Float64Array(nb);
      for (i = 0; i < len; i++) {
        var fa = frames[aStart + i], fb = frames[bStart + i];
        for (k = 0; k < nb; k++) { ma[k] += fa[k]; mb[k] += fb[k]; }
      }
      for (k = 0; k < nb; k++) { ma[k] /= len; mb[k] /= len; }
      var sab = 0, saa = 0, sbb = 0;
      for (i = 0; i < len; i++) {
        var xa = frames[aStart + i], xb = frames[bStart + i];
        for (k = 0; k < nb; k++) {
          var a = xa[k] - ma[k], b = xb[k] - mb[k];
          sab += a * b; saa += a * a; sbb += b * b;
        }
      }
      if (saa <= 1e-9 || sbb <= 1e-9) return 0;
      return sab / Math.sqrt(saa * sbb);
    }

    function push(bands, rmsDb, t) {
      if (frames.length >= maxFrames) return null;
      var v = new Float32Array(bands.length);
      for (var k = 0; k < bands.length; k++) {
        var x = bands[k];
        v[k] = isFinite(x) ? Math.max(-120, x) : -120;
      }
      // short moving average makes the match insensitive to where the analysis hops fall
      raw.push(v); if (raw.length > smooth) raw.shift();
      var sm = new Float32Array(v.length);
      for (var r = 0; r < raw.length; r++) for (k = 0; k < v.length; k++) sm[k] += raw[r][k] / raw.length;
      frames.push(sm); times.push(t);
      var n = frames.length;
      if (s0 < 0) {
        // onset = above an absolute floor AND clearly above the quietest level seen so far (hiss / hum)
        if (nPre === 0 || rmsDb < floorDb) floorDb = rmsDb;
        nPre++;
        if (rmsDb > signalDb && rmsDb > floorDb + 15) s0 = n - 1;
        // music already playing when the recording started: no quiet lead-in to rise from
        else if (nPre === 15 && floorDb > -35) s0 = 0;
        return null;
      }
      if (n - s0 < refLen) return null;
      if (t - times[s0] < minElapsed) return null;
      if (cand !== null) {
        // keep the best alignment among the first few frames after the crossing
        var start = n - refLen;
        if (start <= cand.start + 3) {
          var c2 = corr(s0, start, refLen);
          if (c2 > cand.score) { cand.start = start; cand.score = c2; }
        }
        if (n >= cand.start + refLen + confLen) {
          var total = refLen + confLen;
          var ok = 0;
          for (var d = -2; d <= 2; d++) {
            var st = cand.start + d;
            if (st <= s0 + refLen || st + total > n) continue;
            ok = Math.max(ok, corr(s0, st, total));
          }
          var hit = cand;
          cand = null;
          if (ok >= thr - 0.04) {
            return { event: "repeat", trimAt: times[hit.start], score: ok, refStart: times[s0] };
          }
        }
        return null;
      }
      var ws = n - refLen;
      if (ws <= s0 + refLen) return null;
      var c = corr(s0, ws, refLen);
      if (c > best) best = c;
      lastCheck = c;
      if (c >= thr) cand = { start: ws, score: c };
      return null;
    }
    return {
      push: push,
      info: function () { return { frames: frames.length, signalAt: s0 >= 0 ? times[s0] : null, best: best, last: lastCheck, pending: !!cand }; }
    };
  }
  var api = { create: create };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DJRepeat = api;
})(typeof window !== "undefined" ? window : this);

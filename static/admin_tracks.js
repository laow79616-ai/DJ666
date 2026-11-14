/* In-place track category save: no full reload, no hash/scroll jump. */
(function () {
  function readPayload(res) {
    return res.text().then(function (text) {
      var data = null;
      try { data = text ? JSON.parse(text) : null; } catch (err) { data = null; }
      return { ok: res.ok, status: res.status, data: data };
    });
  }

  function bindSelect(select) {
    if (!select || select.dataset.catBound === "1") return;
    var form = select.form;
    if (!form) return;
    select.dataset.catBound = "1";
    select.removeAttribute("onchange");
    select.onchange = null;
    var prev = select.getAttribute("data-prev");
    if (prev === null) prev = select.value;
    var status = form.querySelector(".track-cat-status");
    if (!status) {
      status = document.createElement("span");
      status.className = "track-cat-status";
      status.setAttribute("aria-live", "polite");
      form.appendChild(status);
    }
    var busy = false;
    var hideTimer = 0;

    function setStatus(kind, text) {
      status.textContent = text || "";
      status.className = "track-cat-status" + (kind ? " " + kind : "");
      if (hideTimer) clearTimeout(hideTimer);
      if (kind === "is-ok") {
        hideTimer = setTimeout(function () {
          status.textContent = "";
          status.className = "track-cat-status";
        }, 2200);
      }
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
    });

    select.addEventListener("change", function () {
      if (busy) {
        select.value = prev;
        return;
      }
      var next = select.value;
      if (next === prev) return;
      busy = true;
      select.classList.add("is-saving");
      setStatus("", "");
      var fd = new FormData(form);
      fd.set("category", next);
      fetch(form.getAttribute("action") || form.action, {
        method: "POST",
        body: fd,
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "X-Requested-With": "dj666-ajax"
        }
      }).then(readPayload).then(function (res) {
        if (!res.data || !res.data.ok) {
          var msg = (res.data && res.data.error) ? res.data.error : "保存失败";
          throw new Error(msg);
        }
        prev = res.data.category == null ? "" : String(res.data.category);
        select.setAttribute("data-prev", prev);
        if (select.value !== prev) select.value = prev;
        setStatus("is-ok", "已保存");
      }).catch(function (err) {
        select.value = prev;
        setStatus("is-err", (err && err.message) || "保存失败");
      }).finally(function () {
        busy = false;
        select.classList.remove("is-saving");
      });
    });
  }

  document.querySelectorAll("select.track-cat-select").forEach(bindSelect);
})();

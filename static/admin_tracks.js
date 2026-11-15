/* In-place track category save + async refresh of the admin track tables. */
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

  function bindAll(root) {
    (root || document).querySelectorAll("select.track-cat-select").forEach(bindSelect);
  }

  function refreshAdminTracks() {
    var box = document.getElementById("admin-track-tables");
    if (!box) return Promise.resolve();
    if (box.getAttribute("data-refreshing") === "1") {
      box.setAttribute("data-refresh-again", "1");
      return Promise.resolve();
    }
    box.setAttribute("data-refreshing", "1");
    return fetch("/admin/tracks/fragment", {
      credentials: "same-origin",
      headers: { "Accept": "text/html", "X-Requested-With": "dj666-ajax" }
    }).then(function (res) {
      if (!res.ok) throw new Error("refresh " + res.status);
      return res.text();
    }).then(function (html) {
      box.innerHTML = html;
      bindAll(box);
      var pending = box.querySelectorAll(".ann-log-title").length;
      var n = (box.querySelector("h3.ann-log-title") && box.querySelector("[data-pending-count]"));
      var countEl = document.querySelector("#tracks .admin-pane-head .count");
      var pendingCount = box.querySelectorAll("h3.ann-log-title").length; // placeholder
      var pendingRows = 0;
      var heads = box.querySelectorAll("h3.ann-log-title");
      heads.forEach(function (h) {
        if (h.textContent.indexOf("待审核") !== -1) {
          var tbl = h.parentNode && h.parentNode.querySelector("tbody");
          if (tbl) {
            pendingRows = tbl.querySelectorAll("tr").length;
            if (tbl.querySelector("td.muted") && pendingRows === 1) pendingRows = 0;
          }
        }
      });
      var tab = document.querySelector('.admin-tab[data-tab="tracks"] .tab-badge');
      if (pendingRows > 0) {
        if (!tab) {
          var a = document.querySelector('.admin-tab[data-tab="tracks"]');
          if (a) {
            tab = document.createElement("span");
            tab.className = "tab-badge";
            a.appendChild(tab);
          }
        }
        if (tab) tab.textContent = String(pendingRows);
        if (countEl) countEl.textContent = pendingRows + " 待审核";
      } else {
        if (tab) tab.remove();
        if (countEl) countEl.remove();
      }
    }).catch(function () {}).finally(function () {
      box.removeAttribute("data-refreshing");
      if (box.getAttribute("data-refresh-again") === "1") {
        box.removeAttribute("data-refresh-again");
        refreshAdminTracks();
      }
    });
  }

  bindAll(document);
  window.refreshAdminTracks = refreshAdminTracks;
})();

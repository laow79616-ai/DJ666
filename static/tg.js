/* DJ666 Telegram-style media: lightbox for announcement images. */
(function () {
  var box, img, idx = 0, list = [];
  function ensure() {
    if (box) return;
    box = document.createElement("div");
    box.className = "tg-lightbox";
    box.hidden = true;
    box.innerHTML = '<button type="button" class="tg-lb-close" aria-label="关闭">×</button>' +
      '<button type="button" class="tg-lb-prev" aria-label="上一张">‹</button>' +
      '<img alt=""><button type="button" class="tg-lb-next" aria-label="下一张">›</button>' +
      '<span class="tg-lb-count"></span>';
    document.body.appendChild(box);
    img = box.querySelector("img");
    box.addEventListener("click", function (e) {
      if (e.target === box || e.target.classList.contains("tg-lb-close")) close();
      else if (e.target.classList.contains("tg-lb-prev")) show(idx - 1);
      else if (e.target.classList.contains("tg-lb-next")) show(idx + 1);
    });
    document.addEventListener("keydown", function (e) {
      if (box.hidden) return;
      if (e.key === "Escape") close();
      if (e.key === "ArrowLeft") show(idx - 1);
      if (e.key === "ArrowRight") show(idx + 1);
    });
    var sx = null;
    box.addEventListener("touchstart", function (e) { sx = e.touches[0].clientX; }, { passive: true });
    box.addEventListener("touchend", function (e) {
      if (sx === null) return;
      var dx = e.changedTouches[0].clientX - sx;
      if (Math.abs(dx) > 40) show(idx + (dx < 0 ? 1 : -1));
      sx = null;
    });
  }
  function show(i) {
    if (!list.length) return;
    idx = (i + list.length) % list.length;
    img.src = list[idx];
    box.querySelector(".tg-lb-count").textContent = list.length > 1 ? (idx + 1) + " / " + list.length : "";
    box.querySelector(".tg-lb-prev").hidden = list.length < 2;
    box.querySelector(".tg-lb-next").hidden = list.length < 2;
  }
  function close() { box.hidden = true; img.src = ""; document.body.classList.remove("tg-lb-open"); }
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a[data-lightbox]") : null;
    if (!a || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    ensure();
    var group = a.closest(".tg-media");
    var links = group ? group.querySelectorAll("a[data-lightbox]") : [a];
    list = Array.prototype.map.call(links, function (el) { return el.getAttribute("href"); });
    box.hidden = false;
    document.body.classList.add("tg-lb-open");
    show(Array.prototype.indexOf.call(links, a));
  });
})();

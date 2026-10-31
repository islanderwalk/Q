(function () {
  var ctx = document.createElement('canvas').getContext('2d');
  function place() {
    document.querySelectorAll('g.n image.gi').forEach(function (im) {
      var t = im.nextElementSibling;
      if (!t.dataset.cx) t.dataset.cx = t.getAttribute('x');
      var cs = getComputedStyle(t);
      ctx.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
      var w = ctx.measureText(t.textContent).width, cx = +t.dataset.cx, y = +t.getAttribute('y');
      var left = cx - (w + 24) / 2;
      im.setAttribute('x', left.toFixed(1));
      im.setAttribute('y', (y - 9).toFixed(1));
      t.setAttribute('x', (left + 24 + w / 2).toFixed(1));
    });
  }
  place();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(place);
})();

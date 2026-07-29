// エーテルアーキ — main.js

(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* --------------------------------------------------------
     製図シートの方眼
     細線 32px / 5本ごとに太線。静的な下地として敷く。
     -------------------------------------------------------- */
  var canvas = document.getElementById('sheet');

  function drawSheet() {
    var ctx = canvas.getContext('2d');
    var dpr = window.devicePixelRatio || 1;
    var w = window.innerWidth;
    var h = window.innerHeight;

    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var step = 32;
    var major = 5; // 5本ごとに濃くする

    // 0.5 ずらして 1px を滲ませずに描く
    function line(x1, y1, x2, y2) {
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }

    ctx.lineWidth = 1;

    var i, n;
    for (i = 0, n = 0; i <= w; i += step, n++) {
      ctx.strokeStyle = n % major === 0 ? 'rgba(22,23,26,0.07)' : 'rgba(22,23,26,0.04)';
      line(Math.floor(i) + 0.5, 0, Math.floor(i) + 0.5, h);
    }
    for (i = 0, n = 0; i <= h; i += step, n++) {
      ctx.strokeStyle = n % major === 0 ? 'rgba(22,23,26,0.07)' : 'rgba(22,23,26,0.04)';
      line(0, Math.floor(i) + 0.5, w, Math.floor(i) + 0.5);
    }
  }

  if (canvas && canvas.getContext) {
    drawSheet();

    var resizeTimer = null;
    window.addEventListener('resize', function () {
      if (resizeTimer) window.cancelAnimationFrame(resizeTimer);
      resizeTimer = window.requestAnimationFrame(drawSheet);
    });
  }

  /* --------------------------------------------------------
     ヘッダー — スクロールで地色と罫を出す
     -------------------------------------------------------- */
  var head = document.getElementById('head');

  function onScroll() {
    head.classList.toggle('is-stuck', window.scrollY > 8);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* --------------------------------------------------------
     モバイルメニュー
     -------------------------------------------------------- */
  var burger = document.getElementById('burger');
  var nav = document.getElementById('nav');

  function closeNav() {
    nav.classList.remove('is-on');
    burger.classList.remove('is-on');
    burger.setAttribute('aria-expanded', 'false');
    burger.setAttribute('aria-label', 'メニューを開く');
  }

  burger.addEventListener('click', function () {
    var on = nav.classList.toggle('is-on');
    burger.classList.toggle('is-on', on);
    burger.setAttribute('aria-expanded', String(on));
    burger.setAttribute('aria-label', on ? 'メニューを閉じる' : 'メニューを開く');
  });

  Array.prototype.forEach.call(nav.querySelectorAll('a'), function (a) {
    a.addEventListener('click', closeNav);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && nav.classList.contains('is-on')) {
      closeNav();
      burger.focus();
    }
  });

  /* --------------------------------------------------------
     スクロールに応じた出現
     見出しの罫は左から引かれ、本文は控えめに現れる
     -------------------------------------------------------- */
  var heads = document.querySelectorAll('.sec__h');
  var blocks = document.querySelectorAll(
    '.sec__lead, .about, .specs, .grid, .proc, .contact'
  );

  if (!reduceMotion) {
    Array.prototype.forEach.call(blocks, function (el) {
      el.classList.add('reveal');
    });
  }

  var targets = [];
  Array.prototype.push.apply(targets, heads);
  Array.prototype.push.apply(targets, blocks);

  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-in');
          io.unobserve(entry.target);
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -8% 0px' }
    );
    targets.forEach(function (el) { io.observe(el); });
  } else {
    targets.forEach(function (el) { el.classList.add('is-in'); });
  }

  /* --------------------------------------------------------
     フッターの年号
     -------------------------------------------------------- */
  document.getElementById('year').textContent = String(new Date().getFullYear());
})();

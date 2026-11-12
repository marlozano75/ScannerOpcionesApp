// Vista de velas de los gráficos ampliados (TradingView Lightweight Charts, ver ui/tv_charts.py). Los gráficos llegan como fragmentos HTML
// (`.tvw`) que cada pantalla inserta en su capa: aquí se atiende el conmutador «Cierres / Velas» por delegación y se dibuja al pedirlo.
(function () {
  var libLoading = null, PREF = 'chartView';
  function lib() {
    if (window.LightweightCharts) return Promise.resolve();
    return libLoading || (libLoading = new Promise(function (ok, ko) {
      var s = document.createElement('script');
      s.src = '/static/lightweight-charts.js';
      s.onload = function () { ok(); };
      s.onerror = function () { libLoading = null; ko(new Error('librería')); };
      document.head.appendChild(s);
    }));
  }
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function pref() { try { return localStorage.getItem(PREF); } catch (e) { return null; } }
  function remember(v) { try { localStorage.setItem(PREF, v); } catch (e) { /* sin almacenamiento: no se recuerda */ } }
  function fmt(v) { return v == null ? '—' : v.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function iso(d) { return d.toISOString().slice(0, 10); }

  // días hábiles desde el siguiente a `last` hasta `until` (el eje se alarga para que quepan el vencimiento y los resultados)
  function futureDays(last, until) {
    var out = [], d = new Date(last + 'T00:00:00Z'), end = new Date(until + 'T00:00:00Z');
    for (var n = 0; n < 400; n++) {
      d.setUTCDate(d.getUTCDate() + 1);
      if (d > end) break;
      if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.push(iso(d));
    }
    return out;
  }
  function sma(candles, n) {
    var out = [], sum = 0;
    for (var i = 0; i < candles.length; i++) {
      sum += candles[i].close;
      if (i >= n) sum -= candles[i - n].close;
      if (i >= n - 1) out.push({ time: candles[i].time, value: sum / n });
    }
    return out;
  }

  function draw(box, data) {
    var LC = window.LightweightCharts, host = box.querySelector('.tvw-chart'), legend = box.querySelector('.tvw-legend');
    var lines = JSON.parse(box.dataset.lines || '[]'), events = JSON.parse(box.dataset.events || '[]');
    var candles = data.candles, last = candles[candles.length - 1].time, line = css('--line'), up = css('--green'), down = css('--red');
    host.textContent = '';
    var chart = LC.createChart(host, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: css('--ink'), fontSize: 11, fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      rightPriceScale: { borderColor: css('--line-strong'), scaleMargins: { top: 0.08, bottom: 0.24 } },
      timeScale: { borderColor: css('--line-strong'), rightOffset: 3 },
      crosshair: { mode: LC.CrosshairMode.Normal },
      localization: { locale: 'es-ES', dateFormat: 'dd MMM \'yy' }
    });
    // bandas de fechas (vencimiento, resultados): una barra de altura completa en una escala propia y oculta, por debajo de las velas
    var until = events.reduce(function (m, e) { return e.time > m ? e.time : m; }, last);
    var future = futureDays(last, until), known = {};
    candles.forEach(function (c) { known[c.time] = 1; }); future.forEach(function (t) { known[t] = 1; });
    var bands = events.filter(function (e) { return known[e.time]; }).sort(function (a, b) { return a.time < b.time ? -1 : 1; });
    if (bands.length) {
      var ev = chart.addHistogramSeries({ priceScaleId: 'ev', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
      chart.priceScale('ev').applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
      var seen = {};
      ev.setData(bands.filter(function (e) { return seen[e.time] ? false : (seen[e.time] = 1); }).map(function (e) { return { time: e.time, value: 1, color: e.color + '55' }; }));
    }
    var volume = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
    volume.setData(data.volume.map(function (v) { return { time: v.time, value: v.value, color: (v.up ? up : down) + '66' }; }));
    var prices = lines.map(function (l) { return l.price; });
    var series = chart.addCandlestickSeries({
      upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down,
      // los strikes quedan lejos del precio: sin esto la escala automática los dejaría fuera de la vista
      autoscaleInfoProvider: function (original) {
        var res = original();
        if (res && res.priceRange && prices.length) {
          res.priceRange.minValue = Math.min.apply(null, prices.concat(res.priceRange.minValue));
          res.priceRange.maxValue = Math.max.apply(null, prices.concat(res.priceRange.maxValue));
        }
        return res;
      }
    });
    series.setData(candles.concat(future.map(function (t) { return { time: t }; })));
    lines.forEach(function (l) {
      series.createPriceLine({ price: l.price, color: l.color, lineWidth: 2, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: l.title });
    });
    var volAt = {};
    data.volume.forEach(function (v) { volAt[v.time] = v.value; });
    function describe(c) {
      if (!c || c.open == null) { legend.innerHTML = '&nbsp;'; return; }
      var chg = c.open ? (c.close - c.open) / c.open * 100 : 0, v = volAt[c.time];
      legend.textContent = new Date(c.time + 'T00:00:00Z').toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
        + '   A ' + fmt(c.open) + '   Máx ' + fmt(c.high) + '   Mín ' + fmt(c.low) + '   C ' + fmt(c.close)
        + '   ' + (chg >= 0 ? '+' : '') + chg.toFixed(2).replace('.', ',') + ' %' + (v ? '   Vol ' + Math.round(v).toLocaleString('es-ES') : '');
    }
    var byTime = {};
    candles.forEach(function (c) { byTime[c.time] = c; });
    describe(candles[candles.length - 1]);
    chart.subscribeCrosshairMove(function (p) { describe(p && p.time && byTime[p.time] ? byTime[p.time] : candles[candles.length - 1]); });

    var averages = {};
    function range(months) {
      var end = future.length ? future[future.length - 1] : last;
      if (!months) { chart.timeScale().fitContent(); return; }
      var from = new Date(last + 'T00:00:00Z');
      from.setUTCMonth(from.getUTCMonth() - months);
      var start = iso(from);
      chart.timeScale().setVisibleRange({ from: start < candles[0].time ? candles[0].time : start, to: end });
    }
    box._tv = {
      range: range,
      log: function (on) { chart.priceScale('right').applyOptions({ mode: on ? LC.PriceScaleMode.Logarithmic : LC.PriceScaleMode.Normal }); },
      average: function (n, on) {
        if (on && !averages[n]) {
          averages[n] = chart.addLineSeries({ color: n === 50 ? '#2f6fb0' : '#8a4fb5', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, title: 'SMA ' + n });
          averages[n].setData(sma(candles, n));
        } else if (!on && averages[n]) { chart.removeSeries(averages[n]); delete averages[n]; }
      }
    };
    var on = box.querySelector('.tvw-rng.on');
    range(on ? +on.dataset.m : 6);
  }

  function show(box, view) {
    box.querySelectorAll('.tvw-btn').forEach(function (b) { b.classList.toggle('on', b.dataset.v === view); });
    box.querySelector('.tvw-svg').hidden = view !== 'svg';
    box.querySelector('.tvw-tv').hidden = view !== 'tv';
    if (view !== 'tv' || box._tv || box._tvLoading) return;
    var host = box.querySelector('.tvw-chart');
    box._tvLoading = true;
    host.textContent = 'Cargando velas…';
    Promise.all([lib(), fetch('/chart/candles?ticker=' + encodeURIComponent(box.dataset.ticker)).then(function (r) {
      if (!r.ok) throw new Error('datos');
      return r.json();
    })]).then(function (res) {
      box._tvLoading = false;
      if (!res[1].candles.length) { host.textContent = 'Sin velas guardadas de este ticker.'; return; }
      draw(box, res[1]);
    }).catch(function () { box._tvLoading = false; host.textContent = 'No se pudieron cargar las velas.'; });
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('.tvw-btn, .tvw-rng, .tvw-log');
    if (!t) return;
    var box = t.closest('.tvw');
    if (!box) return;
    if (t.classList.contains('tvw-btn')) { remember(t.dataset.v); show(box, t.dataset.v); return; }
    if (!box._tv) return;
    if (t.classList.contains('tvw-log')) {
      t.classList.toggle('on');
      t.setAttribute('aria-pressed', t.classList.contains('on'));
      box._tv.log(t.classList.contains('on'));
    } else {
      box.querySelectorAll('.tvw-rng').forEach(function (b) { b.classList.toggle('on', b === t); });
      box._tv.range(+t.dataset.m);
    }
  });
  document.addEventListener('change', function (e) {
    var t = e.target;
    if (!t.classList || !t.classList.contains('tvw-ma')) return;
    var box = t.closest('.tvw');
    if (box && box._tv) box._tv.average(+t.dataset.n, t.checked);
  });
  // quien prefirió las velas la última vez las ve directamente al abrir cualquier gráfico
  new MutationObserver(function (changes) {
    if (pref() !== 'tv') return;
    changes.forEach(function (c) {
      c.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        (n.matches && n.matches('.tvw') ? [n] : Array.prototype.slice.call(n.querySelectorAll ? n.querySelectorAll('.tvw') : [])).forEach(function (box) { show(box, 'tv'); });
      });
    });
  }).observe(document.body, { childList: true, subtree: true });
})();

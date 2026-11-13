// Vista de velas de los gráficos ampliados (TradingView Lightweight Charts, ver ui/tv_charts.py). Los gráficos llegan como fragmentos HTML
// (`.tvw`) que cada pantalla inserta en su capa: aquí se atiende el conmutador «Cierres / Velas» por delegación y se dibuja al pedirlo.
// Intervalos: «Diario» (velas guardadas, `time` = 'AAAA-MM-DD') o intradía 5 min / 15 min / 1 h (pedidas a tastytrade al pulsar; `time` =
// segundos ya desplazados a la hora de Nueva York, así que todo lo que se escribe de ellas se formatea en UTC).
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
    var intra = !!data.interval;
    var lines = JSON.parse(box.dataset.lines || '[]'), events = intra ? [] : JSON.parse(box.dataset.events || '[]');   // intradía: sin bandas de fechas
    var candles = data.candles.map(function (c) {        // pre y post-mercado: velas tenues
      return c.ext ? Object.assign({}, c, { color: (c.close >= c.open ? css('--green') : css('--red')) + '55', wickColor: (c.close >= c.open ? css('--green') : css('--red')) + '88' }) : c;
    }), last = candles[candles.length - 1].time, line = css('--line'), up = css('--green'), down = css('--red');
    host.textContent = '';
    var chart = LC.createChart(host, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: css('--ink'), fontSize: 11, fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      rightPriceScale: { borderColor: css('--line-strong'), scaleMargins: { top: 0.08, bottom: 0.24 } },
      timeScale: { borderColor: css('--line-strong'), rightOffset: 3, timeVisible: intra, secondsVisible: false },
      crosshair: { mode: LC.CrosshairMode.Normal },
      localization: { locale: 'es-ES', dateFormat: 'dd MMM \'yy' }
    });
    // bandas de fechas (vencimiento, resultados): una barra de altura completa en una escala propia y oculta, por debajo de las velas
    // y zonas (la de revisión: de su fecha al vencimiento), más tenues; una fecha sin sesión (festivo) se lleva a la sesión siguiente
    var until = events.reduce(function (m, e) { var t = e.end || e.time; return t > m ? t : m; }, last);
    var future = intra ? [] : futureDays(last, until);
    var times = candles.map(function (c) { return c.time; }).concat(future), shade = {};
    events.filter(function (e) { return e.end; }).forEach(function (e) {
      for (var i = times.length - 1; i >= 0 && times[i] >= e.time; i--) if (times[i] <= e.end) shade[times[i]] = e.color + '26';
    });
    events.filter(function (e) { return !e.end && e.time >= times[0]; }).forEach(function (e) {
      var at = times[times.length - 1];
      for (var i = times.length - 1; i >= 0 && times[i] >= e.time; i--) at = times[i];
      shade[at] = e.color + '66';
    });
    var bands = times.filter(function (t) { return shade[t]; });
    if (bands.length) {
      var ev = chart.addHistogramSeries({ priceScaleId: 'ev', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
      chart.priceScale('ev').applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
      ev.setData(bands.map(function (t) { return { time: t, value: 1, color: shade[t] }; }));
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
    // señales sobre una vela (apertura de la posición, último toque del strike): en la vela de ese día o, si no hubo sesión, en la anterior
    var signs = (intra ? [] : JSON.parse(box.dataset.markers || '[]')).map(function (m) {
      for (var i = candles.length - 1; i >= 0; i--) {
        if (candles[i].time <= m.time) return { time: candles[i].time, position: m.below ? 'belowBar' : 'aboveBar', color: m.color, shape: m.shape, text: m.title };
      }
      return null;
    }).filter(Boolean).sort(function (a, b) { return a.time < b.time ? -1 : (a.time > b.time ? 1 : 0); });
    if (signs.length) series.setMarkers(signs);
    var volAt = {};
    data.volume.forEach(function (v) { volAt[v.time] = v.value; });
    function describe(c) {
      if (!c || c.open == null) { legend.innerHTML = '&nbsp;'; return; }
      var chg = c.open ? (c.close - c.open) / c.open * 100 : 0, v = volAt[c.time];
      legend.textContent = (intra
        ? new Date(c.time * 1000).toLocaleString('es-ES', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' (NY)'
        : new Date(c.time + 'T00:00:00Z').toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }))
        + '   A ' + fmt(c.open) + '   Máx ' + fmt(c.high) + '   Mín ' + fmt(c.low) + '   C ' + fmt(c.close)
        + '   ' + (chg >= 0 ? '+' : '') + chg.toFixed(2).replace('.', ',') + ' %' + (v ? '   Vol ' + Math.round(v).toLocaleString('es-ES') : '');
    }
    var byTime = {};
    candles.forEach(function (c) { byTime[c.time] = c; });
    describe(candles[candles.length - 1]);
    chart.subscribeCrosshairMove(function (p) { describe(p && p.time && byTime[p.time] ? byTime[p.time] : candles[candles.length - 1]); });

    var averages = {};
    var sessions = [];
    if (intra) candles.forEach(function (c) { if (sessions[sessions.length - 1] !== c.session) sessions.push(c.session); });
    function range(months) {
      if (intra) {        // `months` = sesiones visibles (0 = todas)
        var first = months && months < sessions.length ? sessions[sessions.length - months] : sessions[0], at = 0;
        while (at < candles.length - 1 && candles[at].session < first) at++;
        chart.timeScale().setVisibleLogicalRange({ from: at - 0.5, to: candles.length - 1 + 3 });
        return;
      }
      var end = future.length ? future[future.length - 1] : last;
      if (!months) { chart.timeScale().fitContent(); return; }
      var from = new Date(last + 'T00:00:00Z');
      from.setUTCMonth(from.getUTCMonth() - months);
      var start = iso(from);
      chart.timeScale().setVisibleRange({ from: start < candles[0].time ? candles[0].time : start, to: end });
    }
    box._tv = {
      intra: intra,
      remove: function () { chart.remove(); },
      range: range,
      log: function (on) { chart.priceScale('right').applyOptions({ mode: on ? LC.PriceScaleMode.Logarithmic : LC.PriceScaleMode.Normal }); },
      average: function (n, on) {
        if (on && !averages[n]) {
          averages[n] = chart.addLineSeries({ color: n === 50 ? '#2f6fb0' : '#8a4fb5', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, title: 'SMA ' + n });
          averages[n].setData(sma(candles, n));
        } else if (!on && averages[n]) { chart.removeSeries(averages[n]); delete averages[n]; }
      }
    };
    var on = box.querySelector(intra ? '.tvw-irng.on' : '.tvw-rng.on:not(.tvw-irng)');
    range(on ? +(intra ? on.dataset.d : on.dataset.m) : (intra ? 1 : 6));
    // lo elegido antes de cambiar de intervalo se conserva: escala logarítmica y medias
    if (box.querySelector('.tvw-log.on')) box._tv.log(true);
    box.querySelectorAll('.tvw-ma').forEach(function (m) { if (m.checked) box._tv.average(+m.dataset.n, true); });
  }

  // carga y dibuja las velas del intervalo `iv` ('1d' o uno intradía). Las diarias se reutilizan; las intradía se piden de nuevo
  // en cada pulsación (el servidor las guarda 60 s), así volver a pulsar el intervalo activo las actualiza.
  function load(box, iv) {
    var host = box.querySelector('.tvw-chart'), cache = box._tvData || (box._tvData = {}), extBtn = box.querySelector('.tvw-ext');
    var ext = !!(extBtn && extBtn.classList.contains('on'));
    if (extBtn) extBtn.hidden = iv === '1d';
    box.querySelectorAll('.tvw-iv').forEach(function (b) { b.classList.toggle('on', b.dataset.i === iv); });
    box.querySelectorAll('.tvw-rng').forEach(function (b) { b.hidden = b.classList.contains('tvw-irng') === (iv === '1d'); });
    if (box._tv) { box._tv.remove(); box._tv = null; }
    box._tvWant = iv;
    var url = iv === '1d' ? '/chart/candles?ticker=' + encodeURIComponent(box.dataset.ticker)
                          : '/chart/candles/intraday?ticker=' + encodeURIComponent(box.dataset.ticker) + '&interval=' + encodeURIComponent(iv) + (ext ? '&ext=1' : '');
    host.textContent = iv === '1d' ? 'Cargando velas…' : 'Pidiendo velas intradía a tastytrade…';
    box._tvLoading = true;
    Promise.all([lib(), cache[iv] ? Promise.resolve(cache[iv]) : fetch(url).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || 'datos'); return j; });
    })]).then(function (res) {
      if (box._tvWant !== iv) return;         // se eligió otro intervalo mientras cargaba
      box._tvLoading = false;
      if (!res[1].candles.length) { host.textContent = iv === '1d' ? 'Sin velas guardadas de este ticker.' : 'tastytrade no devolvió velas intradía de este ticker.'; return; }
      if (iv === '1d') cache[iv] = res[1];
      host.textContent = '';
      draw(box, res[1]);
    }).catch(function (err) {
      if (box._tvWant !== iv) return;
      box._tvLoading = false;
      host.textContent = 'No se pudieron cargar las velas' + (iv === '1d' ? '.' : ' intradía: ' + err.message);
    });
  }

  function show(box, view) {
    box.querySelectorAll('.tvw-btn').forEach(function (b) { b.classList.toggle('on', b.dataset.v === view); });
    box.querySelector('.tvw-svg').hidden = view !== 'svg';
    box.querySelector('.tvw-tv').hidden = view !== 'tv';
    if (view !== 'tv' || box._tv || box._tvLoading) return;
    load(box, '1d');
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('.tvw-btn, .tvw-rng, .tvw-log, .tvw-iv, .tvw-ext');
    if (!t) return;
    var box = t.closest('.tvw');
    if (!box) return;
    if (t.classList.contains('tvw-btn')) { remember(t.dataset.v); show(box, t.dataset.v); return; }
    if (t.classList.contains('tvw-iv')) { load(box, t.dataset.i); return; }
    if (t.classList.contains('tvw-ext')) {
      t.classList.toggle('on');
      t.setAttribute('aria-pressed', t.classList.contains('on'));
      var cur = box.querySelector('.tvw-iv.on');
      if (cur && cur.dataset.i !== '1d') load(box, cur.dataset.i);
      return;
    }
    if (!box._tv) return;
    if (t.classList.contains('tvw-log')) {
      t.classList.toggle('on');
      t.setAttribute('aria-pressed', t.classList.contains('on'));
      box._tv.log(t.classList.contains('on'));
    } else {
      var intra = t.classList.contains('tvw-irng');
      box.querySelectorAll(intra ? '.tvw-irng' : '.tvw-rng:not(.tvw-irng)').forEach(function (b) { b.classList.toggle('on', b === t); });
      if (box._tv.intra === intra) box._tv.range(+(intra ? t.dataset.d : t.dataset.m));
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

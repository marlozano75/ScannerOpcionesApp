// Vista de velas de los gráficos ampliados (TradingView Lightweight Charts, ver ui/tv_charts.py). Los gráficos llegan como fragmentos HTML
// (`.tvw`) que cada pantalla inserta en su capa: aquí se atiende el conmutador «Cierres / Velas» por delegación y se dibuja al pedirlo.
// Intervalos: «Diario» (velas guardadas, `time` = 'AAAA-MM-DD'), «Semanal» (las diarias agrupadas por semana; `time` = su lunes) o intradía 5 min / 15 min / 1 h (pedidas a tastytrade al pulsar; `time` =
// segundos ya desplazados a la hora de Nueva York, así que todo lo que se escribe de ellas se formatea en UTC).
(function () {
  var libLoading = null, PREF = 'chartView';
  var AXIS_WIDTH = 72;      // ancho mínimo del eje de precios: el mismo en las velas y en los paneles de RSI/MACD para que el tiempo coincida
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
  var FUTURE_MAX_DAYS = 60;   // el eje solo se alarga hasta fechas a ≤ 60 días: un vencimiento más lejano no encoge las velas (su fecha sigue en el pie)
  function addDays(t, n) { var d = new Date(t + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return iso(d); }
  function monday(t) { var d = new Date(t + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7)); return iso(d); }
  function futureWeeks(last, until) {
    var out = [], t = last;
    for (var n = 0; n < 120; n++) { t = addDays(t, 7); if (t > until) break; out.push(t); }
    return out;
  }
  // velas semanales a partir de las diarias: una por semana (fecha = su lunes), con el volumen sumado
  function toWeekly(daily) {
    var candles = [], volume = [], volAt = {};
    daily.volume.forEach(function (v) { volAt[v.time] = v.value; });
    daily.candles.forEach(function (c) {
      var w = monday(c.time), cur = candles[candles.length - 1];
      if (!cur || cur.time !== w) {
        candles.push({ time: w, open: c.open, high: c.high, low: c.low, close: c.close });
        volume.push({ time: w, value: 0 });
      } else {
        cur.high = Math.max(cur.high, c.high); cur.low = Math.min(cur.low, c.low); cur.close = c.close;
      }
      volume[volume.length - 1].value += volAt[c.time] || 0;
    });
    volume = volume.filter(function (v, i) { v.up = candles[i].close >= candles[i].open; return v.value > 0; });
    return { ticker: daily.ticker, weekly: true, candles: candles, volume: volume, supports: daily.supports };
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

  // RSI de Wilder (`n` = 14): un valor por vela, null hasta tener `n` cambios
  function rsi(closes, n) {
    var out = closes.map(function () { return null; }), gain = 0, loss = 0;
    for (var i = 1; i < closes.length; i++) {
      var d = closes[i] - closes[i - 1], g = d > 0 ? d : 0, l = d < 0 ? -d : 0;
      if (i <= n) { gain += g; loss += l; if (i === n) { gain /= n; loss /= n; } else continue; }
      else { gain = (gain * (n - 1) + g) / n; loss = (loss * (n - 1) + l) / n; }
      out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
    }
    return out;
  }
  // media exponencial sobre los valores no nulos (empieza con la media simple de los `n` primeros)
  function ema(values, n) {
    var out = values.map(function () { return null; }), k = 2 / (n + 1), prev = null, seed = [];
    values.forEach(function (v, i) {
      if (v == null) return;
      if (prev == null) { seed.push(v); if (seed.length === n) { prev = seed.reduce(function (a, b) { return a + b; }, 0) / n; out[i] = prev; } return; }
      prev = v * k + prev * (1 - k);
      out[i] = prev;
    });
    return out;
  }
  // MACD 12/26/9: línea, señal e histograma
  function macd(closes) {
    var fast = ema(closes, 12), slow = ema(closes, 26);
    var line = closes.map(function (_, i) { return fast[i] != null && slow[i] != null ? fast[i] - slow[i] : null; });
    var signal = ema(line, 9);
    return { line: line, signal: signal, hist: line.map(function (v, i) { return v != null && signal[i] != null ? v - signal[i] : null; }) };
  }
  function fmt2(v) { return v == null ? '—' : v.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

  function draw(box, data) {
    var LC = window.LightweightCharts, host = box.querySelector('.tvw-chart'), legend = box.querySelector('.tvw-legend');
    var intra = !!data.interval, weekly = !!data.weekly;
    // soportes validados: vienen con las velas diarias (en semanal e intradía, los de las diarias ya cargadas)
    var supports = data.supports || ((box._tvData || {})['1d'] || {}).supports || [];
    var lines = JSON.parse(box.dataset.lines || '[]'), events = intra ? [] : JSON.parse(box.dataset.events || '[]');   // intradía: sin bandas de fechas
    var candles = data.candles.map(function (c) {        // pre y post-mercado: velas tenues
      return c.ext ? Object.assign({}, c, { color: (c.close >= c.open ? css('--green') : css('--red')) + '55', wickColor: (c.close >= c.open ? css('--green') : css('--red')) + '88' }) : c;
    }), last = candles[candles.length - 1].time, line = css('--line'), up = css('--green'), down = css('--red');
    // fechas a más de FUTURE_MAX_DAYS (un vencimiento lejano, p. ej. un CDS de 300 días): no se dibujan para no encoger las velas;
    // una zona que empieza antes se recorta; en semanal cada fecha va a la vela de su semana
    if (!intra) {
      var horizon = addDays(last, FUTURE_MAX_DAYS);
      events = events.filter(function (e) { return e.time <= horizon; })
        .map(function (e) { return e.end && e.end > horizon ? Object.assign({}, e, { end: horizon }) : e; });
      if (weekly) events = events.map(function (e) { return Object.assign({}, e, { time: monday(e.time) }, e.end ? { end: monday(e.end) } : {}); });
    }
    host.textContent = '';
    var chart = LC.createChart(host, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: css('--ink'), fontSize: 11, fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      rightPriceScale: { borderColor: css('--line-strong'), scaleMargins: { top: 0.08, bottom: 0.24 }, minimumWidth: AXIS_WIDTH },
      timeScale: { borderColor: css('--line-strong'), rightOffset: 3, timeVisible: intra, secondsVisible: false },
      crosshair: { mode: LC.CrosshairMode.Normal },
      localization: { locale: 'es-ES', dateFormat: 'dd MMM \'yy' }
    });
    // bandas de fechas (vencimiento, resultados): una barra de altura completa en una escala propia y oculta, por debajo de las velas
    // y zonas (la de revisión: de su fecha al vencimiento), más tenues; una fecha sin sesión (festivo) se lleva a la sesión siguiente
    var until = events.reduce(function (m, e) { var t = e.end || e.time; return t > m ? t : m; }, last);
    var future = intra ? [] : (weekly ? futureWeeks(last, until) : futureDays(last, until));
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
    var prices = lines.concat(supports).map(function (l) { return l.price; });
    var series = chart.addCandlestickSeries({
      upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down,
      // la escala automática se ajusta a las velas visibles (se puede ampliar una zona); con «Niveles» incluye además los strikes,
      // el breakeven y el objetivo, que suelen quedar lejos del precio
      autoscaleInfoProvider: function (original) {
        var res = original();
        if (box._fitLines && res && res.priceRange && prices.length) {
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
    supports.forEach(function (l) {
      series.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: LC.LineStyle.Dotted, axisLabelVisible: true, title: l.title });
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
        : (weekly ? 'Semana del ' : '') + new Date(c.time + 'T00:00:00Z').toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }))
        + '   A ' + fmt(c.open) + '   Máx ' + fmt(c.high) + '   Mín ' + fmt(c.low) + '   C ' + fmt(c.close)
        + '   ' + (chg >= 0 ? '+' : '') + chg.toFixed(2).replace('.', ',') + ' %' + (v ? '   Vol ' + Math.round(v).toLocaleString('es-ES') : '')
        + indicatorText(c.time);
    }
    var byTime = {}, indexOf = {};
    candles.forEach(function (c, i) { byTime[c.time] = c; indexOf[c.time] = i; });

    // RSI y MACD: cada uno en un gráfico propio debajo de las velas (la versión 4 de la librería no tiene paneles), con el mismo eje de
    // tiempo: los puntos son los mismos (velas + días futuros en blanco) y el rango visible y la cruz se copian de uno a otro
    var closes = candles.map(function (c) { return c.close; }), panes = {}, syncing = false, calc = {};
    function pts(values) {
      return times.map(function (t, i) { return i < values.length && values[i] != null ? { time: t, value: values[i] } : { time: t }; });
    }
    function indicatorText(t) {
      var i = indexOf[t], out = '';
      if (i == null) return out;
      if (panes.rsi) out += '   RSI ' + (calc.rsi[i] == null ? '—' : calc.rsi[i].toFixed(1).replace('.', ','));
      if (panes.macd) out += '   MACD ' + fmt2(calc.macd.line[i]) + ' · señal ' + fmt2(calc.macd.signal[i]) + ' · hist. ' + fmt2(calc.macd.hist[i]);
      return out;
    }
    function follow(from, to) {
      from.timeScale().subscribeVisibleLogicalRangeChange(function (r) {
        if (!r || syncing) return;
        syncing = true;
        to.forEach(function (c) { c.timeScale().setVisibleLogicalRange(r); });
        syncing = false;
      });
    }
    function others(except) {
      return [chart].concat(Object.keys(panes).map(function (k) { return panes[k].chart; })).filter(function (c) { return c !== except; });
    }
    follow(chart, { forEach: function (f) { others(chart).forEach(f); } });
    function addPane(k) {
      var el = box.querySelector('.tvw-sub[data-k="' + k + '"]');
      if (!el || panes[k]) return;
      el.hidden = false;
      el.textContent = '';
      var c = LC.createChart(el, {
        autoSize: true,
        layout: { background: { color: 'transparent' }, textColor: css('--ink'), fontSize: 11, fontFamily: getComputedStyle(document.body).fontFamily },
        grid: { vertLines: { color: line }, horzLines: { color: line } },
        rightPriceScale: { borderColor: css('--line-strong'), minimumWidth: AXIS_WIDTH, scaleMargins: { top: 0.1, bottom: 0.08 } },
        timeScale: { visible: false, rightOffset: 3 },
        crosshair: { mode: LC.CrosshairMode.Normal },
        localization: { locale: 'es-ES', dateFormat: 'dd MMM \'yy' }
      });
      var main;
      if (k === 'rsi') {
        calc.rsi = calc.rsi || rsi(closes, 14);
        main = c.addLineSeries({ color: '#8a4fb5', lineWidth: 2, priceLineVisible: false, title: 'RSI 14',
                                 autoscaleInfoProvider: function () { return { priceRange: { minValue: 0, maxValue: 100 } }; } });
        main.setData(pts(calc.rsi));
        [70, 30].forEach(function (lv) { main.createPriceLine({ price: lv, color: css('--mute'), lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: '' }); });
      } else {
        calc.macd = calc.macd || macd(closes);
        var hist = c.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
        hist.setData(times.map(function (t, i) {
          var v = i < calc.macd.hist.length ? calc.macd.hist[i] : null;
          return v == null ? { time: t } : { time: t, value: v, color: (v >= 0 ? up : down) + '88' };
        }));
        main = c.addLineSeries({ color: '#2f6fb0', lineWidth: 2, priceLineVisible: false, title: 'MACD 12/26/9' });
        main.setData(pts(calc.macd.line));
        var signal = c.addLineSeries({ color: '#d9822b', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        signal.setData(pts(calc.macd.signal));
      }
      panes[k] = { chart: c, series: main, values: k === 'rsi' ? calc.rsi : calc.macd.line };
      var r = chart.timeScale().getVisibleLogicalRange();
      if (r) c.timeScale().setVisibleLogicalRange(r);
      follow(c, { forEach: function (f) { others(c).forEach(f); } });
      // la cruz de un panel se lleva a las velas (y de ahí a los demás paneles)
      c.subscribeCrosshairMove(function (p) {
        if (syncing) return;
        syncing = true;
        if (p && p.time != null && byTime[p.time]) chart.setCrosshairPosition(byTime[p.time].close, p.time, series);
        else chart.clearCrosshairPosition();
        crossTo(p && p.time, c);
        syncing = false;
        describe(p && p.time != null && byTime[p.time] ? byTime[p.time] : candles[candles.length - 1]);
      });
    }
    function removePane(k) {
      if (!panes[k]) return;
      panes[k].chart.remove();
      delete panes[k];
      var el = box.querySelector('.tvw-sub[data-k="' + k + '"]');
      if (el) { el.hidden = true; el.textContent = ''; }
    }
    function crossTo(t, except) {
      Object.keys(panes).forEach(function (k) {
        var p = panes[k];
        if (p.chart === except) return;
        var i = t != null ? indexOf[t] : null;
        if (i != null && p.values[i] != null) p.chart.setCrosshairPosition(p.values[i], t, p.series);
        else p.chart.clearCrosshairPosition();
      });
    }

    describe(candles[candles.length - 1]);
    chart.subscribeCrosshairMove(function (p) {
      describe(p && p.time && byTime[p.time] ? byTime[p.time] : candles[candles.length - 1]);
      if (syncing) return;
      syncing = true;
      crossTo(p && p.time, chart);
      syncing = false;
    });

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
      remove: function () { Object.keys(panes).forEach(removePane); chart.remove(); },
      indicator: function (k, on) { if (on) addPane(k); else removePane(k); describe(candles[candles.length - 1]); },
      range: range,
      autoscale: function () { chart.priceScale('right').applyOptions({ autoScale: true }); },
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
    box.querySelectorAll('.tvw-ind').forEach(function (m) { if (m.checked) box._tv.indicator(m.dataset.k, true); });
  }

  // carga y dibuja las velas del intervalo `iv` ('1d' o uno intradía). Las diarias se reutilizan; las intradía se piden de nuevo
  // en cada pulsación (el servidor las guarda 60 s), así volver a pulsar el intervalo activo las actualiza.
  function load(box, iv) {
    var host = box.querySelector('.tvw-chart'), cache = box._tvData || (box._tvData = {}), extBtn = box.querySelector('.tvw-ext');
    var ext = !!(extBtn && extBtn.classList.contains('on'));
    var need = iv === '1d' || iv === '1w' ? '1d' : iv;          // el semanal sale de las velas diarias
    if (extBtn) extBtn.hidden = need === '1d';
    box.querySelectorAll('.tvw-iv').forEach(function (b) { b.classList.toggle('on', b.dataset.i === iv); });
    box.querySelectorAll('.tvw-rng').forEach(function (b) { b.hidden = b.classList.contains('tvw-irng') === (need === '1d'); });
    if (box._tv) { box._tv.remove(); box._tv = null; }
    box._tvWant = iv;
    var url = need === '1d' ? '/chart/candles?ticker=' + encodeURIComponent(box.dataset.ticker)
                          : '/chart/candles/intraday?ticker=' + encodeURIComponent(box.dataset.ticker) + '&interval=' + encodeURIComponent(iv) + (ext ? '&ext=1' : '');
    host.textContent = need === '1d' ? 'Cargando velas…' : 'Pidiendo velas intradía a tastytrade…';
    box._tvLoading = true;
    Promise.all([lib(), cache[need] ? Promise.resolve(cache[need]) : fetch(url).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || 'datos'); return j; });
    })]).then(function (res) {
      if (box._tvWant !== iv) return;         // se eligió otro intervalo mientras cargaba
      box._tvLoading = false;
      if (!res[1].candles.length) { host.textContent = need === '1d' ? 'Sin velas guardadas de este ticker.' : 'tastytrade no devolvió velas intradía de este ticker.'; return; }
      if (need === '1d') cache['1d'] = res[1];
      host.textContent = '';
      draw(box, iv === '1w' ? toWeekly(res[1]) : res[1]);
    }).catch(function (err) {
      if (box._tvWant !== iv) return;
      box._tvLoading = false;
      host.textContent = 'No se pudieron cargar las velas' + (need === '1d' ? '.' : ' intradía: ' + err.message);
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
    var t = e.target.closest && e.target.closest('.tvw-btn, .tvw-rng, .tvw-log, .tvw-iv, .tvw-ext, .tvw-fit, .tvw-reset');
    if (!t) return;
    var box = t.closest('.tvw');
    if (!box) return;
    if (t.classList.contains('tvw-btn')) { remember(t.dataset.v); show(box, t.dataset.v); return; }
    if (t.classList.contains('tvw-iv')) { load(box, t.dataset.i); return; }
    if (t.classList.contains('tvw-ext')) {
      t.classList.toggle('on');
      t.setAttribute('aria-pressed', t.classList.contains('on'));
      var cur = box.querySelector('.tvw-iv.on');
      if (cur && cur.dataset.i !== '1d' && cur.dataset.i !== '1w') load(box, cur.dataset.i);
      return;
    }
    if (!box._tv) return;
    if (t.classList.contains('tvw-fit')) {
      t.classList.toggle('on');
      t.setAttribute('aria-pressed', t.classList.contains('on'));
      box._fitLines = t.classList.contains('on');
      box._tv.autoscale();
    } else if (t.classList.contains('tvw-reset')) {
      box._tv.autoscale();              // tras estirar el eje de precios a mano (desactiva la escala automática)
    } else if (t.classList.contains('tvw-log')) {
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
    if (!t.classList || !(t.classList.contains('tvw-ma') || t.classList.contains('tvw-ind'))) return;
    var box = t.closest('.tvw');
    if (!box || !box._tv) return;
    if (t.classList.contains('tvw-ind')) box._tv.indicator(t.dataset.k, t.checked);
    else box._tv.average(+t.dataset.n, t.checked);
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

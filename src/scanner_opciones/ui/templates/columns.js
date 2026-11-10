// Mostrar/ocultar columnas de las tablas con data-colmanager (salidas de los scanners), por grupos o una a una.
// Cliente puro: oculta las celdas con la clase col-off. La elección se recuerda en localStorage por pantalla
// y modo (?mode=). Ticker, la casilla de selección y las columnas sin título no se pueden ocultar.
(function (root) {
  'use strict';

  // Grupo de una columna según su título (el primero que encaja; el resto = técnicos y fundamentales).
  var GROUPS = [
    ['Riesgo y cartera', /riesgo|p[eé]rd|precio neto|^dist|brev|cart\.|^\+sector|semana|peso|capital/],
    ['Rentabilidad', /yield|cr[eé]d|d[eé]bito|retorno|r\/r|cred\/a|puntuaci|cobro|ganancia|prima/],
    ['Liquidez', /^oi( |$)|inter[eé]s|bid|ask|^vol\.|^volumen|spread|liq/],
    ['Volatilidad y griegas', /^δ|^delta|^iv( |$)/],
    ['Contrato', /^(ticker|sector|precio|strike|venc|historial|desc|dte|tipo|estrategias|ancho|otm|comp|vend)/]
  ];
  var DEFAULT_GROUP = 'Técnicos y fundamentales';

  function groupOf(label) {
    var l = (label || '').trim().toLowerCase();
    for (var i = 0; i < GROUPS.length; i++) if (GROUPS[i][1].test(l)) return GROUPS[i][0];
    return DEFAULT_GROUP;
  }

  function label(th) { return th.textContent.trim().replace(/\s+/g, ' '); }

  function lockedTh(th) {
    var t = label(th);
    return t === '' || th.classList.contains('sel') || th.classList.contains('tk') || t === 'Cant.';
  }

  function storageKey() {
    var mode = '';
    try { mode = new URLSearchParams(location.search).get('mode') || ''; } catch (e) { /* ignorar */ }
    return 'cols:' + location.pathname + ':' + mode;
  }

  function load(key) {
    try { var v = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }
  function save(key, arr) { try { localStorage.setItem(key, JSON.stringify(arr)); } catch (e) { /* sin almacenamiento */ } }

  function init(table) {
    var header = table.querySelector('tr');
    if (!header) return;
    var ths = Array.prototype.slice.call(header.children);
    var key = storageKey();
    var hidden = {};
    load(key).forEach(function (l) { hidden[l] = true; });

    var cols = [];   // {i, label, group}
    ths.forEach(function (th, i) { if (!lockedTh(th)) cols.push({ i: i, label: label(th), group: groupOf(label(th)) }); });

    function apply() {
      var n = header.children.length;
      Array.prototype.slice.call(table.querySelectorAll('tr')).forEach(function (tr) {
        if (tr.children.length !== n) return;   // mensajes «sin resultados»
        cols.forEach(function (c) { tr.children[c.i].classList.toggle('col-off', !!hidden[c.label]); });
      });
      var off = cols.filter(function (c) { return hidden[c.label]; }).length;
      btn.textContent = 'Columnas' + (off ? ' (' + off + ' ocultas)' : '');
    }
    function persist() { save(key, Object.keys(hidden).filter(function (l) { return hidden[l]; })); apply(); }

    // Barra con el botón y el panel; fuera de la tabla, antes de su contenedor con desplazamiento.
    var host = table.closest('.scroll') || table;
    var old = host.parentNode.querySelectorAll(':scope > .colmgr');
    Array.prototype.forEach.call(old, function (o) { o.remove(); });
    var bar = document.createElement('div');
    bar.className = 'colmgr';
    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'secondary colmgr-btn'; btn.setAttribute('aria-expanded', 'false');
    var panel = document.createElement('div');
    panel.className = 'colmgr-panel'; panel.hidden = true;

    var order = [];
    cols.forEach(function (c) { if (order.indexOf(c.group) < 0) order.push(c.group); });
    var boxes = [];   // para refrescar el estado de las casillas
    var actions = document.createElement('div');
    actions.className = 'colmgr-actions';
    [['Mostrar todas', function () { hidden = {}; }],
     ['Ocultar todas', function () { cols.forEach(function (c) { hidden[c.label] = true; }); }]].forEach(function (a) {
      var b = document.createElement('button'); b.type = 'button'; b.className = 'secondary'; b.textContent = a[0];
      b.addEventListener('click', function () { a[1](); persist(); sync(); });
      actions.appendChild(b);
    });
    panel.appendChild(actions);

    order.forEach(function (g) {
      var members = cols.filter(function (c) { return c.group === g; });
      var det = document.createElement('details'); det.open = true; det.className = 'colmgr-grp';
      var sum = document.createElement('summary');
      var gbox = document.createElement('input'); gbox.type = 'checkbox';
      gbox.title = 'Mostrar u ocultar todo el grupo';
      gbox.addEventListener('click', function (e) { e.stopPropagation(); });
      gbox.addEventListener('change', function () {
        members.forEach(function (c) { if (gbox.checked) delete hidden[c.label]; else hidden[c.label] = true; });
        persist(); sync();
      });
      sum.appendChild(gbox); sum.appendChild(document.createTextNode(' ' + g));
      det.appendChild(sum);
      var list = document.createElement('div'); list.className = 'colmgr-list';
      members.forEach(function (c) {
        var lb = document.createElement('label');
        var cb = document.createElement('input'); cb.type = 'checkbox';
        cb.addEventListener('change', function () { if (cb.checked) delete hidden[c.label]; else hidden[c.label] = true; persist(); sync(); });
        lb.appendChild(cb); lb.appendChild(document.createTextNode(' ' + c.label));
        list.appendChild(lb);
        boxes.push({ cb: cb, c: c });
      });
      det.appendChild(list);
      panel.appendChild(det);
      boxes.push({ gbox: gbox, members: members });
    });

    function sync() {
      boxes.forEach(function (b) {
        if (b.cb) { b.cb.checked = !hidden[b.c.label]; return; }
        var shown = b.members.filter(function (c) { return !hidden[c.label]; }).length;
        b.gbox.checked = shown === b.members.length;
        b.gbox.indeterminate = shown > 0 && shown < b.members.length;
      });
    }

    btn.addEventListener('click', function () {
      panel.hidden = !panel.hidden;
      btn.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
    });
    document.addEventListener('click', function (e) {
      if (!panel.hidden && !bar.contains(e.target)) { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); }
    });

    bar.appendChild(btn); bar.appendChild(panel);
    host.parentNode.insertBefore(bar, host);
    sync(); apply();
  }

  var api = { groupOf: groupOf, init: init };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ColumnManager = api;
})(typeof window !== 'undefined' ? window : this);

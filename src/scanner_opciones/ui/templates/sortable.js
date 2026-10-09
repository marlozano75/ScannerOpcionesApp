// Ordenación de tablas al pulsar en el título de la columna (cliente, sin recargar).
// Primer clic: números de mayor a menor / texto de A a Z; segundo clic: al revés.
// Los valores vacíos ("—") quedan siempre al final. El estado se recuerda en la sesión.
(function (root) {
  'use strict';

  // Texto con el que se ordena una celda: su atributo data-sort si lo tiene (p. ej. «$4.4B» -> 4400000000).
  function cellText(c) {
    return (c.dataset && c.dataset.sort !== undefined) ? c.dataset.sort : c.textContent;
  }

  // "12.5%" -> 12.5, "1,234" -> 1234, "0.42x" -> 0.42, "—" -> vacío, resto -> texto
  function parseValue(text) {
    var t = (text || '').trim();
    if (t === '' || t === '—' || t === '-') return { empty: true };
    var n = t.replace(/[%x,\s]/g, '');
    if (/^[+-]?\d+(\.\d+)?$/.test(n)) return { num: parseFloat(n) };
    return { str: t.toLowerCase() };
  }

  // Comparación ascendente entre dos valores no vacíos: números antes que texto.
  function compare(a, b) {
    var an = a.num !== undefined, bn = b.num !== undefined;
    if (an && bn) return a.num - b.num;
    if (an) return -1;
    if (bn) return 1;
    return a.str < b.str ? -1 : (a.str > b.str ? 1 : 0);
  }

  // ¿La columna es numérica? (todos los valores no vacíos son números)
  function isNumericColumn(values) {
    var seen = false;
    for (var i = 0; i < values.length; i++) {
      if (values[i].empty) continue;
      if (values[i].num === undefined) return false;
      seen = true;
    }
    return seen;
  }

  // Ordena las filas por la columna `col`; dir = 'asc' | 'desc'. Los vacíos van siempre al final.
  function sortRows(rows, col, dir) {
    var items = rows.map(function (row, i) {
      return { row: row, i: i, v: parseValue(cellText(row.children[col])) };
    });
    var sign = dir === 'desc' ? -1 : 1;
    items.sort(function (x, y) {
      if (x.v.empty && y.v.empty) return x.i - y.i;
      if (x.v.empty) return 1;
      if (y.v.empty) return -1;
      var c = compare(x.v, y.v) * sign;
      return c !== 0 ? c : x.i - y.i;   // estable
    });
    return items.map(function (it) { return it.row; });
  }

  // Reordena en el DOM las filas de datos de `table` (la primera fila es la cabecera).
  // Las filas con distinto nº de celdas que la cabecera (mensajes «sin resultados») no se tocan.
  function sortTable(table, col, dir) {
    var all = Array.prototype.slice.call(table.querySelectorAll('tr'));
    var header = all[0];
    var rows = all.slice(1).filter(function (r) { return r.children.length === header.children.length; });
    var others = all.slice(1).filter(function (r) { return r.children.length !== header.children.length; });
    var sorted = sortRows(rows, col, dir);
    sorted.forEach(function (r) { r.parentNode.appendChild(r); });
    others.forEach(function (r) { r.parentNode.appendChild(r); });   // los mensajes se quedan al final
    return sorted;
  }

  function defaultDir(table, col) {
    var all = Array.prototype.slice.call(table.querySelectorAll('tr'));
    var vals = all.slice(1).filter(function (r) { return r.children.length === all[0].children.length; })
      .map(function (r) { return parseValue(cellText(r.children[col])); });
    return isNumericColumn(vals) ? 'desc' : 'asc';
  }

  function init(table, index) {
    var header = table.querySelector('tr');
    if (!header) return;
    var ths = Array.prototype.slice.call(header.children);
    var key = 'sort:' + location.pathname + ':' + (table.id || index);
    var current = null;

    function apply(col, dir) {
      sortTable(table, col, dir);
      ths.forEach(function (th, i) {
        if (i === col) th.setAttribute('aria-sort', dir === 'desc' ? 'descending' : 'ascending');
        else th.removeAttribute('aria-sort');
      });
      current = { col: col, dir: dir, label: ths[col].textContent.trim() };
      try { sessionStorage.setItem(key, JSON.stringify(current)); } catch (e) { /* sin almacenamiento */ }
    }

    ths.forEach(function (th, col) {
      if (th.hasAttribute('data-nosort') || th.textContent.trim() === '') return;   // sin título: no ordena
      th.classList.add('sortable-th');
      th.tabIndex = 0;
      th.title = 'Pulsa para ordenar';
      function toggle() {
        var dir = current && current.col === col
          ? (current.dir === 'desc' ? 'asc' : 'desc')
          : defaultDir(table, col);
        apply(col, dir);
      }
      th.addEventListener('click', toggle);
      th.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    });

    var restored = false;
    try {   // recupera el orden elegido (p. ej. tras volver a escanear); se ignora si la columna ha cambiado de sitio
      var saved = JSON.parse(sessionStorage.getItem(key) || 'null');
      var th = saved && ths[saved.col];
      if (th && th.classList.contains('sortable-th') && (!saved.label || saved.label === th.textContent.trim())) {
        apply(saved.col, saved.dir);
        restored = true;
      }
    } catch (e) { /* ignorar */ }
    if (!restored) {   // sin orden elegido: el de la columna marcada con data-sort-default="asc|desc"
      ths.forEach(function (th, col) {
        var dir = th.getAttribute('data-sort-default');
        if (dir && !current && th.classList.contains('sortable-th')) apply(col, dir === 'asc' ? 'asc' : 'desc');
      });
    }
  }

  var api = { cellText: cellText, parseValue: parseValue, compare: compare, isNumericColumn: isNumericColumn,
              sortRows: sortRows, sortTable: sortTable, init: init };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SortableTables = api;
})(typeof window !== 'undefined' ? window : this);

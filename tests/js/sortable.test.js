// Prueba de la lógica de ordenación con un DOM mínimo simulado. Se ejecuta con: node sortable.test.js <ruta a sortable.js>
const assert = require('assert');
const path = require('path');
const S = require(path.resolve(process.argv[2]));

function cell(text) { return { textContent: text }; }
function makeTable(header, data) {
  const tbody = { children: [], appendChild(r) { this.children = this.children.filter(x => x !== r); this.children.push(r); r.parentNode = this; } };
  const mk = (cells) => ({ children: cells.map(cell), parentNode: tbody });
  const rows = [mk(header), ...data.map(mk)];
  tbody.children = rows.slice();
  return { tbody, rows, querySelectorAll: () => tbody.children.slice() };
}
const order = (t, col = 0) => t.tbody.children.slice(1).map(r => r.children[col].textContent);

// --- parseValue
assert.deepStrictEqual(S.parseValue('12.5%'), { num: 12.5 });
assert.deepStrictEqual(S.parseValue('1,234'), { num: 1234 });
assert.deepStrictEqual(S.parseValue('0.42x'), { num: 0.42 });
assert.deepStrictEqual(S.parseValue('-3.5%'), { num: -3.5 });
assert.deepStrictEqual(S.parseValue('—'), { empty: true });
assert.deepStrictEqual(S.parseValue('  '), { empty: true });
assert.deepStrictEqual(S.parseValue('Technology'), { str: 'technology' });
assert.deepStrictEqual(S.parseValue('2026-10-30'), { str: '2026-10-30' });   // fechas: texto ISO, ordena bien como texto

// --- números de mayor a menor y al revés; vacíos siempre al final
let t = makeTable(['Ticker', 'Yield'], [['A', '1.5%'], ['B', '—'], ['C', '12.0%'], ['D', '3.2%'], ['E', '']]);
S.sortTable(t, 1, 'desc');
assert.deepStrictEqual(order(t), ['C', 'D', 'A', 'B', 'E']);
S.sortTable(t, 1, 'asc');
assert.deepStrictEqual(order(t), ['A', 'D', 'C', 'B', 'E']);     // los vacíos siguen al final

// --- comparación numérica, no de texto ("9" < "10")
t = makeTable(['Ticker', 'DTE'], [['A', '9'], ['B', '10'], ['C', '100'], ['D', '25']]);
S.sortTable(t, 1, 'asc');
assert.deepStrictEqual(order(t), ['A', 'B', 'D', 'C']);

// --- separador de miles
t = makeTable(['Ticker', 'Margen'], [['A', '1,200'], ['B', '950'], ['C', '10,000']]);
S.sortTable(t, 1, 'desc');
assert.deepStrictEqual(order(t), ['C', 'A', 'B']);

// --- negativos y ceros
t = makeTable(['Ticker', 'Delta'], [['A', '-0.10'], ['B', '0.00'], ['C', '-0.50'], ['D', '0.20']]);
S.sortTable(t, 1, 'asc');
assert.deepStrictEqual(order(t), ['C', 'A', 'B', 'D']);

// --- texto A-Z / Z-A sin distinguir mayúsculas
t = makeTable(['Ticker', 'Sector'], [['A', 'utilities'], ['B', 'Technology'], ['C', 'Health'], ['D', '—']]);
S.sortTable(t, 1, 'asc');
assert.deepStrictEqual(order(t, 1), ['Health', 'Technology', 'utilities', '—']);
S.sortTable(t, 1, 'desc');
assert.deepStrictEqual(order(t, 1), ['utilities', 'Technology', 'Health', '—']);

// --- fechas/horas ISO como texto
t = makeTable(['Ticker', 'Vence'], [['A', '2026-10-30'], ['B', '2026-10-02'], ['C', '2026-11-20']]);
S.sortTable(t, 1, 'asc');
assert.deepStrictEqual(order(t), ['B', 'A', 'C']);

// --- estabilidad: iguales conservan el orden previo
t = makeTable(['Ticker', 'Yield'], [['A', '2%'], ['B', '2%'], ['C', '2%']]);
S.sortTable(t, 1, 'desc');
assert.deepStrictEqual(order(t), ['A', 'B', 'C']);

// --- la fila de cabecera no se mueve; una fila de mensaje (distinto nº de celdas) tampoco se toca
t = makeTable(['Ticker', 'Yield'], [['A', '1%'], ['B', '3%']]);
const msg = { children: [cell('Ningún contrato cumple')], parentNode: t.tbody };
t.tbody.children.push(msg);
S.sortTable(t, 1, 'desc');
assert.strictEqual(t.tbody.children[0].children[0].textContent, 'Ticker');
assert.deepStrictEqual(t.tbody.children.slice(1, 3).map(r => r.children[0].textContent), ['B', 'A']);
assert.strictEqual(t.tbody.children[3], msg);   // el mensaje sigue al final

// --- dirección inicial: números desc, texto asc (se decide en el clic)
assert.strictEqual(S.isNumericColumn([S.parseValue('1%'), S.parseValue('—'), S.parseValue('2%')]), true);
assert.strictEqual(S.isNumericColumn([S.parseValue('1%'), S.parseValue('abc')]), false);
assert.strictEqual(S.isNumericColumn([S.parseValue('—')]), false);

console.log('OK');

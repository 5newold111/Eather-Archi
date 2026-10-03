// コード.gs のサーバー処理のテスト。実行: node apps/kakeibo-gas/test/server.test.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ctx = vm.createContext({ console });
vm.runInContext(fs.readFileSync(path.join(__dirname, 'gas-stub.js'), 'utf8'), ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'コード.gs'), 'utf8'), ctx);
const run = (code) => vm.runInContext(code, ctx);

let failed = 0;
function test(name, fn) {
  try { fn(); console.log('ok   ' + name); } catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + e.message); }
}

const base = { date: '2026-10-03', type: '支出', amount: 580, category: '食費', method: '現金', memo: 'カフェ' };
ctx.input = null;
// vm の中で作られた値は「別の実行空間のもの」になり比較できないため、JSONで通常の値に戻す
const add = (entries) => { ctx.input = entries; return JSON.parse(JSON.stringify(run('addEntries(input)'))); };

test('設定にカテゴリと支払方法が入っている', () => {
  const c = run('getConfig()');
  assert.ok(c.categories['支出'].includes('食費'));
  assert.ok(c.methods.includes('現金'));
  assert.match(c.today, /^\d{4}-\d{2}-\d{2}$/);
});

test('初回登録でスプレッドシートと見出し行が作られる', () => {
  const r = add([base, Object.assign({}, base, { date: '2026-10-05', amount: 2340, category: '日用品', memo: 'スーパー' })]);
  assert.deepStrictEqual(r, { added: 2, errors: [] });
  const sheet = run('getSheet_()');
  assert.strictEqual(sheet.rows[0][1], '日付');
  assert.strictEqual(sheet.rows.length, 3);
  assert.strictEqual(sheet.rows[1][8], '手入力');
});

test('1件でも不正なら何も登録しない', () => {
  const before = run('getSheet_()').rows.length;
  const r = add([base, Object.assign({}, base, { amount: -5 }), Object.assign({}, base, { date: '2026-02-30' })]);
  assert.strictEqual(r.added, 0);
  assert.deepStrictEqual(r.errors.map((e) => e.index), [1, 2]);
  assert.strictEqual(run('getSheet_()').rows.length, before);
});

test('不正な入力をそれぞれ弾く', () => {
  const bad = [
    { amount: 1.5 }, { amount: '580' + 'x' }, { amount: 0 }, { type: '貯金' }, { category: '旅行' },
    { method: 'ツケ' }, { business_ratio: 101 }, { date: '2026/10/03' }, { type: '収入', category: '食費' },
  ];
  bad.forEach((b) => {
    const r = add([Object.assign({}, base, b)]);
    assert.strictEqual(r.added, 0, JSON.stringify(b));
  });
  assert.strictEqual(add([]).added, 0);
  assert.strictEqual(add('文字列').added, 0);
  assert.strictEqual(add(new Array(201).fill(base)).added, 0);
});

test('メモの先頭が = のときは数式として実行されない形で保存する', () => {
  add([Object.assign({}, base, { memo: '=IMPORTXML("http://example.com")' })]);
  const rows = run('getSheet_()').rows;
  assert.strictEqual(rows[rows.length - 1][6][0], "'");
  const m = run("getMonth('2026-10')");
  assert.ok(m.entries.some((e) => e.memo === '=IMPORTXML("http://example.com")'));
});

test('月の集計（収入・支出・カテゴリ別・事業按分・前月）', () => {
  add([
    { date: '2026-10-25', type: '収入', amount: 300000, category: '給与', method: '口座振替', source: 'チャット' },
    { date: '2026-10-10', type: '支出', amount: 10000, category: '水道光熱', method: '口座振替', business_ratio: 30 },
    { date: '2026-09-15', type: '支出', amount: 5000, category: '食費', method: '現金' },
  ]);
  const m = run("getMonth('2026-10')");
  assert.strictEqual(m.summary.income, 300000);
  assert.strictEqual(m.summary.expense, 580 + 2340 + 580 + 10000);
  assert.strictEqual(m.summary.byCategory['食費'], 1160);
  assert.strictEqual(m.summary.business, 3000);
  assert.strictEqual(m.summary.balance, 300000 - 13500);
  assert.strictEqual(m.prevSummary.expense, 5000);
  assert.strictEqual(m.entries[0].date, '2026-10-25');
  assert.ok(m.entries.every((e) => e.date.startsWith('2026-10')));
});

test('年をまたぐ前月（1月 → 前年12月）', () => {
  add([{ date: '2025-12-31', type: '支出', amount: 1000, category: '交際', method: '現金' }]);
  assert.strictEqual(run("getMonth('2026-01')").prevSummary.expense, 1000);
});

test('手でシートを編集して日付型になった行も読める', () => {
  const sheet = run('getSheet_()');
  sheet.rows.push(['manual', run('new Date(2026, 9, 20)'), '支出', 800, '交通', '現金', '', 0, '手入力', '']);
  assert.ok(run("getMonth('2026-10')").entries.some((e) => e.id === 'manual' && e.date === '2026-10-20'));
});

test('削除', () => {
  const id = run("getMonth('2026-10')").entries[0].id;
  ctx.id = id;
  assert.strictEqual(run('deleteEntry(id)'), true);
  assert.ok(!run("getMonth('2026-10')").entries.some((e) => e.id === id));
  assert.strictEqual(run("deleteEntry('nope')"), false);
});

test('不正な月の指定はエラー', () => {
  assert.throws(() => run("getMonth('2026-13')"));
  assert.throws(() => run("getMonth('abc')"));
});

test('2回目以降は同じスプレッドシートを使う', () => {
  assert.strictEqual(Object.keys(run('SpreadsheetApp._files')).length, 1);
});

console.log(failed ? '\n' + failed + '件失敗' : '\nすべて成功');
process.exit(failed ? 1 : 0);

// サーバー処理（.gs）のテスト。実行: node apps/kakeibo-gas/test/server.test.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ctx = vm.createContext({ console });
vm.runInContext(fs.readFileSync(path.join(__dirname, 'gas-stub.js'), 'utf8'), ctx);
require('./gs-files').forEach((f) => vm.runInContext(fs.readFileSync(path.join(__dirname, '..', f), 'utf8'), ctx));
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
    { amount: 1.5 }, { amount: '580' + 'x' }, { amount: 0 }, { type: '貯金' }, { category: '宇宙旅行' },
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

// ───── 固定費と変動費 ─────
const json = (code) => JSON.parse(JSON.stringify(run(code)));
const saveFixed = (item) => { ctx.item = item; return json('saveFixedCost(item)'); };

test('固定費より前に作られたシート（列Kなし）にも見出しを足し、既存の行を読める', () => {
  const ss = run('openSpreadsheet_()');
  const old = ss.insertSheet('家計簿_旧');
  // 10列だけの旧形式の行を用意して、シート名を差し替える
  old.rows.push(['ID', '日付', '区分', '金額', 'カテゴリ', '支払方法', 'メモ', '事業按分(%)', '登録元', '登録日時']);
  old.rows.push(['old1', '2026-08-01', '支出', 900, '通信', '口座振替', '', 0, '手入力', run('new Date()')]);
  // 以前の版のカテゴリ名（衣服美容）と、一覧に無いカテゴリ（手で書き換えた行など）
  old.rows.push(['old2', '2026-08-02', '支出', 4000, '衣服美容', '現金', '', 0, '手入力', run('new Date()')]);
  old.rows.push(['old3', '2026-08-03', '支出', 50, '謎の出費', '現金', '', 0, '手入力', run('new Date()')]);
  const current = ss.sheets['家計簿'];
  ss.sheets['家計簿'] = old;
  run('getSheet_()');
  assert.strictEqual(old.rows[0][10], '固定費ID');
  assert.strictEqual(old.rows[1][4], '通信');
  const m = json("getMonth('2026-08')");
  assert.strictEqual(m.entries.find((e) => e.id === 'old1').fixedId, '');
  assert.deepStrictEqual(m.summary.byTier, { 1: 900, 2: 4050, 3: 0, 4: 0 });
  ss.sheets['家計簿'] = current;
});

test('設定に4分類（敵1〜敵4）があり、支出カテゴリは分類から作られる', () => {
  const c = json('getConfig()');
  assert.deepStrictEqual(c.tiers.map((t) => t.name + ' ' + t.label), ['敵1 毎月の固定費', '敵2 変動費', '敵3 不定期の固定費', '敵4 変動費2']);
  assert.deepStrictEqual(c.categories['支出'], [].concat(...c.tiers.map((t) => t.categories)));
  ['社会保険', 'サブスク', '被服', '税金', '年会費', '家電家具', '旅行', '冠婚葬祭', '治療', '引越し'].forEach((n) => assert.ok(c.categories['支出'].includes(n), n));
  assert.ok(!c.categories['支出'].includes('衣服美容'));
  // 同じカテゴリが2つの分類に入っていない
  assert.strictEqual(new Set(c.categories['支出']).size, c.categories['支出'].length);
});

test('集計で4分類に分かれ、収入は分類に入らない', () => {
  add([
    { date: '2027-03-01', type: '支出', amount: 80000, category: '住居', method: '口座振替' },
    { date: '2027-03-02', type: '支出', amount: 1200, category: 'サブスク', method: 'クレジット' },
    { date: '2027-03-03', type: '支出', amount: 1500, category: '食費', method: '現金' },
    { date: '2027-03-04', type: '支出', amount: 12000, category: '年会費', method: 'クレジット' },
    { date: '2027-03-05', type: '支出', amount: 60000, category: '旅行', method: 'クレジット' },
    { date: '2027-03-25', type: '収入', amount: 250000, category: '給与', method: '口座振替' },
  ]);
  const m = json("getMonth('2027-03')");
  assert.deepStrictEqual(m.summary.byTier, { 1: 81200, 2: 1500, 3: 12000, 4: 60000 });
  assert.strictEqual(Object.values(m.summary.byTier).reduce((a, b) => a + b, 0), m.summary.expense);
  assert.strictEqual(m.entries.find((e) => e.type === '収入').tier, 0);
  assert.strictEqual(m.entries.find((e) => e.category === '旅行').tier, 4);
});

test('固定費リストの追加と不正な入力の拒否', () => {
  assert.deepStrictEqual(json('getFixedCosts()'), []);
  const r = saveFixed({ name: '家賃', amount: 85000, category: '住居', method: '口座振替', day: 27 });
  assert.ok(r.item && r.item.id);
  saveFixed({ name: '動画配信', amount: 990, category: '趣味娯楽', method: 'クレジット', day: 31 });
  assert.strictEqual(json('getFixedCosts()').length, 2);
  [
    { name: '', amount: 1, category: '住居', method: '現金', day: 1 },
    { name: 'x', amount: 0, category: '住居', method: '現金', day: 1 },
    { name: 'x', amount: 1, category: '給与', method: '現金', day: 1 },
    { name: 'x', amount: 1, category: '住居', method: 'ツケ', day: 1 },
    { name: 'x', amount: 1, category: '住居', method: '現金', day: 32 },
    { name: 'x', amount: 1, category: '住居', method: '現金', day: 0 },
  ].forEach((b) => assert.ok(saveFixed(b).error, JSON.stringify(b)));
  assert.strictEqual(json('getFixedCosts()').length, 2);
});

test('固定費を月ごとにまとめて登録し、2回目は二重にしない', () => {
  assert.deepStrictEqual(json("registerFixedCosts('2027-04')"), { added: 2, skipped: 0 });
  assert.deepStrictEqual(json("registerFixedCosts('2027-04')"), { added: 0, skipped: 2 });
  const m = json("getMonth('2027-04')");
  const rent = m.entries.find((e) => e.memo === '家賃');
  assert.strictEqual(rent.date, '2027-04-27');
  assert.strictEqual(rent.source, '固定費');
  assert.strictEqual(m.entries.find((e) => e.memo === '動画配信').date, '2027-04-30');
  // 家賃（住居）は敵1、動画配信（趣味娯楽で登録）は敵2
  assert.strictEqual(m.summary.byTier[1], 85000);
  assert.strictEqual(m.summary.byTier[2], 990);
  // 別の月は別に登録できる
  assert.deepStrictEqual(json("registerFixedCosts('2027-05')"), { added: 2, skipped: 0 });
});

test('引落日31日は2月末に寄せる（うるう年も）', () => {
  json("registerFixedCosts('2027-02')");
  json("registerFixedCosts('2028-02')");
  assert.ok(json("getMonth('2027-02')").entries.some((e) => e.date === '2027-02-28'));
  assert.ok(json("getMonth('2028-02')").entries.some((e) => e.date === '2028-02-29'));
});

test('登録済みの明細を消すと、その固定費はもう一度登録できる', () => {
  const rent = json("getMonth('2027-04')").entries.find((e) => e.memo === '家賃');
  ctx.id = rent.id;
  run('deleteEntry(id)');
  assert.deepStrictEqual(json("registerFixedCosts('2027-04')"), { added: 1, skipped: 1 });
});

test('固定費をリストから外しても登録済みの明細は残る', () => {
  const id = json('getFixedCosts()').find((f) => f.name === '動画配信').id;
  ctx.id = id;
  assert.strictEqual(run('deleteFixedCost(id)'), true);
  assert.strictEqual(json('getFixedCosts()').length, 1);
  assert.ok(json("getMonth('2027-04')").entries.some((e) => e.memo === '動画配信'));
  assert.throws(() => run("registerFixedCosts('2027-4')"));
});

// ───── 画面の組み立て ─────
test('index が読み込む画面ファイルがすべて存在し、各ファイルは100行未満', () => {
  const dir = path.join(__dirname, '..');
  const index = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
  const names = [...index.matchAll(/include_\('([^']+)'\)/g)].map((m) => m[1]);
  const files = fs.readdirSync(dir).filter((f) => f.startsWith('画面_')).map((f) => f.replace(/\.html$/, ''));
  assert.deepStrictEqual(names.slice().sort(), files.slice().sort(), 'index と「画面_」ファイルの対応');
  // include_ が本物と同じようにファイルの中身を返すこと
  ctx.__html = Object.fromEntries(names.map((n) => [n, fs.readFileSync(path.join(dir, n + '.html'), 'utf8')]));
  assert.strictEqual(run("include_('画面_処理1')"), ctx.__html['画面_処理1']);
  run('doGet()');
  const tooLong = fs.readdirSync(dir).filter((f) => /\.(gs|html)$/.test(f))
    .filter((f) => fs.readFileSync(path.join(dir, f), 'utf8').split('\n').length > 100);
  assert.deepStrictEqual(tooLong, [], '100行以上のファイル');
});

console.log(failed ? '\n' + failed + '件失敗' : '\nすべて成功');
process.exit(failed ? 1 : 0);

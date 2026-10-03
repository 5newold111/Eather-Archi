// 画面のテスト。実行: node apps/kakeibo-gas/test/ui.test.js （Playwright が必要）
// 模擬GASを差し込んだ確認用ページを一時フォルダに作り、取り込み・入力・集計・CSV・削除を操作する。
// スクリーンショットも同じ一時フォルダに保存する。
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const S = fs.mkdtempSync(path.join(os.tmpdir(), 'kakeibo-'));
require('./build-harness')(path.join(S, 'harness.html'));
(async () => {
  const browser = await chromium.launch();
  const errors = [];
  for (const [label, viewport] of [['pc', { width: 1000, height: 900 }], ['sp', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport, acceptDownloads: true });
    page.on('pageerror', (e) => errors.push(label + ': ' + e.message));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(label + ': ' + m.text()); });
    await page.goto('file://' + S + '/harness.html');
    await page.waitForFunction(() => document.querySelector('#fMethod').options.length > 1);

    // 01 Claudeの返答（説明文と表記ゆれ混じり）を貼り付けて取り込む
    const reply = `記録しますね。以下を家計簿アプリに貼り付けてください。

\`\`\`json
[
  {"date":"2026-10-03","type":"支出","amount":580,"category":"食費","method":"現金","memo":"カフェ"},
  {"date":"10/2","type":"支出","amount":"2,340円","category":"日用品","method":"PayPay","memo":"スーパー"},
  {"date":"2026-10-01","type":"支出","amount":12000,"category":"水道光熱","method":"口座振替","memo":"電気代","business_ratio":30},
  {"date":"2026-10-01","type":"支出","amount":3000,"category":"ペット","method":"カード","memo":"フード"},
  {"date":"2026-09-25","type":"収入","amount":280000,"category":"給与","method":"口座振替","memo":"9月分"}
]
\`\`\`
合計 17,920円の支出です。`;
    await page.fill('#paste', reply);
    await page.click('#parse');
    await page.waitForSelector('#preview tbody tr');
    assert.strictEqual(await page.locator('#preview tbody tr').count(), 5);
    const flags = await page.locator('#preview .flag').allTextContents();
    assert.ok(flags.some((t) => t.includes('ペット')), 'カテゴリ外の注記');
    const row2 = page.locator('#preview tbody tr').nth(1);
    assert.strictEqual(await row2.locator('input[type=text]').first().inputValue(), '2340');
    assert.strictEqual(await row2.locator('select').nth(2).inputValue(), '電子マネー');
    await page.screenshot({ path: `${S}/01-import-${label}.png`, fullPage: true });

    // 金額を不正にして登録 → 何も登録されずエラー行が示される
    await row2.locator('input[type=text]').first().fill('-1');
    await row2.locator('input[type=text]').first().dispatchEvent('change');
    await page.click('#saveImport');
    await page.waitForSelector('#importMsg.err');
    assert.match(await page.textContent('#importMsg'), /まだ何も登録していません/);
    assert.strictEqual(await page.evaluate(() => window.__gas.getSheet_().rows.length), 1);
    // 直して登録
    await row2.locator('input[type=text]').first().fill('2340');
    await row2.locator('input[type=text]').first().dispatchEvent('change');
    await page.click('#saveImport');
    await page.waitForFunction(() => /5件を家計簿に登録/.test(document.querySelector('#importMsg').textContent));

    // 02 手入力
    await page.click('nav button[data-tab=input]');
    await page.fill('#fDate', '2026-10-03');
    await page.fill('#fAmount', '１，２００');
    await page.selectOption('#fCategory', '交通');
    await page.fill('#fMemo', 'タクシー');
    await page.screenshot({ path: `${S}/02-input-${label}.png`, fullPage: true });
    await page.click('#saveInput');
    await page.waitForFunction(() => /登録しました/.test(document.querySelector('#inputMsg').textContent));
    await page.fill('#fAmount', '');
    await page.click('#saveInput');
    await page.waitForSelector('#inputMsg.err');

    // 03 月の集計
    await page.click('nav button[data-tab=month]');
    await page.waitForFunction(() => document.querySelector('#sExpense').textContent !== '—' && !/読み込み/.test(document.querySelector('#monthMsg').textContent));
    assert.strictEqual(await page.textContent('#monthLabel'), '2026.10');
    assert.strictEqual(await page.textContent('#sExpense'), '¥19,120');
    assert.strictEqual(await page.textContent('#sIncome'), '¥0');
    assert.match(await page.textContent('#sBusiness'), /¥3,600/);
    assert.strictEqual(await page.textContent('#sFixed'), '¥12,000');
    assert.strictEqual(await page.textContent('#sVariable'), '¥7,120');
    assert.deepStrictEqual((await page.locator('#bars h3').allTextContents()).map((t) => t.replace(/¥.*/, '')), ['固定費', '変動費']);
    assert.strictEqual(await page.locator('#list tbody tr').count(), 5);
    await page.screenshot({ path: `${S}/04-month-before-${label}.png`, fullPage: true });

    // CSV
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#csvSave')]);
    const csv = require('fs').readFileSync(await dl.path(), 'utf8');
    assert.ok(csv.startsWith('\uFEFF日付,区分,固定/変動,金額'));
    assert.ok(csv.includes('2026-10-01,支出,固定,12000,水道光熱,口座振替,電気代,30,チャット'));
    await page.click('#csvShow');
    assert.ok((await page.inputValue('#csvText')).includes('タクシー'));

    // 前月 → 9月の給与
    await page.click('#prevMonth');
    await page.waitForFunction(() => document.querySelector('#monthLabel').textContent === '2026.09' && document.querySelector('#sIncome').textContent === '¥280,000');

    // 削除
    page.once('dialog', (d) => d.accept());
    await page.click('#list tbody tr button');
    await page.waitForFunction(() => document.querySelectorAll('#list tbody tr').length === 0);

    // 03 固定費: リストに追加 → 月を選んで登録 → もう一度押しても二重にならない
    await page.click('nav button[data-tab=fixed]');
    await page.waitForFunction(() => /まだ登録されていません/.test(document.querySelector('#fixedList tbody').textContent));
    assert.ok(await page.isDisabled('#registerFixed'));
    assert.ok((await page.locator('#xCategory option').allTextContents()).includes('保険（固定）'));
    for (const [name, amount, day, cat, method] of [['家賃', '85,000', '27', '住居', '口座振替'], ['動画配信', '990', '31', '趣味娯楽', 'クレジット'], ['医療保険', '3200', '5', '保険', '口座振替']]) {
      await page.fill('#xName', name);
      await page.fill('#xAmount', amount);
      await page.fill('#xDay', day);
      await page.selectOption('#xCategory', cat);
      await page.selectOption('#xMethod', method);
      await page.click('#addFixed');
      await page.waitForFunction((n) => document.querySelector('#fixedList tbody').textContent.includes(n), name);
    }
    await page.fill('#xName', '');
    await page.click('#addFixed');
    await page.waitForSelector('#fixedMsg.err');
    assert.strictEqual(await page.textContent('#fixedTotal'), '¥89,190');
    assert.strictEqual(await page.locator('#fixedList tbody tr').first().locator('td').nth(1).textContent(), '医療保険');
    await page.click('#fixedNext');
    await page.click('#fixedNext');
    await page.waitForFunction(() => document.querySelector('#registerFixed').textContent === '2026年12月分の固定費を登録');
    await page.click('#registerFixed');
    await page.waitForFunction(() => /3件登録しました/.test(document.querySelector('#fixedMsg').textContent));
    await page.click('#registerFixed');
    await page.waitForFunction(() => /3件はこの月に登録済み/.test(document.querySelector('#fixedMsg').textContent));
    await page.screenshot({ path: `${S}/03-fixed-${label}.png`, fullPage: true });

    // 集計: 12月は固定費 85,000 + 3,200、変動費 990（動画配信は趣味娯楽）。31日は12月なのでそのまま
    await page.click('nav button[data-tab=month]');
    await page.waitForFunction(() => document.querySelector('#monthLabel').textContent === '2026.12' && document.querySelector('#sFixed').textContent === '¥88,200');
    assert.strictEqual(await page.textContent('#sVariable'), '¥990');
    assert.ok((await page.locator('#list tbody td').allTextContents()).includes('12/31'));
    await page.screenshot({ path: `${S}/04-month-${label}.png`, fullPage: true });

    // 固定費をリストから外す
    await page.click('nav button[data-tab=fixed]');
    await page.waitForFunction(() => document.querySelectorAll('#fixedList tbody tr').length === 3);
    page.once('dialog', (d) => d.accept());
    await page.locator('#fixedList tbody tr button').first().click();
    await page.waitForFunction(() => document.querySelectorAll('#fixedList tbody tr').length === 2);

    // 読み取れない貼り付け
    await page.click('nav button[data-tab=import]');
    await page.fill('#paste', 'カフェで580円使いました');
    await page.click('#parse');
    assert.match(await page.textContent('#importMsg'), /見つかりませんでした/);
    console.log(label + ': ok');
    await page.close();
  }
  await browser.close();
  console.log('スクリーンショット: ' + S);
  if (errors.length) { console.log('ブラウザのエラー:\n' + errors.join('\n')); process.exit(1); }
})().catch((e) => { console.error(e); process.exit(1); });

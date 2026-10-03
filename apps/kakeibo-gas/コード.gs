/**
 * 家計簿（Claudeチャット連携） — サーバー側の処理
 *
 * Google Apps Script のウェブアプリとして動きます。
 * 画面（index.html）から google.script.run で呼ばれ、スプレッドシートの「家計簿」シートを読み書きします。
 */

// 保存先スプレッドシートのID。
// 経費管理で使っているスプレッドシートにまとめたい場合は、そのURLの /d/ と /edit の間の文字列を入れてください。
// 空のままなら、初回アクセス時に「家計簿」という新しいスプレッドシートを自動で作ります。
const SPREADSHEET_ID = '';

const SHEET_NAME = '家計簿';
const HEADERS = ['ID', '日付', '区分', '金額', 'カテゴリ', '支払方法', 'メモ', '事業按分(%)', '登録元', '登録日時', '固定費ID'];
const FIXED_SHEET_NAME = '固定費';
const FIXED_HEADERS = ['ID', '名前', '金額', 'カテゴリ', '支払方法', '引落日'];

const CATEGORIES = {
  '支出': ['食費', '日用品', '住居', '水道光熱', '通信', '保険', '交通', '趣味娯楽', '衣服美容', '医療', '教育', '交際', 'その他'],
  '収入': ['給与', '事業', 'その他'],
};
// 集計で「固定費」として扱う支出カテゴリ。これ以外の支出は「変動費」になります
const FIXED_CATEGORIES = ['住居', '水道光熱', '通信', '保険'];
const METHODS = ['現金', 'クレジット', '電子マネー', '口座振替', 'その他'];
const SOURCES = ['手入力', 'チャット'];

const MAX_ENTRIES_PER_CALL = 200;
const MAX_AMOUNT = 100000000;
const MAX_MEMO_LENGTH = 200;

/** ウェブアプリのURLを開いたときに画面を返す */
function doGet() {
  return HtmlService.createHtmlOutputFromFile('index')
    .setTitle('家計簿')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** 画面の選択肢（カテゴリ・支払方法）を返す */
function getConfig() {
  return { categories: CATEGORIES, fixedCategories: FIXED_CATEGORIES, methods: METHODS, today: today_() };
}

/**
 * 複数件をまとめて登録する。
 * 1件でも不正があれば何も書き込まず、どの行がなぜ不正かを返す（中途半端な登録を防ぐため）。
 * @param {Object[]} entries {date, type, amount, category, method, memo, business_ratio, source}
 * @return {{added: number, errors: {index: number, message: string}[]}}
 */
function addEntries(entries) {
  if (!Array.isArray(entries) || entries.length === 0) {
    return { added: 0, errors: [{ index: -1, message: '登録するデータがありません' }] };
  }
  if (entries.length > MAX_ENTRIES_PER_CALL) {
    return { added: 0, errors: [{ index: -1, message: '一度に登録できるのは' + MAX_ENTRIES_PER_CALL + '件までです' }] };
  }

  const rows = [];
  const errors = [];
  const now = new Date();
  entries.forEach(function (raw, i) {
    const result = validateEntry_(raw);
    if (result.error) {
      errors.push({ index: i, message: result.error });
      return;
    }
    const e = result.entry;
    rows.push([Utilities.getUuid(), e.date, e.type, e.amount, e.category, e.method, e.memo, e.business_ratio, e.source, now, '']);
  });
  if (errors.length) return { added: 0, errors: errors };

  // 同時に2つの画面から登録したとき、行が重なって上書きされないよう順番待ちにする
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = getSheet_();
    sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, HEADERS.length).setValues(rows);
  } finally {
    lock.releaseLock();
  }
  return { added: rows.length, errors: [] };
}

/**
 * 指定月の明細と集計を返す。
 * @param {string} yyyyMm 例: '2026-10'
 */
function getMonth(yyyyMm) {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(String(yyyyMm))) throw new Error('月の指定が正しくありません: ' + yyyyMm);
  const prev = shiftMonth_(yyyyMm, -1);
  const all = readAll_();

  const entries = all.filter(function (e) { return e.date.indexOf(yyyyMm) === 0; })
    .sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : (a.createdAt < b.createdAt ? 1 : -1); });
  const prevEntries = all.filter(function (e) { return e.date.indexOf(prev) === 0; });

  return {
    month: yyyyMm,
    entries: entries,
    summary: summarize_(entries),
    prevSummary: summarize_(prevEntries),
  };
}

/** 1件削除する（誤登録の取り消し） */
function deleteEntry(id) {
  if (!id) throw new Error('IDがありません');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    return deleteRowById_(getSheet_(), id);
  } finally {
    lock.releaseLock();
  }
}

/** 固定費リスト（毎月決まって出ていく支出）を返す */
function getFixedCosts() {
  const sheet = getFixedSheet_();
  const last = sheet.getLastRow();
  if (last < 2) return [];
  return sheet.getRange(2, 1, last - 1, FIXED_HEADERS.length).getValues()
    .filter(function (r) { return r[0]; })
    .map(function (r) {
      return { id: String(r[0]), name: String(r[1]).replace(/^'/, ''), amount: Number(r[2]) || 0, category: String(r[3]), method: String(r[4]), day: Number(r[5]) || 1 };
    });
}

/**
 * 固定費を1件追加する。
 * @param {Object} item {name, amount, category, method, day}
 * @return {{error: string}|{item: Object}}
 */
function saveFixedCost(item) {
  if (!item || typeof item !== 'object') return { error: 'データの形式が正しくありません' };
  let name = String(item.name == null ? '' : item.name).trim().slice(0, 50);
  if (!name) return { error: '名前を入力してください（例: 家賃）' };
  if (/^[=+\-@]/.test(name)) name = "'" + name;
  const amount = Number(item.amount);
  if (!Number.isInteger(amount) || amount < 1 || amount > MAX_AMOUNT) return { error: '金額は1円以上の整数で入力してください' };
  const category = String(item.category || '').trim();
  if (CATEGORIES['支出'].indexOf(category) < 0) return { error: '支出のカテゴリ「' + category + '」はありません' };
  const method = String(item.method || 'その他').trim();
  if (METHODS.indexOf(method) < 0) return { error: '支払方法「' + method + '」はありません' };
  const day = Number(item.day);
  if (!Number.isInteger(day) || day < 1 || day > 31) return { error: '引落日は1〜31の整数で入力してください' };

  const row = [Utilities.getUuid(), name, amount, category, method, day];
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = getFixedSheet_();
    sheet.getRange(sheet.getLastRow() + 1, 1, 1, FIXED_HEADERS.length).setValues([row]);
  } finally {
    lock.releaseLock();
  }
  return { item: { id: row[0], name: name.replace(/^'/, ''), amount: amount, category: category, method: method, day: day } };
}

/** 固定費を1件リストから外す（登録済みの明細は消えません） */
function deleteFixedCost(id) {
  if (!id) throw new Error('IDがありません');
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    return deleteRowById_(getFixedSheet_(), id);
  } finally {
    lock.releaseLock();
  }
}

/**
 * 固定費リストの全件を、指定月の明細としてまとめて登録する。
 * 同じ月に同じ固定費が登録済みなら、二重にならないよう飛ばす。
 * @param {string} yyyyMm 例: '2026-10'
 * @return {{added: number, skipped: number}}
 */
function registerFixedCosts(yyyyMm) {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(String(yyyyMm))) throw new Error('月の指定が正しくありません: ' + yyyyMm);
  const items = getFixedCosts();
  const lastDay = new Date(Number(yyyyMm.slice(0, 4)), Number(yyyyMm.slice(5, 7)), 0).getDate();
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const done = {};
    readAll_().forEach(function (e) { if (e.fixedId && e.date.indexOf(yyyyMm) === 0) done[e.fixedId] = true; });
    const now = new Date();
    const rows = [];
    let skipped = 0;
    items.forEach(function (f) {
      if (done[f.id]) { skipped++; return; }
      // 引落日がその月に無い日（31日など）は月末に寄せる
      const date = yyyyMm + '-' + ('0' + Math.min(f.day, lastDay)).slice(-2);
      const memo = /^[=+\-@]/.test(f.name) ? "'" + f.name : f.name;
      rows.push([Utilities.getUuid(), date, '支出', f.amount, f.category, f.method, memo, 0, '固定費', now, f.id]);
    });
    if (rows.length) {
      const sheet = getSheet_();
      sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, HEADERS.length).setValues(rows);
    }
    return { added: rows.length, skipped: skipped };
  } finally {
    lock.releaseLock();
  }
}

// ───────── ここから下は内部処理（画面からは呼べません） ─────────

/** 入力1件を検証し、保存用の形に整える */
function validateEntry_(raw) {
  if (!raw || typeof raw !== 'object') return { error: 'データの形式が正しくありません' };

  const date = String(raw.date || '').trim();
  if (!isValidDate_(date)) return { error: '日付は YYYY-MM-DD の形で入力してください（' + (date || '空') + '）' };

  const type = String(raw.type || '').trim();
  if (!CATEGORIES[type]) return { error: '区分は「支出」か「収入」です（' + (type || '空') + '）' };

  const amount = Number(raw.amount);
  if (!Number.isInteger(amount) || amount < 1 || amount > MAX_AMOUNT) {
    return { error: '金額は1円以上の整数で入力してください（' + raw.amount + '）' };
  }

  const category = String(raw.category || '').trim();
  if (CATEGORIES[type].indexOf(category) < 0) {
    return { error: type + 'のカテゴリ「' + category + '」はありません' };
  }

  const method = String(raw.method || 'その他').trim();
  if (METHODS.indexOf(method) < 0) return { error: '支払方法「' + method + '」はありません' };

  let memo = String(raw.memo == null ? '' : raw.memo).trim().slice(0, MAX_MEMO_LENGTH);
  // 「=」などで始まる文字はスプレッドシートが数式として実行してしまうため、先頭に ' を付けて文字として保存する
  if (/^[=+\-@]/.test(memo)) memo = "'" + memo;

  const ratio = raw.business_ratio == null || raw.business_ratio === '' ? 0 : Number(raw.business_ratio);
  if (!Number.isInteger(ratio) || ratio < 0 || ratio > 100) {
    return { error: '事業按分は0〜100の整数で入力してください（' + raw.business_ratio + '）' };
  }

  const source = SOURCES.indexOf(raw.source) >= 0 ? raw.source : '手入力';
  return { entry: { date: date, type: type, amount: amount, category: category, method: method, memo: memo, business_ratio: ratio, source: source } };
}

/** 1列目（ID）が一致する行を削除する */
function deleteRowById_(sheet, id) {
  const last = sheet.getLastRow();
  if (last < 2) return false;
  const ids = sheet.getRange(2, 1, last - 1, 1).getValues();
  for (let i = 0; i < ids.length; i++) {
    if (ids[i][0] === id) {
      sheet.deleteRow(i + 2);
      return true;
    }
  }
  return false;
}

function isValidDate_(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return false;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.getFullYear() === Number(m[1]) && d.getMonth() === Number(m[2]) - 1 && d.getDate() === Number(m[3]);
}

/** 保存先のシートを取得する。無ければスプレッドシートやシート・見出し行を作る */
function getSheet_() {
  const ss = openSpreadsheet_();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold');
    sheet.setFrozenRows(1);
    // 日付列は文字として保存する（自動で日付型に変換されて時差でずれるのを防ぐ）
    sheet.getRange('B:B').setNumberFormat('@');
    sheet.getRange('D:D').setNumberFormat('#,##0');
    sheet.hideColumns(1);
  } else if (sheet.getRange(1, HEADERS.length, 1, 1).getValues()[0][0] !== HEADERS[HEADERS.length - 1]) {
    // 固定費の機能より前に作られたシートには「固定費ID」列の見出しが無いので足す（既存の行はそのまま）
    sheet.getRange(1, HEADERS.length, 1, 1).setValues([[HEADERS[HEADERS.length - 1]]]).setFontWeight('bold');
  }
  return sheet;
}

/** 固定費リストのシートを取得する。無ければ作る */
function getFixedSheet_() {
  const ss = openSpreadsheet_();
  let sheet = ss.getSheetByName(FIXED_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(FIXED_SHEET_NAME);
    sheet.getRange(1, 1, 1, FIXED_HEADERS.length).setValues([FIXED_HEADERS]).setFontWeight('bold');
    sheet.setFrozenRows(1);
    sheet.getRange('C:C').setNumberFormat('#,##0');
    sheet.hideColumns(1);
  }
  return sheet;
}

function openSpreadsheet_() {
  if (SPREADSHEET_ID) return SpreadsheetApp.openById(SPREADSHEET_ID);
  const props = PropertiesService.getScriptProperties();
  const savedId = props.getProperty('SPREADSHEET_ID');
  if (savedId) return SpreadsheetApp.openById(savedId);
  const ss = SpreadsheetApp.create('家計簿');
  props.setProperty('SPREADSHEET_ID', ss.getId());
  return ss;
}

/** シートの全明細を読み込む */
function readAll_() {
  const sheet = getSheet_();
  const last = sheet.getLastRow();
  if (last < 2) return [];
  const tz = Session.getScriptTimeZone();
  return sheet.getRange(2, 1, last - 1, HEADERS.length).getValues()
    .filter(function (r) { return r[0] && r[1]; })
    .map(function (r) {
      return {
        id: String(r[0]),
        // 手でシートを編集して日付型になった行も読めるようにする
        date: r[1] instanceof Date ? Utilities.formatDate(r[1], tz, 'yyyy-MM-dd') : String(r[1]),
        type: String(r[2]),
        amount: Number(r[3]) || 0,
        category: String(r[4]),
        method: String(r[5]),
        memo: String(r[6]).replace(/^'/, ''),
        business_ratio: Number(r[7]) || 0,
        source: String(r[8]),
        createdAt: r[9] instanceof Date ? r[9].getTime() : 0,
        fixedId: r[10] ? String(r[10]) : '',
        fixed: r[2] !== '収入' && FIXED_CATEGORIES.indexOf(String(r[4])) >= 0,
      };
    });
}

/** 収入・支出・差額とカテゴリ別合計を計算する */
function summarize_(entries) {
  const s = { income: 0, expense: 0, fixed: 0, variable: 0, balance: 0, business: 0, byCategory: {} };
  entries.forEach(function (e) {
    if (e.type === '収入') {
      s.income += e.amount;
    } else {
      s.expense += e.amount;
      if (e.fixed) s.fixed += e.amount; else s.variable += e.amount;
      s.byCategory[e.category] = (s.byCategory[e.category] || 0) + e.amount;
      s.business += Math.round(e.amount * e.business_ratio / 100);
    }
  });
  s.balance = s.income - s.expense;
  return s;
}

function shiftMonth_(yyyyMm, delta) {
  const y = Number(yyyyMm.slice(0, 4));
  const m = Number(yyyyMm.slice(5, 7)) - 1 + delta;
  const d = new Date(y, m, 1);
  return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2);
}

function today_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

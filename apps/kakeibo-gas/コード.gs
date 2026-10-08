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

// 支出の4分類と、それぞれに入るカテゴリ。カテゴリを足したり別の分類へ移したりするときは、ここを書き換えます
const TIERS = [
  { id: 1, name: '敵1', label: '毎月の固定費', categories: ['住居', '水道光熱', '社会保険', '通信', 'サブスク', '保険'] },
  { id: 2, name: '敵2', label: '変動費', categories: ['食費', '日用品', '被服', '交通', '医療', '趣味娯楽', '交際', '教育', 'その他'] },
  { id: 3, name: '敵3', label: '不定期の固定費', categories: ['税金', '年会費'] },
  { id: 4, name: '敵4', label: '変動費2', categories: ['家電家具', '旅行', '冠婚葬祭', '治療', '引越し'] },
];
// 以前の版のカテゴリ名で登録済みの行を、どの分類で集計するか（一覧に無いカテゴリは敵2）
const LEGACY_TIERS = { '衣服美容': 2 };

const CATEGORIES = {
  '支出': TIERS.reduce(function (all, t) { return all.concat(t.categories); }, []),
  '収入': ['給与', '事業', 'その他'],
};
const METHODS = ['現金', 'クレジット', '電子マネー', '口座振替', 'その他'];
const SOURCES = ['手入力', 'チャット'];

const MAX_ENTRIES_PER_CALL = 200;
const MAX_AMOUNT = 100000000;
const MAX_MEMO_LENGTH = 200;

/** ウェブアプリのURLを開いたときに画面を返す（index の中で「画面_」の各ファイルを読み込んで組み立てる） */
function doGet() {
  return HtmlService.createTemplateFromFile('index').evaluate()
    .setTitle('家計簿')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** 画面のファイルの中身をそのまま返す（index から呼ぶ。末尾の _ で画面側からは呼べない） */
function include_(name) {
  return HtmlService.createHtmlOutputFromFile(name).getContent();
}

/** 画面の選択肢（カテゴリ・支払方法）を返す */
function getConfig() {
  return { categories: CATEGORIES, tiers: TIERS, methods: METHODS, today: today_() };
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


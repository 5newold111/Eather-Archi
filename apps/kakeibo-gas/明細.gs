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


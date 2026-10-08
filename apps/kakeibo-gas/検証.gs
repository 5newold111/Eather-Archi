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


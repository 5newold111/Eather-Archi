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
        tier: r[2] === '収入' ? 0 : tierOf_(String(r[4])),
      };
    });
}

function today_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

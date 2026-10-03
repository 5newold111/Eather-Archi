// Google Apps Script の SpreadsheetApp などを、メモリ上の表で真似る模擬部品（テスト専用）。
// Node.js のテストと、ブラウザでの画面確認の両方から読み込む。
(function (root) {
  function makeSheet(name) {
    const rows = [];
    const sheet = {
      name: name,
      rows: rows,
      getLastRow: () => rows.length,
      setFrozenRows: () => sheet,
      hideColumns: () => sheet,
      deleteRow: (n) => { rows.splice(n - 1, 1); },
      getRange: (r, c, nr, nc) => {
        const range = {
          setFontWeight: () => range,
          setNumberFormat: () => range,
          setValues: (vals) => {
            if (vals.length !== nr || vals.some((v) => v.length !== nc)) throw new Error('範囲と値の大きさが合いません');
            vals.forEach((v, i) => { rows[r - 1 + i] = v.slice(); });
            return range;
          },
          getValues: () => rows.slice(r - 1, r - 1 + nr).map((row) => row.slice(c - 1, c - 1 + nc)),
        };
        return range;
      },
    };
    return sheet;
  }
  function makeSpreadsheet(id) {
    const sheets = {};
    return {
      sheets: sheets,
      getId: () => id,
      getSheetByName: (n) => sheets[n] || null,
      insertSheet: (n) => (sheets[n] = makeSheet(n)),
    };
  }
  const files = {};
  let seq = 0;
  const props = {};
  root.SpreadsheetApp = {
    create: (name) => { const id = 'ss' + (++seq); return (files[id] = makeSpreadsheet(id)); },
    openById: (id) => { if (!files[id]) throw new Error('スプレッドシートが見つかりません: ' + id); return files[id]; },
    _files: files,
  };
  root.PropertiesService = { getScriptProperties: () => ({ getProperty: (k) => props[k] || null, setProperty: (k, v) => { props[k] = v; } }) };
  root.LockService = { getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) };
  let uuid = 0;
  root.Utilities = {
    getUuid: () => 'id-' + (++uuid),
    formatDate: (d, tz, fmt) => {
      const p = (n) => ('0' + n).slice(-2);
      return fmt.replace('yyyy', d.getFullYear()).replace('MM', p(d.getMonth() + 1)).replace('dd', p(d.getDate()));
    },
  };
  root.Session = { getScriptTimeZone: () => 'Asia/Tokyo' };
  root.HtmlService = { createHtmlOutputFromFile: () => { const o = { setTitle: () => o, addMetaTag: () => o }; return o; } };
})(typeof window !== 'undefined' ? window : globalThis);

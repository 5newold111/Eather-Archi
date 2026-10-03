// index.html の本体スクリプトの直前に、模擬GAS＋コード.gs＋google.script.run の代役を差し込んだ確認用ページを作る
const fs = require('fs');
const path = require('path');
const dir = path.join(__dirname, '..') + '/';
const html = fs.readFileSync(dir + 'index.html', 'utf8');
const inject = '<script>' + fs.readFileSync(dir + 'test/gas-stub.js', 'utf8') + '</script>\n'
  + '<script>' + fs.readFileSync(dir + 'コード.gs', 'utf8') + '\nwindow.__gas = { addEntries, getMonth, getConfig, deleteEntry, getSheet_, getFixedCosts, saveFixedCost, deleteFixedCost, registerFixedCosts };</script>\n'
  + `<script>
  // google.script.run の代役。本物と同じく非同期で、値はJSONとして受け渡す
  window.google = { script: { get run() {
    let ok = () => {}, ng = () => {};
    const runner = new Proxy({}, { get(_, name) {
      if (name === 'withSuccessHandler') return (f) => { ok = f; return runner; };
      if (name === 'withFailureHandler') return (f) => { ng = f; return runner; };
      return (...args) => setTimeout(() => {
        try { ok(JSON.parse(JSON.stringify(window.__gas[name](...JSON.parse(JSON.stringify(args)))) ?? null)); }
        catch (e) { ng(e); }
      }, 30);
    }});
    return runner;
  }}};
  </script>\n`;
const idx = html.lastIndexOf('<script>');
module.exports = (out) => fs.writeFileSync(out, html.slice(0, idx) + inject + html.slice(idx));

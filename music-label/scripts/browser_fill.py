#!/usr/bin/env python3
"""
Suno と DistroKid の入力を、システムが専用のブラウザで行う（ダッシュボードのボタンから 1 クリック。決定 22）

  suno       曲の内容（曲名・スタイル・入れないもの・歌詞）を Suno の作曲画面に入れる。「Create」は押さない
  distrokid  DistroKid の登録画面に下書き（項目・音源・ジャケット）を入れる。最後の登録ボタンは押さない

押すのは Gon さんだけ：Suno の Create、いいね、ダウンロード、DistroKid の最後の登録ボタン。
システムはこれらのボタン（作成・送信・公開・購入・削除など）を絶対に押さない（押そうとしても止める）。

しくみ：
  ・専用の Chrome（ふだんの Chrome とは別の窓。ログインはここが覚える：~/.postdog-browser）を、ふつうの Chrome として開き、
    入力するときだけ外からつないで欄を埋める。自動操作モードで開かないので、Google などでのログインも断られない。
    初回だけ「login suno」「login distrokid」でログインする（このときは自動操作を一切つながない）。入力のあともタブは開いたまま
  ・入れる欄は、欄の名前・見出し・薄い字の案内（placeholder）から探す。
    見つからない欄は、ブラウザの右下の案内に「〇〇の欄をクリックしてください」と出るので、1 回クリックして教える。
    教えた場所は out/browser/fields.json に覚え、次からは自動で入れる（クリックしても、その欄を押したことにはならない）
  ・入れ終わったら、右下の案内に「確かめて Create（登録ボタン）を押してください」と出る。
    Suno は「次の曲へ」で次の曲に進む。DistroKid は登録したあと「登録した」を押すと、楽曲管理シートとダッシュボードに記録される
  ・入れられなかった項目は、案内に値と一緒に並ぶ（手で入れる）

使い方：
  python3 scripts/browser_fill.py suno                 # 作曲待ちの全曲を 1 曲ずつ
  python3 scripts/browser_fill.py suno PDR-003         # この曲だけ
  python3 scripts/browser_fill.py distrokid PDR-003    # DistroKid の下書き
  python3 scripts/browser_fill.py login suno           # 初回：専用ブラウザでログインしておく（distrokid も）
  python3 scripts/browser_fill.py forget suno          # 覚えた欄の場所を消す（画面が変わってうまく入らないとき）

最初に 1 回：ダッシュボードの「ブラウザ入力の部品を入れる」を押す（中身は  python3 -m pip install --user playwright  と  python3 -m playwright install chromium ）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, load_dotenv, read_json  # noqa: E402

load_dotenv()
import _songs  # noqa: E402

S = Path(__file__).resolve().parent
SITES = {"suno": os.environ.get("POSTDOG_SUNO_URL", "https://suno.com/create"),
         "distrokid": os.environ.get("POSTDOG_DISTROKID_URL", "https://distrokid.com/new/")}
LEARNED = OUT / "browser" / "fields.json"
PROFILE = Path(os.environ.get("POSTDOG_BROWSER_PROFILE", str(Path.home() / ".postdog-browser")))
AUTO = os.environ.get("POSTDOG_AUTOCONTINUE") == "1"   # 試験用：案内のボタンを待たずに進む
# システムが絶対に押さないボタン（作成・送信・公開・購入・削除など）
NEVER = re.compile(r"create|generate|submit|upload|done|publish|release|distribute|buy|purchase|pay|checkout|subscribe|delete|remove|"
                   r"作成|生成|送信|公開|配信|購入|支払|削除", re.I)

# ページに毎回入れる下準備：欄の場所を言葉にする関数と、「クリックで教える」の受け取り
INIT_JS = r"""
(() => {
  window.__postdogSel = function (el) {
    const q = s => String(s).replace(/"/g, '\\"');
    for (const a of ['data-testid', 'id', 'name', 'aria-label', 'placeholder']) {
      const v = el.getAttribute && el.getAttribute(a);
      if (!v || (a === 'id' && /\d{3,}|:r|^radix-/.test(v))) continue;
      const sel = a === 'id' ? '#' + CSS.escape(v) : `${el.tagName.toLowerCase()}[${a}="${q(v)}"]`;
      try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (e) {}
    }
    const parts = []; let e = el;
    while (e && e.nodeType === 1 && e !== document.body) {
      let i = 1, s = e; while ((s = s.previousElementSibling)) if (s.tagName === e.tagName) i++;
      parts.unshift(e.tagName.toLowerCase() + ':nth-of-type(' + i + ')'); e = e.parentElement;
    }
    return 'body > ' + parts.join(' > ');
  };
  window.__postdogEditable = function (el) {
    if (!el) return null;
    if (el.matches && el.matches('input,textarea,select,[contenteditable="true"],[contenteditable=""]')) return el;
    const lab = el.closest && el.closest('label');
    if (lab && lab.control) return lab.control;
    const inner = el.querySelector && el.querySelector('input,textarea,select,[contenteditable="true"]');
    return inner || el;
  };
  document.addEventListener('click', (ev) => {
    if (!window.__postdogPicking) return;
    if (ev.target.closest && ev.target.closest('#postdog-overlay')) return;
    ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation();
    window.__postdogPicking = false;
    const el = window.__postdogEditable(ev.target);
    window.postdog({action: 'picked', selector: window.__postdogSel(el), tag: el.tagName.toLowerCase()});
  }, true);
})();
"""

OVERLAY_JS = r"""
(cfg) => {
  let box = document.getElementById('postdog-overlay');
  if (!box) { box = document.createElement('div'); box.id = 'postdog-overlay'; document.documentElement.appendChild(box); }
  box.setAttribute('style', 'position:fixed;right:16px;bottom:16px;z-index:2147483647;width:min(420px,calc(100vw - 32px));max-height:70vh;overflow:auto;' +
    'background:#fffdf8;color:#22201c;border:2px solid #a07a3c;border-radius:12px;padding:14px 16px;font:14px/1.6 -apple-system,"Hiragino Sans",sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.25)');
  box.innerHTML = '';
  const h = document.createElement('div'); h.textContent = cfg.title; h.style.cssText = 'font-weight:700;margin-bottom:6px'; box.appendChild(h);
  for (const line of cfg.lines || []) { const p = document.createElement('div'); p.textContent = line; p.style.margin = '3px 0'; box.appendChild(p); }
  let dateInput = null;
  if (cfg.date) { dateInput = document.createElement('input'); dateInput.type = 'date'; dateInput.value = cfg.date; dateInput.style.cssText = 'margin:8px 8px 0 0;font:inherit'; box.appendChild(dateInput); }
  const row = document.createElement('div'); row.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px'; box.appendChild(row);
  for (const b of cfg.buttons || []) {
    const btn = document.createElement('button'); btn.textContent = b.label;
    btn.style.cssText = 'font:inherit;border-radius:6px;padding:6px 12px;cursor:pointer;border:1px solid #a07a3c;' + (b.primary ? 'background:#a07a3c;color:#fff' : 'background:#fff;color:#22201c');
    btn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); window.postdog({action: b.id, date: dateInput ? dateInput.value : null}); };
    row.appendChild(btn);
  }
  window.__postdogPicking = !!cfg.pick;
}
"""


# ---------------------------------------------------------------------------
# ふつうの Chrome を専用のプロフィールで開く。
# Playwright が開く「自動操作モード」の Chrome では、Google などのログインが「安全でないブラウザ」と断られるため、
# Chrome は自分で（ふつうに）起動し、入力するときだけ「つなぎ口」（リモートデバッグのポート）から外から操作する
CHROME_PATHS = [os.environ.get("POSTDOG_CHROME", ""),
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome")]


def chrome_path() -> str | None:
    return next((p for p in CHROME_PATHS if p and Path(p).exists()), None)


def _port_alive(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


def _running_port() -> int | None:
    """専用の Chrome がもう開いていれば、そのつなぎ口の番号"""
    f = PROFILE / "DevToolsActivePort"
    try:
        port = int(f.read_text().split()[0])
    except Exception:  # noqa: BLE001
        return None
    return port if _port_alive(port) else None


def open_chrome(url: str = "about:blank") -> None:
    """専用の Chrome を開く（開いていれば、新しいタブで url を開く）"""
    PROFILE.mkdir(parents=True, exist_ok=True)
    if _running_port() is None:
        (PROFILE / "DevToolsActivePort").unlink(missing_ok=True)
    extra = os.environ.get("POSTDOG_CHROME_ARGS", "").split()   # 試験用
    subprocess.Popen([chrome_path(), f"--user-data-dir={PROFILE}", "--remote-debugging-port=0",
                      "--no-first-run", "--no-default-browser-check", *extra, url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def chrome_port(timeout: float = 20) -> int:
    port = _running_port()
    if port:
        return port
    open_chrome()
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.5)
        port = _running_port()
        if port:
            return port
    sys.exit("[案内] 専用の Chrome につなげませんでした。開いている専用の Chrome の窓をすべて閉じて（Chrome を選んで ⌘Q）、もう一度押してください")


class BrowserClosed(Exception):
    """専用のブラウザが閉じられた"""


class Session:
    """専用のブラウザと、右下の案内でのやりとり"""

    def __init__(self, pw):
        PROFILE.mkdir(parents=True, exist_ok=True)
        exe = os.environ.get("POSTDOG_BROWSER_EXECUTABLE")
        self.cdp = bool(chrome_path()) and not exe
        if self.cdp:   # ふつうに開いた専用の Chrome に、つなぎ口から入る（ログインが断られない）
            self.browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{chrome_port()}")
            self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        else:          # Chrome が無い：Playwright のブラウザ（Google でのログインは断られることがある）
            opts = dict(user_data_dir=str(PROFILE), headless=AUTO, viewport=None, args=["--start-maximized"])
            self.ctx = pw.chromium.launch_persistent_context(executable_path=exe, **opts) if exe else \
                pw.chromium.launch_persistent_context(**opts)
        self.msg: dict | None = None
        self.ctx.expose_binding("postdog", lambda source, payload: setattr(self, "msg", payload))
        self.ctx.add_init_script(INIT_JS)
        self.page = self.ctx.new_page() if self.cdp or not self.ctx.pages else self.ctx.pages[0]
        if self.cdp:
            self.page.bring_to_front()
        self.overlay: dict | None = None

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        self.settle()

    def settle(self, ms: int = 1500) -> None:
        """ページの読み込み（あとから中身を作るサイトも）が落ち着くまで待つ"""
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:  # noqa: BLE001  ずっと通信しているサイトもあるので、待ちすぎない
            pass
        self.page.wait_for_timeout(ms)

    def closed(self) -> bool:
        try:
            return self.page.is_closed() or not self.ctx.pages
        except Exception:  # noqa: BLE001
            return True

    def js(self, expr: str, arg=None, default=None):
        """ページで JavaScript を動かす。途中でページが切り替わったら、落ち着くのを待ってやり直す"""
        for _ in range(6):
            if self.closed():
                raise BrowserClosed()
            try:
                if not self.page.evaluate("typeof window.__postdogSel === 'function'"):
                    self.page.evaluate(INIT_JS)   # 下準備が入っていないページ（開いたままのタブなど）に入れ直す
                return self.page.evaluate(expr, arg)
            except BrowserClosed:
                raise
            except Exception:  # noqa: BLE001  ページの切り替わりの最中
                if self.closed():
                    raise BrowserClosed()
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:  # noqa: BLE001
                    pass
                self.page.wait_for_timeout(800)
        return default

    def show(self, title: str, lines: list[str] = (), buttons: list[dict] = (), date_value: str | None = None, pick: bool = False) -> None:
        self.overlay = {"title": title, "lines": list(lines), "buttons": list(buttons), "date": date_value, "pick": pick}
        self.js(OVERLAY_JS, self.overlay)

    def wait(self, auto: dict | None = None) -> dict:
        """右下の案内のボタン（またはクリックで教える）を待つ。ブラウザを閉じられたら {'action': 'closed'}"""
        self.msg = None
        if AUTO:
            return auto or {"action": "continue"}
        while self.msg is None:
            try:
                self.page.wait_for_timeout(400)
                if self.overlay and not self.page.evaluate("!!document.getElementById('postdog-overlay')"):
                    self.js(OVERLAY_JS, self.overlay)   # ページが切り替わったら案内を出し直す
            except Exception:  # noqa: BLE001
                if self.closed():
                    return {"action": "closed"}   # ブラウザを閉じた
                # ページの切り替わりの最中：少し待って続ける
        return self.msg

    def ensure_login(self, site: str) -> bool:
        url = self.page.url.lower()
        if any(k in url for k in ("login", "signin", "sign-in", "sign_in", "auth")):
            self.show(f"{site} にログインしてください",
                      ["この専用のブラウザで、いつものアカウントでログインします（次からは覚えています）。",
                       "ログインが終わったら「続ける」を押してください。"],
                      [{"id": "continue", "label": "続ける", "primary": True}, {"id": "stop", "label": "やめる"}])
            if self.wait().get("action") != "continue":
                return False
            self.goto(SITES[site])
        return True

    def close(self) -> None:
        try:
            if self.cdp:   # Chrome とタブは開いたままにする（続けて聴く・いいね・ダウンロードができるように）。案内だけ消す
                if not self.closed():
                    self.page.evaluate("() => { const b = document.getElementById('postdog-overlay'); if (b) b.remove(); window.__postdogPicking = false; }")
            else:
                self.ctx.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 欄を探して入れる
def learned() -> dict:
    return read_json(LEARNED) if LEARNED.exists() else {}


def remember(site: str, key: str, selector: str) -> None:
    d = learned()
    d.setdefault(site, {})[key] = selector
    LEARNED.parent.mkdir(parents=True, exist_ok=True)
    LEARNED.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _visible(loc) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


def find(sess: Session, site: str, f: dict):
    page = sess.page
    sel = learned().get(site, {}).get(f["key"])
    if sel and _visible(page.locator(sel)):
        return page.locator(sel).first
    if f["type"] == "file":
        want = "audio" if f["key"] == "audio" else "image"
        inputs = page.locator("input[type=file]")
        for i in range(inputs.count()):
            acc = (inputs.nth(i).get_attribute("accept") or "").lower()
            if want in acc or (want == "audio" and re.search(r"wav|mp3|flac", acc)) or (want == "image" and re.search(r"jpe?g|png", acc)):
                return inputs.nth(i)
        return None
    for h in f.get("hints", []):
        rx = re.compile(re.escape(h), re.I)
        makers = [lambda: page.get_by_label(rx), lambda: page.get_by_placeholder(rx)]
        if f["type"] == "select":
            makers.append(lambda: page.get_by_role("combobox", name=rx))
        elif f["type"] == "click":
            makers = [lambda: page.get_by_role("button", name=rx), lambda: page.get_by_role("tab", name=rx),
                      lambda: page.get_by_role("radio", name=rx), lambda: page.get_by_role("switch", name=rx)]
        else:
            makers.append(lambda: page.get_by_role("textbox", name=rx))
        for make in makers:
            try:
                loc = make()
                if _visible(loc):
                    return loc.first
            except Exception:  # noqa: BLE001
                continue
    if f.get("largest_textarea"):
        sel = sess.js("""() => { let best = null, size = 0;
            for (const t of document.querySelectorAll('textarea')) { const r = t.getBoundingClientRect();
              if (r.width * r.height > size && r.width > 0) { size = r.width * r.height; best = t; } }
            return best ? window.__postdogSel(best) : null; }""")
        if sel:
            return page.locator(sel).first
    return None


def teach(sess: Session, site: str, f: dict):
    """見つからない欄を、クリックで 1 回だけ教えてもらう"""
    sess.show(f"「{f['label']}」の欄が見つかりません",
              [f"「{f['label']}」の入力欄を 1 回クリックしてください。場所を覚えて、次からは自動で入れます。",
               "（このクリックでボタンが押されることはありません）"],
              [{"id": "skip", "label": "この欄はとばす（手で入れる）"}], pick=True)
    for _ in range(3):
        m = sess.wait({"action": "skip"})
        if m.get("action") != "picked" or not m.get("selector"):
            return None
        loc = sess.page.locator(m["selector"]).first
        if f["type"] in ("text", "date", "select") and not _is_field(loc):
            sess.show("そこは入力欄ではありません", [f"「{f['label']}」の、文字を入れる枠そのものをクリックしてください（ボタンやリンクは選べません）。"],
                      [{"id": "skip", "label": "この欄はとばす（手で入れる）"}], pick=True)
            continue
        remember(site, f["key"], m["selector"])
        return loc
    return None


def _is_field(loc) -> bool:
    """文字を入れる欄か（ボタン・リンク・送信は違う）"""
    try:
        return loc.evaluate("""e => { const t = e.tagName.toLowerCase(), ty = (e.getAttribute('type') || '').toLowerCase();
            if (t === 'textarea' || t === 'select') return true;
            if (t === 'input') return !['button', 'submit', 'reset', 'image', 'file', 'checkbox', 'radio'].includes(ty);
            return e.isContentEditable === true; }""")
    except Exception:  # noqa: BLE001
        return False


def put(sess: Session, loc, f: dict) -> bool:
    """欄に値を入れる。押してはいけないボタンには触らない"""
    v = f.get("value", "")
    try:
        if f["type"] == "file":
            loc.set_input_files(str(v))
            return True
        if f["type"] == "click":
            text = " ".join(filter(None, [loc.inner_text(timeout=2000), loc.get_attribute("aria-label") or ""]))
            if NEVER.search(text):
                return False   # 作成・送信などのボタンは押さない
            before = sess.page.url
            loc.click()
            sess.page.wait_for_timeout(800)
            if sess.page.url.split("?")[0] != before.split("?")[0]:
                sess.goto(before)   # 別のページに移ってしまった：元の画面に戻す
                return False
            return True
        tag = loc.evaluate("e => e.tagName.toLowerCase()")
        if f["type"] == "select" and tag == "select":
            loc.select_option(label=str(v))
            return True
        if f["type"] == "date" and (loc.get_attribute("type") or "") == "date":
            loc.fill(str(v))
            return True
        if not _is_field(loc):
            return False   # 入力欄でないもの（ボタンなど）には触らない
        if tag in ("input", "textarea"):
            loc.fill(str(v))
            return True
        loc.click()   # 文字を入れられる箇所（contenteditable）
        sess.page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
        sess.page.keyboard.insert_text(str(v))
        return True
    except Exception:  # noqa: BLE001
        return False


def fill_fields(sess: Session, site: str, fields: list[dict]) -> tuple[list[str], list[str]]:
    done, manual = [], []
    for f in fields:
        if f.get("value") in (None, ""):
            continue
        try:
            loc = find(sess, site, f)
            if loc is None and f.get("teach", True) and f["type"] != "file":
                loc = teach(sess, site, f)
            ok = loc is not None and put(sess, loc, f)
        except BrowserClosed:
            raise
        except Exception as e:  # noqa: BLE001  この欄だけ手で入れてもらう
            print(f"   － 「{f['label']}」を入れられませんでした（{str(e).splitlines()[0][:120]}）")
            ok = False
        if ok:
            done.append(f["label"])
        elif not f.get("optional"):
            manual.append(f"{f['label']}：{f.get('show', f['value'])}")
    return done, manual


# ---------------------------------------------------------------------------
def suno_fields(song: dict) -> list[dict]:
    return [
        {"key": "custom", "label": "Custom（カスタム）", "type": "click", "value": "on", "hints": ["Custom"], "optional": True, "teach": False},
        {"key": "title", "label": "曲名（Title）", "type": "text", "value": song["title"], "hints": ["Song Title", "Title", "曲名"]},
        {"key": "style", "label": "スタイル（Style of Music）", "type": "text", "value": song.get("style", ""), "hints": ["Style of Music", "Styles", "Style", "スタイル"]},
        {"key": "exclude", "label": "入れないもの（Exclude Styles）", "type": "text", "value": song.get("exclude", ""), "hints": ["Exclude"]},
        {"key": "lyrics", "label": "歌詞（Lyrics）", "type": "text", "value": "" if song.get("instrumental") else song.get("lyrics", ""),
         "hints": ["Lyrics", "歌詞"], "largest_textarea": True, "show": "（歌詞はダッシュボードの「Suno に貼る内容」からコピー）"},
    ]


def wait_for_form(sess: Session, name: str, how: str) -> bool:
    """入力欄のある画面が出るまで待つ。出なければ（ログインの画面・別の画面）、Gon さんに整えてもらう"""
    for _ in range(3):
        try:
            sess.page.wait_for_selector("textarea, input[type=text], [contenteditable=true]", state="visible", timeout=15000)
            sess.settle(500)
            return True
        except BrowserClosed:
            raise
        except Exception:  # noqa: BLE001
            if sess.closed():
                raise BrowserClosed()
        sess.show(f"{name} の入力画面が見つかりません",
                  [f"ログインしていなければログインし、{how}「続ける」を押してください。",
                   "（ログインはこの専用のブラウザが覚えるので、次からは要りません）"],
                  [{"id": "continue", "label": "続ける", "primary": True}, {"id": "stop", "label": "やめる"}])
        if sess.wait({"action": "continue"}).get("action") != "continue":
            return False
    return True


def run_suno(sess: Session, ids: list[str]) -> int:
    import publish
    songs = [_songs.by_id(i) for i in ids] if ids else publish.suno_todo()
    songs = [s for s in songs if s]
    if not songs:
        print("－ Suno で作る番の曲はありません")
        return 0
    for n, song in enumerate(songs, 1):
        sess.goto(SITES["suno"])
        if not sess.ensure_login("suno"):
            return 0
        if not wait_for_form(sess, "Suno", "左のメニューの「Create」を開いてから"):
            return 0
        done, manual = fill_fields(sess, "suno", suno_fields(song))
        print(f"   ○ {song['artist']}「{song['title']}」：入れた {len(done)}・手で {len(manual)}")
        lines = [f"{song['artist']}「{song['title']}」（{n}／{len(songs)} 曲目）",
                 "入れた：" + ("・".join(done) or "なし")]
        if manual:
            lines += ["手で入れてください：", *manual]
        lines += ["① Custom（カスタム）モードになっているか確かめる",
                  "② 内容を確かめて「Create」を押す（押すのは Gon さんだけ）",
                  "③ できた 2 つを聴いて、気に入った方に「いいね」→ その 1 本を SSD の「楽曲情報」にダウンロード（そこから先は自動）"]
        last = n == len(songs)
        sess.show("Suno に入力しました", lines,
                  [{"id": "next", "label": "終わる" if last else "次の曲へ", "primary": True}, {"id": "stop", "label": "ここでやめる"}])
        m = sess.wait({"action": "next"})
        if m.get("action") in ("stop", "closed"):
            break
    return 0


def distrokid_fields(song: dict) -> list[dict]:
    import publish
    f = publish.draft_fields(song)
    folder, audio, cover = publish.publish_folder(song)
    audio_path = Path(f["音源ファイル"])
    cover_path = Path(f["ジャケット"]) if f["ジャケット"] != "（まだ）" else None
    rel = date.today().isoformat() if "いちばん早い" in f["Release date"] else f["Release date"]
    owner = os.environ.get("OWNER_LEGAL_NAME", "")
    return [
        {"key": "artist", "label": "Artist / Band name", "type": "text", "value": f["Artist / Band name"], "hints": ["Artist name", "Band name", "Artist"]},
        {"key": "title", "label": "Song title", "type": "text", "value": f["Song title"], "hints": ["Song title", "Track title", "Title"]},
        {"key": "release", "label": "Release date", "type": "date", "value": rel, "hints": ["Release date"], "teach": False,
         "show": f["Release date"]},
        {"key": "language", "label": "Language", "type": "select", "value": f["Language"], "hints": ["Language"]},
        {"key": "genre1", "label": "Primary genre", "type": "select", "value": f["Primary genre"], "hints": ["Primary genre", "Genre"]},
        {"key": "genre2", "label": "Secondary genre", "type": "select", "value": f["Secondary genre"], "hints": ["Secondary genre"], "teach": False},
        {"key": "songwriter", "label": "Songwriter（本名）", "type": "text", "value": owner, "hints": ["Songwriter", "Real name"], "teach": False},
        {"key": "audio", "label": "音源ファイル", "type": "file", "value": str(audio_path) if audio_path.exists() else "", "show": str(audio_path)},
        {"key": "cover", "label": "ジャケット", "type": "file", "value": str(cover_path) if cover_path and cover_path.exists() else "", "show": str(cover_path)},
    ], f


def run_distrokid(sess: Session, song_id: str) -> int:
    song = _songs.by_id(song_id)
    if song is None:
        sys.exit(f"[エラー] 曲 {song_id} が見つかりません")
    sess.goto(SITES["distrokid"])
    if not sess.ensure_login("distrokid"):
        return 0
    if not wait_for_form(sess, "DistroKid", "登録の画面（Upload）を開いてから"):
        return 0
    fields, draft = distrokid_fields(song)
    done, manual = fill_fields(sess, "distrokid", fields)
    always = [f"Explicit lyrics：{draft['Explicit lyrics']}", f"Instrumental：{draft['Instrumental']}",
              f"AI の申告：{draft['AI の申告']}", f"Featured artist：{draft['Featured artist']}", f"ストア：{draft['ストア']}"]
    print(f"   ○ {song['artist']}「{song['title']}」：入れた {len(done)}・手で {len(manual)}")
    sess.show("DistroKid に下書きを入れました",
              [f"{song['artist']}「{song['title']}」", "入れた：" + ("・".join(done) or "なし"),
               *(["手で入れてください：", *manual] if manual else []), "画面で確かめて選ぶもの：", *always,
               "内容を確かめて、最後の登録ボタンを押してください（押すのは Gon さんだけ）。",
               "押し終わったら、配信日を選んで「登録した」を押すと、記録されます。"],
              [{"id": "registered", "label": "登録した", "primary": True}, {"id": "stop", "label": "閉じる（まだ登録しない）"}],
              date_value=date.today().isoformat())
    m = sess.wait({"action": "stop"})
    if m.get("action") == "registered":
        day = m.get("date") or date.today().isoformat()
        r = subprocess.run([sys.executable, str(S / "publish.py"), "registered", song["id"], "--date", day], capture_output=True, text=True)
        print(r.stdout.strip())
        sess.show("記録しました", [f"DistroKid 登録済み（配信日 {day}）。配信が始まると、ISRC などが自動で入ります。"],
                  [{"id": "stop", "label": "閉じる", "primary": True}])
        sess.wait({"action": "stop"})
    return 0


def open_login(site: str) -> bool:
    """ログイン：自動操作をつながない、ふつうの専用の Chrome で開く（Google でのログインも通る）"""
    if not chrome_path():
        return False
    open_chrome(SITES[site])
    print(f"○ 専用の Chrome で {site} を開きました。いつものアカウントでログインしてください（次からは覚えています）。")
    print("  ログインが終わったら、その窓は開いたままでも、閉じてもかまいません。")
    return True


def run_login(sess: Session, site: str) -> int:
    sess.goto(SITES[site])
    sess.show(f"{site} のログイン", ["この専用のブラウザでログインしてください（次からは覚えています）。", "終わったら「閉じる」を押してください。"],
              [{"id": "stop", "label": "閉じる", "primary": True}])
    sess.wait({"action": "stop"})
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Suno と DistroKid の入力を、システムが専用のブラウザで行う（押すのは Gon さん）")
    ap.add_argument("what", choices=["suno", "distrokid", "login", "forget"])
    ap.add_argument("args", nargs="*", help="suno：曲 ID（省くと全曲）／distrokid：曲 ID／login・forget：suno か distrokid")
    a = ap.parse_args()
    if a.what == "forget":
        d = learned()
        for site in a.args or ["suno", "distrokid"]:
            d.pop(site, None)
        LEARNED.parent.mkdir(parents=True, exist_ok=True)
        LEARNED.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print("○ 覚えた欄の場所を消しました")
        return
    if a.what == "login" and open_login((a.args or ["suno"])[0]):
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("[案内] ブラウザで入力する部品がありません。ターミナルで  python3 -m pip install --user playwright  を 1 回実行してください")
    if a.what == "distrokid" and not a.args:
        sys.exit("[エラー] 曲 ID を付けてください（例 PDR-003）")
    with sync_playwright() as pw:
        sess = Session(pw)
        try:
            if a.what == "suno":
                run_suno(sess, a.args)
            elif a.what == "distrokid":
                run_distrokid(sess, a.args[0])
            else:
                run_login(sess, (a.args or ["suno"])[0])
        except BrowserClosed:
            print("－ ブラウザが閉じられたので終わりました")
        except Exception as e:  # noqa: BLE001
            import traceback
            d = OUT / "browser"
            d.mkdir(parents=True, exist_ok=True)
            (d / "last_error.txt").write_text(f"{sess.page.url if not sess.closed() else ''}\n{traceback.format_exc()}", encoding="utf-8")
            try:
                sess.page.screenshot(path=str(d / "last_error.png"))
            except Exception:  # noqa: BLE001
                pass
            print("[エラー] ブラウザでの入力が途中で止まりました。")
            print(f"  原因：{(str(e).strip().splitlines() or [type(e).__name__])[0][:300]}")
            print(f"  くわしい記録：{d / 'last_error.txt'}（画面の写し：last_error.png）。この 2 つを Claude に見せてください")
            sys.exit(1)
        finally:
            sess.close()


if __name__ == "__main__":
    main()

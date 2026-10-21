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
    初回だけ「login suno」「login distrokid」でログインする（このときは自動操作を一切つながず、つなぎ口も開けない）。
    入力のときにログインしていなければ、システムはいったん離れてログイン用の Chrome を開き直す。入力のあともタブは開いたまま
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
  python3 scripts/browser_fill.py reset                # 専用の Chrome を作り直す（ログインが何度も断られるとき）
  python3 scripts/browser_fill.py parts suno           # いま開いている Suno の画面の部品の一覧を残す（out/browser/suno_parts.txt）

「終わりました」は、作曲画面（登録画面）で、入れた欄を読み返して全部そろっていたときだけ（終了コード 0）。
足りない欄・ログインが要るときは「まだ終わっていません」（終了コード 2）。最後の行の【結果】がダッシュボードに出る

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
NEVER = re.compile(r"create|generate|submit|upload|done|publish|release|distribute|buy|purchase|pay|checkout|subscribe|delete|remove|upgrade|"
                   r"作成|生成|送信|公開|配信|購入|支払|削除|アップグレード", re.I)

# ページに毎回入れる下準備：欄の場所を言葉にする関数と、「クリックで教える」の受け取り
INIT_JS = r"""
(() => {
  // Chrome の自動翻訳を止める（画面の文字が書き換わると、欄を探せず、入れた文字が消えることもある）
  const noTranslate = () => { try {
    const h = document.documentElement; h.setAttribute('translate', 'no'); h.classList.add('notranslate');
    if (document.head && !document.querySelector('meta[name=google][content=notranslate]')) {
      const m = document.createElement('meta'); m.name = 'google'; m.content = 'notranslate'; document.head.appendChild(m); }
  } catch (e) {} };
  noTranslate(); document.addEventListener('DOMContentLoaded', noTranslate);   // ページを読み込む前は html がまだ無いことがあるので、読み込み後にも
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
  box.setAttribute('translate', 'no'); box.classList.add('notranslate');
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
    btn.onclick = (e) => { e.preventDefault(); e.stopPropagation();
      if (b.id === 'dismiss') { box.remove(); window.__postdogPicking = false; return; }   // 案内を消すだけ（システムとはつながっていなくてよい）
      window.postdog({action: b.id, date: dateInput ? dateInput.value : null}); };
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


def set_download_dir() -> Path | None:
    """専用の Chrome のダウンロード先を、決めてある保存先（SSD の「楽曲情報」。無いときは ダウンロード/SUNO楽曲）にする。
    Chrome を閉じているときだけ書ける（開いていると、閉じるときに上書きされる）"""
    try:
        import publish
        dest = publish.inboxes()[0]
        dest.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        return None
    pf = PROFILE / "Default" / "Preferences"
    pf.parent.mkdir(parents=True, exist_ok=True)
    try:
        d = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("download", {}).update({"default_directory": str(dest), "prompt_for_download": False, "directory_upgrade": True})
    d.setdefault("savefile", {})["default_directory"] = str(dest)
    d.setdefault("translate", {})["enabled"] = False   # 自動翻訳を切る（ふだんの Chrome には関係ない）
    pf.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return dest


def open_chrome(url: str = "about:blank", debug: bool = True) -> None:
    """専用の Chrome を開く（開いていれば、新しいタブで url を開く）。debug=False：つなぎ口を開けない、ログイン用"""
    PROFILE.mkdir(parents=True, exist_ok=True)
    if not _chrome_pids():
        set_download_dir()   # 保存先は、Chrome を開く前にだけ設定できる
    if _running_port() is None:
        (PROFILE / "DevToolsActivePort").unlink(missing_ok=True)
    extra = os.environ.get("POSTDOG_CHROME_ARGS", "").split()   # 試験用
    subprocess.Popen([chrome_path(), f"--user-data-dir={PROFILE}", *(["--remote-debugging-port=0"] if debug else []),
                      "--no-first-run", "--no-default-browser-check", "--disable-features=Translate,TranslateUI", *extra, url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _chrome_pids() -> list[int]:
    """専用の Chrome の本体のプロセス（ふだんの Chrome は含まない）"""
    out = subprocess.run(["ps", "-axww", "-o", "pid=,command="], capture_output=True, text=True).stdout
    key = f"--user-data-dir={PROFILE}"
    return [int(line.split(None, 1)[0]) for line in out.splitlines() if key in line and "--type=" not in line]


def quit_chrome() -> None:
    """専用の Chrome をきちんと終わらせる（ログインの記録は残る）"""
    import signal
    for pid in _chrome_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    end = time.time() + 10
    while _chrome_pids() and time.time() < end:
        time.sleep(0.3)
    for pid in _chrome_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    (PROFILE / "DevToolsActivePort").unlink(missing_ok=True)


def chrome_port(timeout: float = 20) -> int:
    port = _running_port()
    if port:
        return port
    if _chrome_pids():   # ログイン用（つなぎ口なし）の Chrome が開いている：入力用に開き直す
        print("   … ログイン用の Chrome を閉じて、入力用に開き直します（ログインは覚えています）")
        quit_chrome()
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


class NeedLogin(Exception):
    """ログインしていない（ログインは、システムがつながっていない Chrome で行う）"""
    def __init__(self, site: str):
        super().__init__(site)
        self.site = site


SIGN_IN = re.compile(r"^\s*(sign in|log in|login|sign up|ログイン|サインイン|新規登録)\s*$", re.I)


def _host(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").lower()


def _base(host: str) -> str:
    return ".".join(host.split(".")[-2:])


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
        self.overlay_host = _host(self.page.url)
        self.js(OVERLAY_JS, self.overlay)

    def wait(self, auto: dict | None = None) -> dict:
        """右下の案内のボタン（またはクリックで教える）を待つ。ブラウザを閉じられたら {'action': 'closed'}"""
        self.msg = None
        if AUTO:
            return auto or {"action": "continue"}
        while self.msg is None:
            try:
                self.page.wait_for_timeout(400)
                if self.overlay and _host(self.page.url) == getattr(self, "overlay_host", "") and \
                        not self.page.evaluate("!!document.getElementById('postdog-overlay')"):
                    self.js(OVERLAY_JS, self.overlay)   # 同じサイトの中でページが切り替わったら案内を出し直す
            except Exception:  # noqa: BLE001
                if self.closed():
                    return {"action": "closed"}   # ブラウザを閉じた
                # ページの切り替わりの最中：少し待って続ける
        return self.msg

    def logged_out(self, site: str) -> bool:
        """ログインしていないように見えるか（ログインの画面に移った・別のサイトに移った・「Sign in」のボタンがある）"""
        url = self.page.url.lower()
        if any(k in url for k in ("login", "signin", "sign-in", "sign_in", "/auth", "accounts.")):
            return True
        if _base(_host(url)) != _base(_host(SITES[site])):
            return True
        for role in ("button", "link"):
            try:
                loc = self.page.get_by_role(role, name=SIGN_IN)
                for i in range(min(loc.count(), 5)):
                    if loc.nth(i).is_visible():
                        return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def ensure_login(self, site: str) -> bool:
        if not self.logged_out(site):
            return True
        if self.cdp:
            raise NeedLogin(site)   # つながったままログインすると Google に断られるので、いったん離れてログイン用の Chrome を開く
        self.show(f"{site} にログインしてください",
                  ["この専用のブラウザで、いつものアカウントでログインします（次からは覚えています）。",
                   "ログインが終わったら「続ける」を押してください。"],
                  [{"id": "continue", "label": "続ける", "primary": True}, {"id": "stop", "label": "やめる"}])
        if self.wait().get("action") != "continue":
            return False
        self.goto(SITES[site])
        return not self.logged_out(site)

    def close(self) -> None:
        try:
            if self.cdp:   # Chrome とタブは開いたままにする（続けて聴く・いいね・ダウンロードができるように）。案内だけ消す
                if not self.closed() and not getattr(self, "keep_overlay", False):
                    self.page.evaluate("() => { const b = document.getElementById('postdog-overlay'); if (b) b.remove(); window.__postdogPicking = false; }")
            else:
                self.ctx.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 欄を探して入れる
# 覚えた欄をしまう名前。Suno は「高度な」（Advanced）の画面に合わせて覚え直す（前の画面で覚えた場所は使わない）
LEARN_AS = {"suno": "suno_advanced"}


def learned() -> dict:
    return read_json(LEARNED) if LEARNED.exists() else {}


def remember(site: str, key: str, selector: str) -> None:
    d = learned()
    d.setdefault(LEARN_AS.get(site, site), {})[key] = selector
    LEARNED.parent.mkdir(parents=True, exist_ok=True)
    LEARNED.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def _visible(loc) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:  # noqa: BLE001
        return False


DESC_JS = """e => [e.getAttribute('placeholder'), e.getAttribute('aria-label'), e.getAttribute('name'), e.id,
                   e.labels ? [...e.labels].map(l => l.innerText).join(' ') : ''].join(' ')"""


def _pick(loc, avoid):
    """候補のうち、見えていて、避けたい欄（例：スタイルを探すときの「除外するスタイル」）ではない最初のもの"""
    try:
        n = min(loc.count(), 8)
    except Exception:  # noqa: BLE001
        return None
    for i in range(n):
        c = loc.nth(i)
        try:
            if not c.is_visible():
                continue
            if avoid and re.search(avoid, c.evaluate(DESC_JS) or "", re.I):
                continue
            return c
        except Exception:  # noqa: BLE001
            continue
    return None


def find(sess: Session, site: str, f: dict):
    page = sess.page
    sel = learned().get(LEARN_AS.get(site, site), {}).get(f["key"])
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
            if f.get("text_ok"):   # 見出しの文字そのもの（「高度な」など）。別のページに移ったら put が元に戻す
                makers.append(lambda: page.get_by_text(re.compile(r"^\s*" + re.escape(h) + r"\s*$", re.I)))
        else:
            makers.append(lambda: page.get_by_role("textbox", name=rx))
        for make in makers:
            try:
                c = _pick(make(), f.get("avoid"))
                if c is not None:
                    return c
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


# ボタンが「選ばれているか」を読む（読めないときは unknown。そのときは押さない：押すと選択が外れることがある）
STATE_CORE = r"""
  const read = x => { if (!x || !x.getAttribute) return null;
    const a = x.getAttribute('aria-pressed') ?? x.getAttribute('aria-checked') ?? x.getAttribute('aria-selected');
    if (a === 'true') return 'on'; if (a === 'false') return 'off';
    const ds = x.getAttribute('data-state'); if (ds) return /^(on|active|checked|selected|open)$/.test(ds) ? 'on' : 'off';
    for (const k of ['data-selected', 'data-active', 'data-checked']) if (x.hasAttribute(k)) return x.getAttribute(k) === 'false' ? 'off' : 'on';
    if (x.matches && x.matches('input[type=radio],input[type=checkbox]')) return x.checked ? 'on' : 'off';
    const cls = String(x.className && x.className.baseVal !== undefined ? x.className.baseVal : x.className || '');
    if (/(^|[\s_-])(active|selected|checked|is-on|on)([\s_-]|$)/i.test(cls)) return 'on';
    return null; };
  const holder = e => { if (read(e)) return e;
    // 文字のボタンの中や横にスイッチがある作り（そのスイッチが状態を持つ）
    for (let up = e, i = 0; i < 3 && up; i++, up = up.parentElement) {
      const sw = [...up.querySelectorAll('[role=switch],input[type=checkbox],input[type=radio],[aria-checked],[aria-pressed],[data-state]')].filter(x => x !== e);
      if (sw.length === 1 && read(sw[0])) return sw[0];
      if (sw.length > 1) break;
    }
    return null; };"""
STATE_JS = "e => {" + STATE_CORE + " const h = holder(e); return h ? read(h) : 'unknown'; }"
HOLDER_JS = "e => {" + STATE_CORE + " const h = holder(e); return h && h !== e ? window.__postdogSel(h) : null; }"
NEAR_JS = r"""([hint, sel, unique]) => { const want = hint.trim().toLowerCase();
    for (const el of document.querySelectorAll('label,span,div,p,h3,h4,legend')) {
      if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().toLowerCase() === want)) continue;
      let up = el;
      for (let i = 0; i < 5 && up; i++, up = up.parentElement) {
        if (i > 1 && (up.innerText || '').length > 100) break;   // ほかの設定まで入った大きなかたまり：ここより上は探さない
        const all = [...up.querySelectorAll(sel)].filter(x => x.offsetParent !== null);
        if (!all.length) continue;
        // ほかの設定の部品まで入った大きなかたまりまで上がってしまったら、取り違えるので使わない
        if (unique && all.length > 1) break;
        return window.__postdogSel(all[0]);
      } }
    return null; }"""
SLIDER_SEL = "[role=slider],input[type=range]"
TIME_SEL = "input:not([type=range]):not([type=checkbox]):not([type=radio]):not([type=file])"


def find_slider(sess: Session, site: str, f: dict):
    page = sess.page
    sel = learned().get(LEARN_AS.get(site, site), {}).get(f["key"])
    if sel and _visible(page.locator(sel)):
        return page.locator(sel).first
    for h in f.get("hints", []):
        rx = re.compile(re.escape(h), re.I)
        for make in (lambda: page.get_by_role("slider", name=rx), lambda: page.get_by_label(rx)):
            try:
                c = _pick(make(), None)
                if c is not None and c.evaluate("e => e.getAttribute('role') === 'slider' || (e.tagName === 'INPUT' && e.type === 'range')"):
                    return c
            except Exception:  # noqa: BLE001
                continue
        sel = sess.js(NEAR_JS, [h, SLIDER_SEL, True])   # 見出しの文字の近くにあるスライダー
        if sel and _visible(page.locator(sel)):
            return page.locator(sel).first
    return None


def _slider_value(loc) -> float:
    return float(loc.evaluate("e => e.getAttribute('aria-valuenow') ?? e.value"))


def _slider_range(loc) -> tuple[float, float]:
    return tuple(loc.evaluate("""e => { const g = (a, b, d) => { const v = e.getAttribute(a) ?? e[b];
        return (v === null || v === undefined || v === '') ? d : Number(v); };
        return [g('aria-valuemin', 'min', 0), g('aria-valuemax', 'max', 100)]; }"""))


def slider_target(loc, f: dict) -> tuple[float, float]:
    """設定の値（% ・段階・秒）を、そのスライダーの目盛りに直す。(目標, 許す誤差)"""
    mn, mx = _slider_range(loc)
    v, span = float(f["value"]), (mx - mn) or 1
    if f.get("scale") == "raw":
        return v, 0.5
    if f.get("scale") == "seconds":
        if mx >= 300:
            return v, 5                     # 目盛りが秒
        if mx <= 6.5:
            return v / 60, 5 / 60           # 目盛りが分
        return mn + span * (v - 10) / 350, span * 5 / 350
    return mn + span * v / 100, max(span * 0.02, 0.5)   # %


def set_slider(sess: Session, loc, target: float, tol: float = 2) -> bool:
    """スライダーを target（目盛り）に合わせる。矢印キーで 1 目盛りずつ動かし、合ったか読み返す"""
    if loc.evaluate("e => e.tagName === 'INPUT'"):
        loc.evaluate("""(e, v) => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            set.call(e, String(v)); e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); }""", target)
    else:
        loc.focus()
        for _ in range(400):
            cur = _slider_value(loc)
            if abs(cur - target) <= min(tol, 0.5):   # ぴったりをねらう（確かめるときは誤差 tol まで許す）
                break
            key = "ArrowRight" if cur < target else "ArrowLeft"
            sess.page.keyboard.press(key)
            new = _slider_value(loc)
            if new == cur:
                break   # 動かない
            if (cur < target) != (new < target):   # 行き過ぎた：近いほうで止める
                if abs(new - target) > abs(cur - target):
                    sess.page.keyboard.press("ArrowLeft" if key == "ArrowRight" else "ArrowRight")
                break
    return abs(_slider_value(loc) - target) <= tol


def _scope(sess: Session, f: dict):
    """f['near']（見出しの言葉）があれば、その近くのかたまりの中だけを探す"""
    for h in f.get("near") or []:
        sel = sess.js(NEAR_JS, [h, "button,[role=radio],[role=tab],[role=switch],[role=checkbox]", False])
        if sel:
            box = sess.js("""(sel) => { const b = document.querySelector(sel); let up = b;
                for (let i = 0; i < 4 && up.parentElement; i++) { up = up.parentElement;
                  if (up.querySelectorAll('button,[role=radio],[role=tab],[role=switch],[role=checkbox]').length >= 2) break; }
                return window.__postdogSel(up); }""", sel)
            if box:
                return sess.page.locator(box).first
    return sess.page


def _sec(text: str) -> int | None:
    m = re.match(r"^\s*(\d+):(\d{2})\s*$", str(text or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else (int(text) if str(text or "").strip().isdigit() else None)


def put_slider(sess: Session, site: str, f: dict) -> bool:
    """スライダーの欄に入れる。スライダーが無ければ：段階のボタン（バラエティ 0〜4）／ mm:ss の欄（間隔）"""
    loc = find_slider(sess, site, f)
    if loc is not None:
        target, tol = slider_target(loc, f)
        sess.filled[f["key"]] = ("slider", loc)
        return set_slider(sess, loc, target, tol)
    if f.get("scale") == "raw":   # 0〜4 のボタン
        b = _option(sess, [str(int(f["value"]))], {"near": f["hints"]})
        if b is not None:
            if b.evaluate(STATE_JS) != "on":
                b.click()
                sess.page.wait_for_timeout(300)
            sess.filled[f["key"]] = ("button", b)
            return b.evaluate(STATE_JS) in ("on", "unknown")
    if f.get("scale") == "seconds":   # 「3:10」と書く欄
        for h in f["hints"]:
            sel = sess.js(NEAR_JS, [h, TIME_SEL, True])
            if sel:
                box = sess.page.locator(sel).first
                v = int(f["value"])
                box.fill(f"{v // 60}:{v % 60:02d}" if ":" in (box.get_attribute("placeholder") or box.input_value() or ":") else str(v))
                box.press("Tab")
                sess.filled[f["key"]] = ("time", box)
                return slider_ok(sess, f)
    return False


def slider_ok(sess: Session, f: dict) -> bool:
    kind, loc = getattr(sess, "filled", {}).get(f["key"], (None, None))
    if loc is None:
        return False
    if kind == "slider":
        target, tol = slider_target(loc, f)
        return abs(_slider_value(loc) - target) <= tol
    if kind == "button":
        return loc.evaluate(STATE_JS) in ("on", "unknown")
    got = _sec(loc.input_value())
    return got is not None and abs(got - int(f["value"])) <= 5


def _option(sess: Session, hints: list[str], f: dict | None = None):
    page = _scope(sess, f) if f else sess.page
    for h in hints:
        rx = re.compile(r"^\s*" + re.escape(h) + r"\s*$", re.I)
        for role in ("button", "radio", "tab", "checkbox", "switch", "option"):
            c = _pick(page.get_by_role(role, name=rx), None)
            if c is not None:
                return c
    if f is not None and len(f.get("options", {})) == 1:   # オン・オフのスイッチ：名前が無くても、見出しの横のものを使う
        for h in hints:
            sel = sess.js(NEAR_JS, [h, "[role=switch],input[type=checkbox],button[aria-checked],button[aria-pressed]", True])
            if sel:
                return sess.page.locator(sel).first
    return None


def _states(sess: Session, f: dict) -> dict:
    """選択肢ごとの (部品, 状態)。1 つだけ「選ばれている」と読めたら、読めないほかの選択肢は「選ばれていない」とみなす"""
    got = {}
    for opt, hints in f["options"].items():
        loc = _option(sess, hints, f)
        if loc is not None:
            got[opt] = [loc, loc.evaluate(STATE_JS)]
    if len(got) >= 2 and sum(v[1] == "on" for v in got.values()) == 1:
        for v in got.values():
            if v[1] == "unknown":
                v[1] = "off"
    return got


def choice_state(sess: Session, f: dict) -> bool | None:
    """「どちらか選ぶ」が希望どおりか。True／False／None（読めない・見つからない）"""
    got = _states(sess, f)
    for opt, (loc, st) in got.items():
        if st == "unknown":
            return None
        if (st == "on") != (opt == f["value"]):
            return False
    return True if got or f["value"] == "none" else None


def set_choice(sess: Session, f: dict) -> bool | None:
    """希望どおりに選ぶ（ボーカルの性別など）。状態が読めないボタンは押さない"""
    got = _states(sess, f)
    if any(st == "unknown" for _, st in got.values()):
        return None
    # 選ぶほうを先に押す（2 つのうち 1 つを選ぶ作りなら、それだけで切り替わる）
    for opt, (loc, st) in sorted(got.items(), key=lambda kv: kv[0] != f["value"]):
        st = _states(sess, f).get(opt, [loc, st])[1]
        if (st == "on") != (opt == f["value"]):
            text = " ".join(filter(None, [loc.inner_text(timeout=2000), loc.get_attribute("aria-label") or ""]))
            if NEVER.search(text):
                return False
            h = loc.evaluate(HOLDER_JS)   # 状態を持っているのが横のスイッチなら、そちらを押す
            (sess.page.locator(h).first if h else loc).click()
            sess.page.wait_for_timeout(400)
    return choice_state(sess, f)


def teach(sess: Session, site: str, f: dict):
    """見つからない欄を、クリックで 1 回だけ教えてもらう"""
    sess.show(f"「{f['label']}」の欄が見つかりません",
              [f"「{f['label']}」の入力欄を 1 回クリックしてください。場所を覚えて、次からは自動で入れます。",
               *([f["tip"]] if f.get("tip") else []),
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
            inner = loc.locator("textarea, input:not([type=hidden]):not([type=file]), [contenteditable=true], [contenteditable='']").first
            if inner.count() and inner.is_visible() and _is_field(inner):
                loc, tag = inner, inner.evaluate("e => e.tagName.toLowerCase()")   # 外側の枠：中の欄に入れる
            else:
                return False   # 入力欄でないもの（ボタンなど）には触らない
        sess.last_loc = loc
        if tag in ("input", "textarea"):
            loc.fill(str(v))
            sess.page.wait_for_timeout(300)
            if _norm(_read(loc)) == _norm(v):
                return True
        # 入れた文字が残らない作り（画面が自分で文字を管理している）：クリックして、全部選んで、キーボードで打つ
        loc.click()
        sess.page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
        sess.page.keyboard.press("Backspace")
        sess.page.keyboard.insert_text(str(v))
        sess.page.wait_for_timeout(300)
        return True
    except Exception:  # noqa: BLE001
        return False


def _read(loc) -> str:
    return loc.evaluate("""e => e.tagName === 'SELECT' ? ((e.selectedOptions[0] || {}).text || e.value)
                           : (e.isContentEditable ? e.innerText : (e.value ?? ''))""")


def fill_fields(sess: Session, site: str, fields: list[dict]) -> tuple[list[str], list[str]]:
    done, manual = [], []
    sess.filled = {}
    for f in fields:
        if f.get("value") in (None, ""):
            continue
        if f.get("only_if_missing"):
            keys = f["only_if_missing"] if isinstance(f["only_if_missing"], list) else [f["only_if_missing"]]
            targets = [x for x in fields if x["key"] in keys and x.get("value") not in (None, "")]
            if not any((find_slider(sess, site, x) if x["type"] == "slider" else
                        _option(sess, sum(x["options"].values(), []), x) if x["type"] == "choice" else find(sess, site, x)) is None
                       for x in targets):
                continue   # 中の欄が全部見えている＝もう開いている（押すと閉じてしまう）
        if f["type"] == "choice":
            try:
                r = set_choice(sess, f)
            except BrowserClosed:
                raise
            except Exception:  # noqa: BLE001
                r = False
            if r:
                done.append(f["label"])
            elif not (r is None and f.get("quiet_unknown")):
                manual.append(f"{f['label']}：{f.get('show', f['value'])}" + ("（状態が読めないので、手で選んでください）" if r is None else ""))
            continue
        if f["type"] == "slider":
            try:
                ok = put_slider(sess, site, f)
            except BrowserClosed:
                raise
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                done.append(f["label"])
            else:
                manual.append(f"{f['label']}：{f['show']}（手で合わせてください）")
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
            sess.filled[f["key"]] = getattr(sess, "last_loc", None) or loc
            sess.last_loc = None
        elif not f.get("optional"):
            manual.append(f"{f['label']}：{f.get('show', f['value'])}")
    return done, manual


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip().lower()


def verify(sess: Session, fields: list[dict]) -> list[str]:
    """入れた欄の中身を読み返して、入れたかった値と同じかを確かめる。合わない欄の一覧（空なら全部入っている）"""
    sess.page.wait_for_timeout(700)   # サイトが値を消したり書き換えたりしないかを少し待ってから見る
    bad = []
    sess.unsure = []
    for f in fields:
        if f.get("value") in (None, "") or f["type"] == "click":
            continue
        if f["type"] == "choice":
            r = choice_state(sess, f)
            if r is False:
                bad.append(f"{f['label']}：{f.get('show', f['value'])}になっていません")
            elif r is None and not f.get("quiet_unknown"):
                sess.unsure.append(f"{f['label']}：{f.get('show', f['value'])}（画面で確かめてください）")
            continue
        if f["type"] == "slider":
            try:
                ok = slider_ok(sess, f)
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                bad.append(f"{f['label']}：{f['show']} になっていません")
            continue
        loc = getattr(sess, "filled", {}).get(f["key"])
        if loc is None:
            bad.append(f"{f['label']}：入っていません")
            continue
        try:
            if f["type"] == "file":
                got = loc.evaluate("e => [...(e.files || [])].map(x => x.name).join(',')")
            else:
                got = loc.evaluate("""e => e.tagName === 'SELECT' ? ((e.selectedOptions[0] || {}).text || e.value)
                                      : (e.isContentEditable ? e.innerText : e.value)""")
        except Exception:  # noqa: BLE001
            bad.append(f"{f['label']}：画面が変わって確かめられませんでした")
            continue
        want, g = _norm(f["value"]), _norm(got)
        if f["type"] == "file":
            ok = Path(str(f["value"])).name in str(got)
        elif f["type"] == "date":
            ok = bool(g)
        elif f["type"] == "select":
            ok = g == want or want in g
        else:
            ok = g == want
        if not ok:
            bad.append(f"{f['label']}：途中までしか入っていません（文字数の上限かもしれません）" if g and want.startswith(g)
                       else f"{f['label']}：入っていません")
    return bad


def on_page(sess: Session, site: str) -> bool:
    """いまの画面が、入力するはずの画面か（Suno なら作曲画面）"""
    from urllib.parse import urlparse
    want, now = urlparse(SITES[site]), urlparse(sess.page.url)
    return _base(now.hostname or "") == _base(want.hostname or "") and now.path.rstrip("/").startswith(want.path.rstrip("/"))


# ---------------------------------------------------------------------------
def suno_fields(song: dict) -> list[dict]:
    """Suno の作曲画面の「高度な」の欄。値は設計図（suno_design.py）から。Gon さんの一覧の順
    （歌詞・スタイル・除外するスタイル・声の性別・間隔・Max mode・音量の大きさ・スタイルの影響度・バラエティ・あなたの好み・曲名）"""
    import suno_design
    d = suno_design.design(song)
    tip = "欄が見えないときは、画面の上の「高度な」を選び、「その他のオプション」を開いてからクリックしてください。"
    onoff = lambda v: "オン" if v else "オフ"  # noqa: E731
    fields = [
        {"key": "mode", "label": "「高度な」モード", "type": "click", "value": "on", "optional": True, "teach": False, "text_ok": True,
         "hints": ["高度な", "高度", "Advanced"]},
        {"key": "lyrics", "label": "歌詞", "type": "text", "value": d["lyrics"],
         "hints": ["Lyrics", "歌詞", "Write some lyrics", "Write your own lyrics"], "avoid": r"style|スタイル|除外|exclude|title|タイトル|曲名",
         "largest_textarea": True, "tip": tip, "show": "（歌詞はダッシュボードの「Suno に貼る内容」からコピー）"},
        # スタイルは「カスタム」（文字で書く）。選べる候補は、文字に同じ言葉が入っているので押さない
        {"key": "style_mode", "label": "スタイルを「カスタム」に", "type": "choice", "value": "custom", "show": "カスタム", "quiet_unknown": True,
         "near": ["スタイル", "Styles", "Style"], "options": {"custom": ["カスタム", "Custom"], "select": ["選択", "Select"]}},
        {"key": "style", "label": "スタイル", "type": "text", "value": d["style"],
         "hints": ["Style of Music", "Styles", "Style", "スタイル", "音楽のスタイル"], "avoid": r"exclude|除外|lyrics|歌詞", "tip": tip},
        {"key": "more", "label": "その他のオプション", "type": "click", "value": "on", "optional": True, "teach": False, "text_ok": True,
         "only_if_missing": ["exclude", "gender", "length", "max_mode", "loudness", "style_influence", "variety", "your_taste"],
         "hints": ["その他のオプション", "詳細オプション", "More Options", "More options", "オプション"]},
        {"key": "exclude", "label": "除外するスタイル", "type": "text", "value": d["exclude"],
         "hints": ["除外するスタイル", "Exclude styles", "Exclude", "除外"], "tip": tip},
    ]
    if d["vocal_gender"] != "unset":
        g = d["vocal_gender"] or "none"
        fields.append({"key": "gender", "label": "声の性別", "type": "choice", "value": g, "near": ["声の性別", "ボーカルの性別", "Vocal Gender", "Gender", "Vocals"],
                       "show": _songs.GENDER_JA.get(d["vocal_gender"], g), "options": {"male": ["男", "男性", "Male"], "female": ["女", "女性", "Female"]}})
    hints_len = ["間隔", "長さ", "曲の長さ", "Duration", "Length", "Song Length", "Song length", "Interval", "Timing"]
    fields.append({"key": "length_mode", "label": "間隔（自動・カスタム）", "type": "choice", "near": hints_len,
                   "value": "custom" if d["length_sec"] else "auto", "show": "カスタム" if d["length_sec"] else "自動",
                   "options": {"custom": ["カスタム", "Custom"], "auto": ["自動", "Auto"]}})
    if d["length_sec"]:
        fields.append({"key": "length", "label": "間隔（曲の長さ）", "type": "slider", "scale": "seconds", "value": d["length_sec"],
                       "show": _songs.fmt_len(d["length_sec"]).replace("カスタム ", ""), "hints": hints_len})
    if d.get("max_mode") is not None:
        fields.append({"key": "max_mode", "label": "Max mode", "type": "choice", "value": "on" if d["max_mode"] else "none",
                       "show": onoff(d["max_mode"]), "options": {"on": ["Max mode", "Max モード", "MAX モード", "Max Mode"]}})
    if d.get("loudness") is not None:
        fields.append({"key": "loudness", "label": "音量の大きさ", "type": "slider", "scale": "percent", "value": d["loudness"],
                       "show": f"{d['loudness']}%", "hints": ["音量の大きさ", "音量", "Loudness", "Volume", "Audio Loudness", "Level"]})
    if d.get("style_influence") is not None:
        fields.append({"key": "style_influence", "label": "スタイルの影響度", "type": "slider", "scale": "percent", "value": d["style_influence"],
                       "show": f"{d['style_influence']}%", "hints": ["スタイルの影響度", "スタイルの影響", "Style Influence", "Style influence", "Style Strength"]})
    if d.get("variety") is not None:
        fields.append({"key": "variety", "label": "バラエティ", "type": "slider", "scale": "raw", "value": d["variety"],
                       "show": f"{d['variety']}（0〜4）", "hints": ["バラエティ", "Variety", "Variation", "Variations"]})
    if d.get("your_taste") is not None:
        fields.append({"key": "your_taste", "label": "あなたの好み", "type": "choice", "value": "on" if d["your_taste"] else "none",
                       "show": onoff(d["your_taste"]), "options": {"on": ["あなたの好み", "Your taste", "Your Taste", "My Taste", "Taste", "Personalize", "Personalized", "好み"]}})
    fields.append({"key": "title", "label": "曲名", "type": "text", "value": d["title"],
                   "hints": ["曲名", "曲のタイトル", "Song Title", "Title", "タイトル"], "avoid": r"lyrics|歌詞|style|スタイル", "tip": tip})
    return fields


PARTS_JS = r"""() => {
  const out = [], vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const cut = (t, n) => String(t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const lab = e => { // 近くの見出しの文字
    for (let up = e.parentElement, i = 0; i < 3 && up; i++, up = up.parentElement) {
      const t = [...up.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim()
        || [...up.children].filter(c => !['SCRIPT', 'STYLE'].includes(c.tagName) && !c.querySelector('input,textarea,button,[role]') && c !== e).map(c => c.innerText || '').join(' ').trim();
      if (t) return cut(t, 30); }
    return ''; };
  for (const e of document.querySelectorAll('input,textarea,select,button,[role],[contenteditable],[aria-checked],[aria-pressed],[data-state]')) {
    if (!vis(e) || e.closest('#postdog-overlay')) continue;
    const role = e.getAttribute('role') || '', t = e.tagName.toLowerCase();
    if (!['input', 'textarea', 'select', 'button'].includes(t) && !/button|switch|slider|radio|tab|checkbox|textbox|combobox|option|menuitem/.test(role) && !e.isContentEditable) continue;
    const at = ['type', 'aria-label', 'aria-checked', 'aria-pressed', 'aria-selected', 'aria-valuenow', 'aria-valuemin', 'aria-valuemax',
                'data-state', 'placeholder', 'name', 'data-testid'].map(a => e.getAttribute(a) !== null ? `${a}=${cut(e.getAttribute(a), 40)}` : '').filter(Boolean);
    const val = (t === 'input' || t === 'textarea') ? ` 中身=${String(e.value || '').length}文字` : (e.isContentEditable ? ` 中身=${(e.innerText || '').length}文字` : '');
    out.push(`${Math.round(e.getBoundingClientRect().top)}\t${t}${role ? '[' + role + ']' : ''}${e.isContentEditable ? '[編集可]' : ''}\t「${cut(e.innerText || e.value || '', 30)}」\t${at.join(' ')}${val}\tclass=${cut(e.className && e.className.baseVal !== undefined ? '' : e.className, 50)}\t見出し=${lab(e)}`);
    if (out.length >= 300) break;
  }
  return out.sort((a, b) => parseInt(a) - parseInt(b)).join('\n'); }"""


def save_parts(sess: Session, site: str) -> Path | None:
    """画面の部品の一覧（欄・ボタン・スイッチ・スライダーの名前と状態。入れた歌詞などの中身は文字数だけ）と画面の写しを残す"""
    d = OUT / "browser"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{site}_parts.txt"
    try:
        body = sess.js(PARTS_JS, None, default="")
        p.write_text(f"画面：{sess.page.url}\n（上からの位置\t部品\t文字\t目印\tclass\t見出し）\n{body}\n", encoding="utf-8")
        sess.page.screenshot(path=str(d / f"{site}_screen.png"), full_page=True)
        return p
    except Exception:  # noqa: BLE001
        return p if p.exists() else None


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
    """全部の曲で、作曲画面のすべての欄に入ったことを確かめられたら 0、そうでなければ 2"""
    import publish
    songs = [_songs.by_id(i) for i in ids] if ids else publish.suno_todo()
    songs = [s for s in songs if s]
    if not songs:
        print("【結果】Suno で作る番の曲はありません")
        return 0
    ok_songs, problems, unsure_all = [], [], []
    for n, song in enumerate(songs, 1):
        sess.goto(SITES["suno"])
        if not sess.ensure_login("suno"):
            problems.append("Suno にログインできていません")
            break
        if not wait_for_form(sess, "Suno", "左のメニューの「Create」を開いてから"):
            problems.append(f"{song['artist']}「{song['title']}」：作曲画面が開けませんでした")
            break
        if not sess.ensure_login("suno"):   # 画面ができてから「Sign in」が出るサイトもあるので、もう一度
            problems.append("Suno にログインできていません")
            break
        fields = suno_fields(song)
        done, manual = fill_fields(sess, "suno", fields)
        bad = verify(sess, fields)
        if not on_page(sess, "suno"):
            bad.insert(0, f"Suno の作曲画面ではありません（いまの画面：{sess.page.url}）")
        name = f"{song['artist']}「{song['title']}」"
        if bad or sess.unsure:
            parts = save_parts(sess, "suno")
            if parts:
                print(f"   … 画面の部品の一覧を残しました：{parts}（画面の写し：{parts.with_name('suno_screen.png')}）。"
                      "この 2 つを Claude に見せると、本物の画面に合わせて直せます")
        if bad:
            problems += [f"{name}の{x}" for x in bad]
            print(f"   ✕ {name}：入っていない欄があります → " + "／".join(bad))
        else:
            ok_songs.append(name)
            print(f"   ✓ {name}：作曲画面の欄を読み返して、全部入っていることを確かめました（" + "・".join(done) + "）")
        import suno_design
        dz = suno_design.design(song)
        sc_ok, sc_n = suno_design.score(dz)
        lines = [f"{name}（{n}／{len(songs)} 曲目）",
                 f"イメージの写し具合：{sc_ok}／{sc_n}（設定書の特徴語・禁止事項・毎曲の癖が、Suno の欄に入っているか）",
                 *[f"⚠ {k}：{t}" for k, st, t in dz["checks"] if st == "warn"]]
        if bad:
            lines += ["⚠ 次の欄が入っていません。手で入れてください：", *bad]
            if manual:
                lines += ["入れる内容：", *manual]
        else:
            lines += ["✓ 全部の欄に入っていることを確かめました：" + "・".join(done)]
        if sess.unsure:
            lines += ["目で確かめてください（画面から状態が読めませんでした）：", *sess.unsure]
            unsure_all.extend(f"{name}の{x}" for x in sess.unsure)
        try:
            import publish
            lines += [f"保存先：{publish.inboxes()[0]}（この Chrome でダウンロードすると、ここに入ります）"]
        except Exception:  # noqa: BLE001
            pass
        lines += ["① 画面の上が「高度な」（Advanced）になっているか確かめる",
                  "② 内容を確かめて「Create」を押す（押すのは Gon さんだけ）",
                  "③ できた 2 つを聴いて、気に入った方に「いいね」→ その 1 本を SSD の「楽曲情報」にダウンロード（そこから先は自動）"]
        if n == len(songs):   # 最後の曲：案内を残したまま終わる（Create は Gon さんが押す）
            sess.keep_overlay = True
            sess.show("Suno に入力しました" if not bad else "Suno への入力が足りません", lines, [{"id": "dismiss", "label": "この案内を消す"}])
            break
        sess.show("Suno に入力しました" if not bad else "Suno への入力が足りません", lines,
                  [{"id": "next", "label": "次の曲へ", "primary": True}, {"id": "stop", "label": "ここでやめる"}])
        m = sess.wait({"action": "next"})
        if m.get("action") in ("stop", "closed"):
            break
    if not problems and len(ok_songs) == len(songs):
        extra = f"（ただし {'／'.join(unsure_all)}）" if unsure_all else ""
        print(f"【結果】Suno の作曲画面に{'、' + str(len(songs)) + ' 曲とも' if len(songs) > 1 else ''}入力し、全部の欄に入っていることを確かめました{extra}。開いている Chrome で「作成」（Create）を押してください")
        return 0
    msg = "／".join(problems[:4]) or f"{len(songs)} 曲のうち {len(ok_songs)} 曲で止めました"
    print(f"【結果】まだ終わっていません：{msg}")
    return 2


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
        print("【結果】まだ終わっていません：DistroKid にログインできていません")
        return 2
    if not wait_for_form(sess, "DistroKid", "登録の画面（Upload）を開いてから"):
        print("【結果】まだ終わっていません：DistroKid の登録画面が開けませんでした")
        return 2
    fields, draft = distrokid_fields(song)
    done, manual = fill_fields(sess, "distrokid", fields)
    bad = verify(sess, fields)
    always = [f"Explicit lyrics：{draft['Explicit lyrics']}", f"Instrumental：{draft['Instrumental']}",
              f"AI の申告：{draft['AI の申告']}", f"Featured artist：{draft['Featured artist']}", f"ストア：{draft['ストア']}"]
    print(f"   ○ {song['artist']}「{song['title']}」：入れた {len(done)}・手で {len(manual)}")
    sess.show("DistroKid に下書きを入れました",
              [f"{song['artist']}「{song['title']}」",
               ("✓ 読み返して確かめました：" if not bad else "入れた：") + ("・".join(done) or "なし"),
               *(["⚠ 入っていない欄：", *bad] if bad else []),
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
        print(f"【結果】DistroKid に登録したことを記録しました（配信日 {day}）")
        return 0
    if bad:
        print("【結果】まだ終わっていません：DistroKid の入っていない欄 → " + "／".join(bad))
    else:
        print("【結果】下書きは全部の欄に入りましたが、「登録した」はまだ押されていません")
    return 2


def open_login(site: str) -> bool:
    """ログイン：自動操作をつながない、ふつうの専用の Chrome で開く（Google でのログインも通る）"""
    if not chrome_path():
        return False
    if _running_port():   # 入力用（つなぎ口あり）の Chrome が開いている：閉じてから、ログイン用に開く
        quit_chrome()
    open_chrome(SITES[site], debug=False)
    print(f"○ 専用の Chrome で {site} を開きました。システムはこの Chrome にはつながっていません。")
    print("  いつものアカウント（Google など）でログインしてください。次からは覚えています。")
    print(f"【結果】ログイン用に Chrome を開きました。ログインが済んだら「{'Suno' if site == 'suno' else 'DistroKid'} に入力する」を押してください"
          "（ログインできたかどうかは、ダッシュボードからは見えません）")
    return True


def run_login(sess: Session, site: str) -> int:
    """（Chrome が無いとき）Playwright のブラウザでログインする"""
    sess.goto(SITES[site])
    sess.show(f"{site} のログイン", ["この専用のブラウザでログインしてください（次からは覚えています）。", "終わったら「閉じる」を押してください。"],
              [{"id": "stop", "label": "閉じる", "primary": True}])
    sess.wait({"action": "stop"})
    print("【結果】ログインの画面を閉じました（ログインできたかは、次に入力するときに確かめます）")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Suno と DistroKid の入力を、システムが専用のブラウザで行う（押すのは Gon さん）")
    ap.add_argument("what", choices=["suno", "distrokid", "login", "forget", "reset", "parts"])
    ap.add_argument("args", nargs="*", help="suno：曲 ID（省くと全曲）／distrokid：曲 ID／login・forget：suno か distrokid")
    a = ap.parse_args()
    if a.what == "forget":
        d = learned()
        for site in a.args or ["suno", "distrokid"]:
            d.pop(site, None)
            d.pop(LEARN_AS.get(site, site), None)
        LEARNED.parent.mkdir(parents=True, exist_ok=True)
        LEARNED.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print("○ 覚えた欄の場所を消しました")
        return
    if a.what == "reset":   # 専用の Chrome の記録（ログインなど）を作り直す。前のものは .old として残す
        quit_chrome()
        if PROFILE.exists():
            old = PROFILE.with_name(PROFILE.name + ".old")
            if old.exists():
                import shutil
                shutil.rmtree(old, ignore_errors=True)
            PROFILE.rename(old)
        print("【結果】専用の Chrome を作り直しました。もう一度「Suno にログイン（初回だけ）」からログインしてください")
        return
    if a.what == "login" and open_login((a.args or ["suno"])[0]):
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("[案内] ブラウザで入力する部品がありません。ターミナルで  python3 -m pip install --user playwright  を 1 回実行してください")
    if a.what == "distrokid" and not a.args:
        sys.exit("[エラー] 曲 ID を付けてください（例 PDR-003）")
    code, need = 0, None
    with sync_playwright() as pw:
        sess = Session(pw)
        try:
            if a.what == "suno":
                code = run_suno(sess, a.args)
            elif a.what == "distrokid":
                code = run_distrokid(sess, a.args[0])
            elif a.what == "parts":   # いま開いている Suno（または DistroKid）の画面の部品の一覧
                site = (a.args or ["suno"])[0]
                pg = next((x for x in reversed(sess.ctx.pages) if _base(_host(x.url)) == _base(_host(SITES[site]))), None)
                if pg is None:
                    sess.goto(SITES[site])
                else:
                    sess.page = pg
                p = save_parts(sess, site)
                if p and p.exists():
                    print(p.read_text(encoding="utf-8"))   # ダッシュボードのログからもコピーできるように
                print(f"【結果】画面の部品の一覧を残しました：{p}（ダッシュボードのログをコピーして Claude に貼れます）")
            else:
                code = run_login(sess, (a.args or ["suno"])[0])
        except NeedLogin as e:
            need = e.site
        except BrowserClosed:
            print("【結果】まだ終わっていません：ブラウザが閉じられました")
            code = 2
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
            print(f"【結果】エラーで止まりました：{(str(e).strip().splitlines() or [type(e).__name__])[0][:150]}")
            code = 1
        finally:
            sess.close()
    if need:
        name = "Suno" if need == "suno" else "DistroKid"
        print(f"－ {name} にログインしていません。システムがつながったままだと Google などのログインが断られるので、いったん離れます")
        open_login(need)
        print(f"【結果】まだ終わっていません：{name} にログインしていません。開いた Chrome でログインしてから、もう一度「{name} に入力する」を押してください")
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()

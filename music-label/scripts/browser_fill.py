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
  python3 scripts/browser_fill.py photo tidewool spotify   # アーティスト写真を入れる（spotify / apple / amazon。「保存」は Gon さん。決定 32）
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
         "distrokid": os.environ.get("POSTDOG_DISTROKID_URL", "https://distrokid.com/new/"),
         # アーティスト写真を入れるサイト（artist_photo.py。決定 32）
         "spotify": "https://artists.spotify.com/", "apple": "https://artists.apple.com/", "amazon": "https://artists.amazonmusic.com/"}
LEARNED = OUT / "browser" / "fields.json"
PROFILE = Path(os.environ.get("POSTDOG_BROWSER_PROFILE", str(Path.home() / ".postdog-browser")))
AUTO = os.environ.get("POSTDOG_AUTOCONTINUE") == "1"   # 試験用：案内のボタンを待たずに進む
# システムが絶対に押さないボタン（作成・送信・公開・購入・削除など）
NEVER = re.compile(r"create|generate|submit|upload|done|publish|release|distribute|buy|purchase|pay|checkout|subscribe|delete|remove|upgrade|clear|reset|"
                   r"作成|生成|送信|公開|配信|購入|支払|削除|アップグレード|消去|リセット", re.I)

# ページに毎回入れる下準備：欄の場所を言葉にする関数と、「クリックで教える」の受け取り
# ロボット確認（Cloudflare など）の画面や、ページの中の小さな画面（iframe）には何も入れない。
# 入れると「自動操作の気配」と見なされ、ロボット確認が何度も出て通らなくなる（2026-10-22）
FRAME_GUARD = r"window.top !== window || /(^|\.)(challenges\.cloudflare\.com|hcaptcha\.com|recaptcha\.net)$/.test(location.hostname)"
INIT_JS = r"""
(() => {
  if (window.top !== window || /(^|\.)(challenges\.cloudflare\.com|hcaptcha\.com|recaptcha\.net)$/.test(location.hostname)) return;   // 一番外の画面だけ（ロボット確認の枠の中には入れない）
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
      if (b.id === 'copy_parts') {   // 画面の部品の一覧をコピー（Claude に貼って、本物の画面に合わせて直してもらう）
        const t = window.__postdogParts ? window.__postdogParts() : '（部品の一覧を作れませんでした）';
        const ok = () => { btn.textContent = 'コピーしました。Claude に貼ってください'; };
        const fallback = () => { const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select();
          document.execCommand('copy'); ta.remove(); ok(); };
        if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).then(ok, fallback); else fallback();
        return; }
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


class Challenge(Exception):
    """サイトの「ロボットではありません」の確認（Cloudflare など）が出た。システムは絶対に触らず、止まる"""


def challenge_shown(sess) -> bool:
    """ロボット確認が画面に出ているか（Cloudflare Turnstile・reCAPTCHA・hCaptcha の枠）"""
    try:
        for fr in sess.page.frames:
            u = (fr.url or "").lower()
            if any(k in u for k in ("challenges.cloudflare.com", "turnstile", "recaptcha", "hcaptcha")):
                return True
        return bool(sess.page.evaluate("""() => /私はロボットではありません|I'?m not a robot|Verify you are human|人間であることを確認/i
            .test(document.body ? document.body.innerText.slice(0, 20000) : '')"""))
    except Exception:  # noqa: BLE001
        return False


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
        self.ctx.add_init_script(f"if (!({FRAME_GUARD})) window.__postdogParts = {PARTS_JS};")
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
                    self.page.evaluate(f"window.__postdogParts = {PARTS_JS};")
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
LEARN_AS = {"suno": "suno_2026_10_21"}   # 本物の画面の部品の一覧に合わせた日。前に覚えた場所は使わない


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


def _pick(loc, avoid, field_only: bool = False):
    """候補のうち、見えていて、避けたい欄（例：スタイルを探すときの「除外するスタイル」）ではない最初のもの。
    field_only：文字を書ける部品（入力欄・書き込み欄）だけ"""
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
            if field_only and not _is_field(c):
                continue
            return c
        except Exception:  # noqa: BLE001
            continue
    return None


def find(sess: Session, site: str, f: dict):
    page = sess.page
    sel = learned().get(LEARN_AS.get(site, site), {}).get(f["key"])
    if sel and (_visible(page.locator(sel)) or (f["type"] == "select" and page.locator(sel).count() > 0)):
        return page.locator(sel).first
    if f.get("css"):   # 本物の画面の部品の一覧でわかった欄の名前（DistroKid。2026-10-22）
        loc = page.locator(f["css"])
        if loc.count() > 0 and (f["type"] in ("select", "file") or _visible(loc)):
            return loc.first
    if f["type"] == "file":
        want = "audio" if f["key"] == "audio" else "image"
        inputs = page.locator("input[type=file]")
        for i in range(inputs.count()):
            acc = (inputs.nth(i).get_attribute("accept") or "").lower()
            if want in acc or (want == "audio" and re.search(r"wav|mp3|flac", acc)) or (want == "image" and re.search(r"jpe?g|png", acc)):
                return inputs.nth(i)
        return None
    for h in f.get("below", []):   # 名前の無い欄：見出しのすぐ下のもの
        sel = sess.js(BELOW_JS, [h, "select,[role=combobox]" if f["type"] == "select" else FIELD_SEL, 160])
        if sel and _visible(page.locator(sel)):
            return page.locator(sel).first
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
                c = _pick(make(), f.get("avoid"), field_only=f["type"] == "text")
                if c is not None:
                    return c
            except Exception:  # noqa: BLE001
                continue
    if f["type"] == "select":   # 見た目は別の部品で、本物の選ぶ箱が隠れている作り
        for h in f.get("below", []) + f.get("hints", []):
            sel = sess.js(HIDDEN_SELECT_JS, [h])
            if sel:
                return page.locator(sel).first
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
    const ds = x.getAttribute('data-state');
    if (/^(on|active|checked|selected)$/.test(ds || '')) return 'on';
    if (/^(off|inactive|unchecked|unselected)$/.test(ds || '')) return 'off';   // "closed" "delayed-open" など（吹き出しの目印）は使わない
    for (const k of ['data-selected', 'data-active', 'data-checked']) if (x.hasAttribute(k)) return x.getAttribute(k) === 'false' ? 'off' : 'on';
    const hs = x.getAttribute('data-headlessui-state'); if (hs !== null) return /checked|selected|active/.test(hs) ? 'on' : 'off';
    if (x.matches && x.matches('input[type=radio],input[type=checkbox]')) return x.checked ? 'on' : 'off';
    if (window.__postdogNoClass) return null;
    const cls = String(x.className && x.className.baseVal !== undefined ? x.className.baseVal : x.className || '');
    if (/(^|\s)(active|selected|checked|is-on)(\s|$)/i.test(cls)) return 'on';
    return null; };
  const holder = e => { if (read(e)) return e;
    // 文字のボタンの中や横にスイッチがある作り（そのスイッチが状態を持つ）
    for (let up = e, i = 0; i < 3 && up; i++, up = up.parentElement) {
      const sw = [...up.querySelectorAll('[role=switch],input[type=checkbox],input[type=radio],[aria-checked],[aria-pressed]')].filter(x => x !== e);
      if (sw.length === 1 && read(sw[0])) return sw[0];
      if (sw.length > 1) break;
    }
    return null; };"""
STATE_JS = "e => {" + STATE_CORE + " const h = holder(e); return h ? read(h) : 'unknown'; }"
SELF_STATE_JS = ("e => { window.__postdogNoClass = true; try {" + STATE_CORE +
                 " return read(e) || 'unknown'; } finally { window.__postdogNoClass = false; } }")   # そのボタン自身の目印だけ（class 名の言葉は見ない）
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
# 見出しの文字の「すぐ下（か横）」にある部品を、画面の上の位置で探す（名前の無い欄・ボタンのかたまり用）
BELOW_JS = r"""([hint, sel, maxdy]) => { const want = hint.trim().toLowerCase(); let best = null, bd = 1e9;
    const shown = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !e.closest('#postdog-overlay'); };
    const labels = [...document.querySelectorAll('label,span,div,p,h2,h3,h4,legend,button')].filter(el =>
      shown(el) && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().toLowerCase() === want));
    const cands = [...document.querySelectorAll(sel)].filter(shown);
    for (const L of labels) { const lr = L.getBoundingClientRect();
      for (const c of cands) { if (c === L || L.contains(c)) continue; const r = c.getBoundingClientRect();
        const dy = r.top - lr.top; if (dy < -15 || dy > maxdy) continue;
        const d = Math.abs(dy) + Math.abs(r.left - lr.left) * 0.2; if (d < bd) { bd = d; best = c; } } }
    return best ? window.__postdogSel(best) : null; }"""
# 見出しの近くの、隠れていてもよい選ぶ箱（select）。かたまりの中に 1 つだけのとき
HIDDEN_SELECT_JS = r"""([hint]) => { const want = hint.trim().toLowerCase();
    for (const el of document.querySelectorAll('label,span,div,p,h2,h3,h4,legend,strong,b')) {
      const r = el.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) continue;
      if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().toLowerCase().replace(/[:*]$/, '').trim() === want)) continue;
      for (let up = el, i = 0; i < 4 && up; i++, up = up.parentElement) {
        const ss = up.querySelectorAll('select'); if (ss.length === 1) return window.__postdogSel(ss[0]); if (ss.length > 1) break; } }
    return null; }"""
# クリックした部品のまわりにある、隠れた選ぶ箱
SELECT_AROUND_JS = r"""e => { for (let up = e, i = 0; i < 4 && up; i++, up = up.parentElement) {
      const ss = up.querySelectorAll('select'); if (ss.length === 1) return window.__postdogSel(ss[0]); if (ss.length > 1) break; }
    return null; }"""
FIELD_SEL = "textarea,input:not([type=range]):not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=file]),[contenteditable=true],[contenteditable='']"
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


LABEL_POS_JS = r"""(hints) => { for (const hint of hints) { const want = hint.trim().toLowerCase();
    for (const el of document.querySelectorAll('label,span,div,p,h2,h3,h4,legend,button')) {
      if (el.closest('#postdog-overlay')) continue;
      if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().toLowerCase() === want)) continue;
      const r = el.getBoundingClientRect(); if (r.width > 0 && r.height > 0) return {x: r.left, y: r.top}; } }
    return null; }"""


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
    """名前の合うボタン。画面に 1 つしか無ければそれ（Male・Female など）。
    同じ名前が何か所にもあるとき（Off・On）は、見出し（f['near']）にいちばん近いもの。近くに無ければ使わない（取り違えないように）"""
    cands = []
    for h in hints:
        rx = re.compile(r"^\s*" + re.escape(h) + r"\s*$", re.I)
        for role in ("button", "radio", "tab", "checkbox", "switch", "option"):
            loc = sess.page.get_by_role(role, name=rx)
            try:
                cands += [loc.nth(i) for i in range(min(loc.count(), 10)) if loc.nth(i).is_visible()
                          and not loc.nth(i).evaluate("e => !!e.closest('#postdog-overlay')")]
            except Exception:  # noqa: BLE001
                continue
        if cands:
            break
    if cands:
        if len(cands) == 1 or not (f and f.get("near")):
            return cands[0]
        pos = sess.js(LABEL_POS_JS, f["near"])
        best, bd = None, 1e9
        for c in cands if pos else []:
            b = c.bounding_box()
            if not b:
                continue
            dy = b["y"] - pos["y"]
            if -20 <= dy <= 150:
                d = abs(dy) + 0.2 * abs(b["x"] - pos["x"])
                if d < bd:
                    best, bd = c, d
        return best
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
            multi = len(f["options"]) >= 2
            st = loc.evaluate(SELF_STATE_JS if multi else STATE_JS)   # 2 つから選ぶボタンは、そのボタン自身だけを見る
            if st == "unknown" and multi:   # 見た目の種類で読む（Suno は選ばれている方が standard、選ばれていない方が tertiary）
                cls = loc.get_attribute("class") or ""
                st = "off" if re.search(r"variant-tertiary", cls) else ("on" if re.search(r"variant-(standard|primary|secondary)", cls) else st)
            got[opt] = [loc, st]
    if len(got) >= 2 and sum(v[1] == "on" for v in got.values()) == 1:
        for v in got.values():
            if v[1] == "unknown":
                v[1] = "off"
    return got


def _look(sess: Session, f: dict) -> str:
    """選択肢ごとの状態を短く（記録用）。例：male=off(tertiary) female=on(standard)"""
    out = []
    for opt, (loc, st) in _states(sess, f).items():
        m = re.search(r"variant-([a-z]+)", loc.get_attribute("class") or "")
        out.append(f"{opt}={st}" + (f"({m.group(1)})" if m else ""))
    return " ".join(out) or "（ボタンが見つからない）"


def choice_state(sess: Session, f: dict) -> bool | None:
    """「どちらか選ぶ」が希望どおりか。True／False／None（読めない・見つからない）"""
    got = _states(sess, f)
    for opt, (loc, st) in got.items():
        if st == "unknown":
            return None
        if (st == "on") != (opt == f["value"]):
            return False
    return True if got or f["value"] == "none" or f.get("skip_if_missing") else None


def set_choice(sess: Session, f: dict) -> bool | None:
    """希望どおりに選ぶ（ボーカルの性別など）。状態が読めないボタンは押さない"""
    got = _states(sess, f)
    if not got:
        sess.log.append(f"{f['label']}：ボタンが見つかりません（{'・'.join(sum(f['options'].values(), [])[:6])}）")
    if any(st == "unknown" for _, st in got.values()):
        sess.log.append(f"{f['label']}：状態が読めないので押していません（{_look(sess, f)}）")
        return None
    pressed = False
    # 選ぶほうを先に押す（2 つのうち 1 つを選ぶ作りなら、それだけで切り替わる）
    for opt, (loc, st) in sorted(got.items(), key=lambda kv: kv[0] != f["value"]):
        st = _states(sess, f).get(opt, [loc, st])[1]
        if (st == "on") != (opt == f["value"]):
            text = " ".join(filter(None, [loc.inner_text(timeout=2000), loc.get_attribute("aria-label") or ""]))
            if NEVER.search(text):
                return False
            h = loc.evaluate(HOLDER_JS) if len(f["options"]) == 1 else None   # 1 つだけのスイッチ：状態を持つ横のスイッチを押す
            target = sess.page.locator(h).first if h else loc
            before = _look(sess, f)
            cls0 = target.get_attribute("class") or ""
            target.click()
            sess.page.wait_for_timeout(600)
            if (target.get_attribute("class") or "") == cls0 and _look(sess, f) == before:   # 何も変わらない：押せていない
                box = target.bounding_box()
                if box:
                    sess.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    sess.page.wait_for_timeout(600)
            sess.log.append(f"{f['label']}：押す前 {before} → 「{opt}」を押した → 押した後 {_look(sess, f)}")
            pressed = True
    if got and not pressed:
        sess.log.append(f"{f['label']}：もう希望どおりなので押していません（{_look(sess, f)}）")
    return choice_state(sess, f)


def teach(sess: Session, site: str, f: dict):
    """見つからない欄を、クリックで 1 回だけ教えてもらう"""
    sess.show(f"「{f['label']}」の欄が見つかりません",
              [f"「{f['label']}」の入力欄を 1 回クリックしてください。場所を覚えて、次からは自動で入れます。",
               *([f["tip"]] if f.get("tip") else []),
               "（このクリックでボタンが押されることはありません）"],
              [{"id": "skip", "label": "この欄はとばす（手で入れる）"},
               {"id": "copy_parts", "label": "画面の部品をコピー（Claude に貼る用）"}], pick=True)
    for _ in range(3):
        m = sess.wait({"action": "skip"})
        if m.get("action") != "picked" or not m.get("selector"):
            return None
        loc = sess.page.locator(m["selector"]).first
        if f["type"] == "select" and not _is_field(loc):
            near = loc.evaluate(SELECT_AROUND_JS)
            if near:
                m["selector"], loc = near, sess.page.locator(near).first
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
        if f["type"] == "select" and tag == "select" and not loc.is_visible():
            return bool(loc.evaluate("""(e, vs) => { const n = s => String(s).replace(/\s+/g, ' ').trim().toLowerCase();
                let o = null;
                for (const v of vs) { o = [...e.options].find(o => n(o.text) === n(v)); if (o) break; }
                if (!o) for (const v of vs) { if (n(v).length < 5) continue; o = [...e.options].find(o => n(o.text).includes(n(v))); if (o) break; }
                if (!o) return false; e.value = o.value;
                e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true}));
                if (window.jQuery) { try { window.jQuery(e).val(o.value).trigger('change'); } catch (x) {} }
                return true; }""", [str(v)] + [str(x) for x in (f.get("alts") or [])]))
        if f["type"] == "select" and tag == "select":
            opts = loc.evaluate("e => [...e.options].map(o => o.text.trim())")
            wants = [str(x) for x in [v] + list(f.get("alts") or []) if x]
            # 候補を順に：まず同じ名前、無ければ名前を含むもの（5 文字以上の候補だけ。「Pop」が「K-Pop」に当たらないように）
            hits = [o for w in wants for o in opts if _norm(o) == _norm(w)] + \
                [o for w in wants if len(_norm(w)) >= 5 for o in opts if _norm(w) in _norm(o)]
            for hit in hits[:1]:
                if hit:
                    loc.select_option(label=hit)
                    f["chosen"] = hit
                    return True
            return False
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
            sess.page.wait_for_timeout(500)
            if _norm(_read(loc)) == _norm(v):
                return True
            if f.get("key") in ("style", "lyrics", "exclude") and _coverage(sess, loc, f) >= 0.9:
                return True   # 札（タグ）に変わって表示される作り：もう入っているので打ち直さない（二重になるため）
        # 入れた文字が残らない作り（画面が自分で文字を管理している）：クリックして、全部選んで、キーボードで打つ
        loc.click()
        sess.page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
        sess.page.keyboard.press("Backspace")
        lines = str(v).split("\n")
        if tag in ("input", "textarea") or len(lines) == 1:
            sess.page.keyboard.insert_text(str(v))
        else:   # 書き込み欄：1 行ずつ打って Enter（改行をまとめて入れると行が消える作りがある）
            for i, ln in enumerate(lines):
                if ln:
                    sess.page.keyboard.insert_text(ln)
                if i < len(lines) - 1:
                    sess.page.keyboard.press("Enter")
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
    sess.log = []
    for f in fields:
        if challenge_shown(sess):
            raise Challenge()   # ロボット確認が出た：これ以上は何も触らない
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
            if r is False and f["value"] == "none":   # どちらかが必ず選ばれる作りで「選ばない」にできない：失敗にはせず知らせる
                sess.unsure.append(f"{f['label']}：Suno では「選ばない」にできませんでした（声はスタイル欄の言葉で出します。気になれば画面で外してください）")
            elif r is False:
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
            ok = any(_norm(x) == g or (_norm(x) and _norm(x) in g) for x in [f["value"], f.get("chosen", "")] + list(f.get("alts") or []) if x)
        else:
            ok = g == want
        cov = None
        if not ok and f["key"] in ("style", "lyrics", "exclude"):
            cov = _coverage(sess, loc, f)
            ok = cov >= 0.9
        if not ok:
            if cov is not None and cov >= 0.3:
                bad.append(f"{f['label']}：一部しか入っていません（入れた言葉の {round(cov * 100)}%）")
            else:
                bad.append(f"{f['label']}：途中までしか入っていません（文字数の上限かもしれません）" if g and want.startswith(g)
                           else f"{f['label']}：入っていません")
    return bad


def _coverage(sess: Session, loc, f: dict) -> float:
    """入れた言葉（スタイルは , 区切り、歌詞は歌う行）のうち、欄とそのまわり（タグの札などに変わって表示される作り）に出ているものの割合"""
    v = str(f["value"])
    if f["key"] == "lyrics":
        toks = [ln for ln in v.splitlines() if ln.strip() and not ln.strip().startswith("[")]
    else:
        toks = [t for t in re.split(r",\s*", v) if t.strip()]
    toks = [_norm(t) for t in toks if _norm(t)]
    if not toks:
        return 1.0
    texts = loc.evaluate("""e => { const out = [e.value ?? '', e.innerText ?? ''];
        let up = e.parentElement;
        for (let i = 0; i < 3 && up; i++, up = up.parentElement) out.push(up.innerText || '');
        return out; }""")
    return max(sum(t in _norm(x) for t in toks) / len(toks) for x in texts)


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
         "hints": ["Lyrics editor", "Lyrics", "歌詞", "Start writing lyrics", "Write some lyrics"], "avoid": r"style|スタイル|除外|exclude|title|タイトル|曲名|saved|cowriter|script|tone",
         "largest_textarea": True, "tip": tip, "show": "（歌詞はダッシュボードの「Suno に貼る内容」からコピー）"},
        # スタイルは「カスタム」（文字で書く）。選べる候補は、文字に同じ言葉が入っているので押さない
        {"key": "style_mode", "label": "スタイルを「カスタム」に", "type": "choice", "value": "custom", "show": "カスタム", "quiet_unknown": True,
         "near": ["スタイル", "Styles", "Style"], "options": {"custom": ["カスタム", "Custom"], "select": ["選択", "Select"]}},
        # Suno のスタイル欄は名前が無く、薄い字の例文が毎回変わる：見出し「Styles」のすぐ下の欄
        {"key": "style", "label": "スタイル", "type": "text", "value": d["style"], "below": ["Styles", "スタイル"],
         "hints": ["Style of Music", "Styles", "Style", "スタイル", "音楽のスタイル"], "avoid": r"exclude|除外|lyrics|歌詞|search|speech|tone|describe the sound", "tip": tip},
        {"key": "more", "label": "その他のオプション", "type": "click", "value": "on", "optional": True, "teach": False, "text_ok": True,
         "only_if_missing": ["exclude", "gender", "length", "max_mode", "weirdness", "style_influence", "variety", "your_taste"],
         "hints": ["その他のオプション", "詳細オプション", "More Options", "More options", "オプション"]},
        {"key": "exclude", "label": "除外するスタイル", "type": "text", "value": d["exclude"],
         "hints": ["除外するスタイル", "Exclude styles", "Exclude", "除外"], "tip": tip},
    ]
    if d["vocal_gender"] != "unset":
        g = d["vocal_gender"] or "none"
        fields.append({"key": "gender", "label": "声の性別", "type": "choice", "value": g, "near": ["Vocal Gender", "声の性別", "ボーカルの性別", "Gender"],
                       "show": _songs.GENDER_JA.get(d["vocal_gender"], g), "options": {"male": ["男", "男性", "Male"], "female": ["女", "女性", "Female"]}})
    hints_len = ["間隔", "長さ", "曲の長さ", "Duration", "Length", "Song Length", "Song length", "Interval", "Timing"]
    fields.append({"key": "length_mode", "label": "間隔（自動・カスタム）", "type": "choice", "near": hints_len,
                   "value": "custom" if d["length_sec"] else "auto", "show": "カスタム" if d["length_sec"] else "自動",
                   "skip_if_missing": True, "quiet_unknown": True,   # いまの Suno には自動・カスタムのボタンが無い（長さを入れればカスタム）
                   "options": {"custom": ["カスタム", "Custom"], "auto": ["自動", "Auto"]}})
    if d["length_sec"]:
        fields.append({"key": "length", "label": "間隔（曲の長さ）", "type": "slider", "scale": "seconds", "value": d["length_sec"],
                       "show": _songs.fmt_len(d["length_sec"]).replace("カスタム ", ""), "hints": hints_len})
    if d.get("max_mode") is not None:
        # Max Mode は見出しの横の Off・On の 2 つのボタン
        fields.append({"key": "max_mode", "label": "Max mode", "type": "choice", "value": "on" if d["max_mode"] else "off",
                       "show": onoff(d["max_mode"]), "near": ["Max Mode", "Max mode", "Max モード"],
                       "options": {"on": ["On", "オン"], "off": ["Off", "オフ"]}})
    if d.get("weirdness") is not None:
        fields.append({"key": "weirdness", "label": "Weirdness（意外さ）", "type": "slider", "scale": "percent", "value": d["weirdness"],
                       "show": f"{d['weirdness']}%", "hints": ["Weirdness", "奇抜さ", "奇妙さ"]})
    if d.get("style_influence") is not None:
        fields.append({"key": "style_influence", "label": "スタイルの影響度", "type": "slider", "scale": "percent", "value": d["style_influence"],
                       "show": f"{d['style_influence']}%", "hints": ["スタイルの影響度", "スタイルの影響", "Style Influence", "Style influence", "Style Strength"]})
    if d.get("variety") is not None:
        fields.append({"key": "variety", "label": "バラエティ", "type": "slider", "scale": "raw", "value": d["variety"],
                       "show": f"{d['variety']}（0〜4）", "hints": ["バラエティ", "Variety", "Variation", "Variations"]})
    if d.get("your_taste") is not None:
        # あなたの好み＝見出し「Personalize」の横の Off・On（「My Taste」のボタンは好みの設定を開くだけなので押さない）
        fields.append({"key": "your_taste", "label": "あなたの好み（Personalize）", "type": "choice", "value": "on" if d["your_taste"] else "off",
                       "show": onoff(d["your_taste"]), "near": ["Personalize", "あなたの好み", "Your Taste"],
                       "options": {"on": ["On", "オン"], "off": ["Off", "オフ"]}})
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
    const raw = (t === 'input' || t === 'textarea') ? String(e.value || '') : (e.isContentEditable ? (e.innerText || '') : null);
    const val = raw === null ? '' : ` 中身=${raw.length}文字「${cut(raw, 40)}」 まわり=「${cut(e.parentElement && e.parentElement.parentElement ? e.parentElement.parentElement.innerText : '', 60)}」`;
    out.push(`${Math.round(e.getBoundingClientRect().top)}\t${t}${role ? '[' + role + ']' : ''}${e.isContentEditable ? '[編集可]' : ''}\t「${cut(e.innerText || e.value || '', 30)}」\t${at.join(' ')}${val}\tclass=${cut(e.className && e.className.baseVal !== undefined ? '' : e.className, 160)}\t見出し=${lab(e)}`);
    if (out.length >= 300) break;
  }
  const log = window.__postdogLog ? '押したボタンの記録：\n' + window.__postdogLog + '\n' : '';
  return log + out.sort((a, b) => parseInt(a) - parseInt(b)).join('\n'); }"""


def save_parts(sess: Session, site: str) -> Path | None:
    """画面の部品の一覧（欄・ボタン・スイッチ・スライダーの名前と状態。入れた歌詞などの中身は文字数だけ）と画面の写しを残す"""
    d = OUT / "browser"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{site}_parts.txt"
    try:
        body = sess.js(PARTS_JS, None, default="")
        log = "\n".join(getattr(sess, "log", []) or [])
        p.write_text(f"画面：{sess.page.url}\n押したボタンの記録：\n{log}\n（上からの位置\t部品\t文字\t目印\tclass\t見出し）\n{body}\n", encoding="utf-8")
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
    # 1 回の入力は 1 曲だけ。入れ終わったらすぐにシステムが Chrome から離れ、つながっていない状態で Gon さんが Create を押す。
    # つながったまま Create を押すと、ロボット確認が「自動操作の気配」を見つけて、何度出しても通らなくなる（2026-10-22）
    later = songs[1:]
    songs = songs[:1]
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
        if challenge_shown(sess):
            raise Challenge()
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
        if (bad or sess.unsure) and getattr(sess, "log", None):
            lines += ["押したボタンの記録：", *sess.log]
        for x in getattr(sess, "log", []):
            print(f"   記録：{x}")
            unsure_all.extend(f"{name}の{x}" for x in sess.unsure)
        try:
            import publish
            lines += [f"保存先：{publish.inboxes()[0]}（この Chrome でダウンロードすると、ここに入ります）"]
        except Exception:  # noqa: BLE001
            pass
        lines += ["① 画面の上が「高度な」（Advanced）になっているか確かめる",
                  "② 内容を確かめて「Create」を押す（押すのは Gon さんだけ）。システムはもうこの Chrome から離れています。"
                  "「私はロボットではありません」が出たら、Gon さんがチェックする",
                  "③ できた 2 つを聴いて、気に入った方に「いいね」→ その 1 本を SSD の「楽曲情報」にダウンロード（そこから先は自動）",
                  *([f"次の曲（あと {len(later)} 曲）は、Create のあと少し時間をあけて、ダッシュボードでもう一度「Suno に入力する」を押してください"] if later else []),
                  "ロボット確認が何度も出て通らないとき：この専用の Chrome は使わず、ふだんの Chrome か Safari で http://localhost:8765/phone/{id} を開き、「写す」ボタンで Suno に貼ってください（ふだんのブラウザは確認が出にくい）".format(id=song["id"])]
        if n == len(songs):   # 最後の曲：案内を残したまま終わる（Create は Gon さんが押す）
            sess.keep_overlay = True
            sess.js("(t) => { window.__postdogLog = t; }", "\n".join(getattr(sess, "log", []) or []))   # コピーのボタンにも記録を入れる
            btns = [{"id": "dismiss", "label": "この案内を消す"}]
            if bad or sess.unsure:
                btns.insert(0, {"id": "copy_parts", "label": "画面の部品をコピー（Claude に貼る用）", "primary": True})
                lines.append("「画面の部品をコピー」を押して Claude に貼ると、本物の画面に合わせて直せます（欄やボタンの名前と状態だけで、歌詞は頭の数文字だけ）")
            sess.show("Suno に入力しました" if not bad else "Suno への入力が足りません", lines, btns)
            break
        sess.show("Suno に入力しました" if not bad else "Suno への入力が足りません", lines,
                  [{"id": "next", "label": "次の曲へ", "primary": True}, {"id": "stop", "label": "ここでやめる"}])
        m = sess.wait({"action": "next"})
        if m.get("action") in ("stop", "closed"):
            break
    if not problems and len(ok_songs) == len(songs):
        extra = f"（ただし {'／'.join(unsure_all)}）" if unsure_all else ""
        rest = f"。次の曲（あと {len(later)} 曲）は、少し時間をあけてもう一度押してください" if later else ""
        print(f"【結果】Suno の作曲画面に入力し、全部の欄に入っていることを確かめました{extra}。システムは Chrome から離れました。開いている Chrome で「作成」（Create）を押してください{rest}")
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
    # Songwriter の本名は名・ミドル・姓の 3 つの欄（ローマ字。例 OWNER_LEGAL_NAME=Taro Yamada）。本名は .env にだけ置く
    parts = owner.split()
    first = os.environ.get("OWNER_LEGAL_NAME_FIRST") or (parts[0] if parts else "")
    last = os.environ.get("OWNER_LEGAL_NAME_LAST") or (parts[-1] if len(parts) > 1 else "")
    middle = " ".join(parts[1:-1]) if len(parts) > 2 and not os.environ.get("OWNER_LEGAL_NAME_FIRST") else ""
    # 2 つ目のジャンル：DistroKid の一覧は 1 つ目のジャンルに合わせて変わるので、その組の音に近い順に候補を並べて試す
    from _common import load_artist
    sound = ((load_artist(song["slug"], song.get("label_slug"), missing_ok=True) or {}).get("sound") or {})
    cands = [g.title() for g in sound.get("genres", [])] + [f["Secondary genre"]]
    genre2 = [g for i, g in enumerate(cands) if g and g.lower() != str(f["Primary genre"]).lower()
              and g.lower() not in [c.lower() for c in cands[:i]]]
    return [
        {"key": "artist", "label": "Artist / Band name", "type": "text", "value": f["Artist / Band name"], "css": "input[name=bandname]",
         "hints": ["Artist name", "Band name", "Artist"]},
        {"key": "title", "label": "Song title", "type": "text", "value": f["Song title"], "css": "input.uploadFileTitle.track_1",
         "hints": ["Song title", "Track title", "Title"]},
        {"key": "release", "label": "Release date", "type": "date", "value": rel, "css": "input[name=releaseDate]", "hints": ["Release date"],
         "teach": False, "show": f["Release date"]},
        {"key": "language", "label": "Language", "type": "select", "value": f["Language"], "css": "select[name=language]",
         "hints": ["Language"], "below": ["Language"]},
        {"key": "genre1", "label": "Primary genre", "type": "select", "value": f["Primary genre"], "css": "select[name=genre1]", "hints": ["Primary genre", "Genre"],
         "below": ["Primary genre", "Genre"], "tip": "ページを下にスクロールすると「Primary genre」の選ぶ箱があります。その箱そのものをクリックしてください。"},
        # 1 つ目のジャンルの細かい分類（Electronic を選ぶと必須）。候補は 2 つ目のジャンルと同じく、組の音に近い順
        {"key": "subgenre", "label": f"{f['Primary genre']} のサブジャンル", "type": "select", "value": genre2[0] if genre2 else "",
         "alts": genre2[1:] + ["Electronica", "Downtempo"], "teach": False, "optional": True,
         "hints": [f"{f['Primary genre']} subgenre", "Subgenre", "Sub-genre"], "below": [f"{f['Primary genre']} subgenre", "Subgenre", "Sub-genre"],
         "show": "・".join(genre2[:3]) + " など（近いものを選ぶ）"},
        {"key": "genre2", "label": "Secondary genre", "type": "select", "value": genre2[0] if genre2 else "", "alts": genre2[1:], "css": "select[name=genre2]",
         "hints": ["Secondary genre"], "below": ["Secondary genre"], "teach": False, "optional": True,
         "show": "・".join(genre2) + "（どれも無ければ空欄でよい）"},
        {"key": "songwriter_role", "label": "Songwriter の役割", "type": "select", "css": "select.songwriter_real_name_role", "teach": False,
         "value": "Music" if song.get("instrumental") else "Music and lyrics"},
        {"key": "songwriter_first", "label": "Songwriter の名（ローマ字）", "type": "text", "value": first, "css": "input[name=songwriter_real_name_first1]",
         "teach": False, "show": "（.env の OWNER_LEGAL_NAME の名）"},
        {"key": "songwriter_middle", "label": "Songwriter のミドルネーム", "type": "text", "value": middle, "css": "input[name=songwriter_real_name_middle1]",
         "teach": False, "optional": True},
        {"key": "songwriter_last", "label": "Songwriter の姓（ローマ字）", "type": "text", "value": last, "css": "input[name=songwriter_real_name_last1]",
         "teach": False, "show": "（.env の OWNER_LEGAL_NAME の姓）"},
        {"key": "audio", "label": "音源ファイル", "type": "file", "value": str(audio_path) if audio_path.exists() else "", "css": "input.trackupload_1",
         "show": str(audio_path)},
        {"key": "cover", "label": "ジャケット", "type": "file", "value": str(cover_path) if cover_path and cover_path.exists() else "", "css": "input[name=artwork]",
         "show": str(cover_path)},
    ], f


# DistroKid の「はい／いいえ」の選択（本物の画面の部品の一覧でわかった名前。2026-10-22）。
# システムが選ぶのはここに書いたものだけ。Extras（有料の追加）・宣誓のチェック（areyousure）・最後の「Continue」は押さない
ARTIST_ID_RADIOS = [("spotifyArtistID", "Spotify"), ("appleArtistID", "Apple Music"), ("googleArtistID", "YouTube Music"),
                    ("instagramProfileArtistID", "Instagram"), ("facebookProfileArtistID", "Facebook")]


def distrokid_radios(song: dict) -> list[tuple[str, str]]:
    """[(何を選ぶか, CSS)]"""
    inst = "1" if song.get("instrumental") else "0"
    return [
        ("前に出したことがある？ → No", 'input[type=radio][name^="previouslyReleased_"][value="0"]'),
        ("曲名に featured artist を出す？ → No", 'input[type=radio][name^="feat_"][value="0"]'),
        ("バージョン → ふつうの版", 'input[type=radio][name^="version_"][value=""]'),
        ("Dolby Atmos → No", 'input[type=radio][name="dolby_1"][value="0"]'),
        ("自分で書いた曲（カバーではない）", 'input[type=radio][name^="coversong_"][value="0"]'),
        ("Explicit（不適切な言葉）→ No", 'input[type=radio][name^="explicit_"][value="0"]'),
        ("Clean 版の別にある？ → No（もともと Clean）", 'input[type=radio][name^="cleaned_"][value="0"]'),
        ("インスト？ → " + ("歌詞なし" if inst == "1" else "歌詞あり"), f'input[type=radio][name^="instrumental_"][value="{inst}"]'),
        ("AI を使った？ → Yes（正直に申告）", 'input[type=radio][name^="ai_gate_"][value="1"]'),
        ("試聴の始まり → サービスにまかせる", 'input[type=radio][name="previewStart_1"][value="no"]'),
    ]


def artist_id_radios(song: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """Artist already in 〜?：前の曲がまだ配信されていない（DistroKid が『0 件』と出す）組は「No（新しいページ）」。
    前の曲のページが見つかっている組は、そのページを選ぶ（Spotify は配信の確認で記録した ID）。わからなければ Gon さんに"""
    try:
        import publish
        sp = (publish.artist_links(song["slug"]).get("spotify") or {}).get("id")
    except Exception:  # noqa: BLE001
        sp = None
    pick, ask = [], []
    for name, svc in ARTIST_ID_RADIOS:
        pick.append((f"Artist already in {svc}? → No（新しいページ）", f'input[type=radio][name="{name}"][value="new"][class*="ZeroMatches"]'))
        if name == "spotifyArtistID" and sp:
            pick.append((f"Artist already in Spotify? → Yes（前の曲のページ {sp}）", f'input[type=radio][name="{name}"][value="{sp}"]'))
        ask.append(name)
    return pick, ask


def set_radios(sess: Session, items: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """選ぶ（見えない作りのボタンは、要素そのものを押す）。選べたもの／画面に無かったもの"""
    done, missing = [], []
    for label, css in items:
        loc = sess.page.locator(css)
        if loc.count() == 0:
            missing.append(label)
            continue
        el = loc.first
        try:
            if not el.is_checked():
                try:
                    el.check(timeout=3000)
                except Exception:  # noqa: BLE001  見た目だけの丸の下に隠れたボタン
                    el.evaluate("e => { e.click(); e.dispatchEvent(new Event('change', {bubbles: true})); }")
            (done if el.is_checked() else missing).append(label)
        except Exception:  # noqa: BLE001
            missing.append(label)
    return done, missing


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
    r_done, r_miss = set_radios(sess, distrokid_radios(song))
    a_items, _ = artist_id_radios(song)
    a_done, _ = set_radios(sess, a_items)
    done += r_done + a_done
    a_left = [svc for n, svc in ARTIST_ID_RADIOS if not any(svc in d for d in a_done)]
    bad = verify(sess, fields)
    # Gon さんに残すのは、宣誓（法的な約束なので本人が読んで押す）と、画面に出たときだけの項目
    always = ["Important checkboxes（下の 5 つの宣誓）：文を読んで自分でチェック（Suno の Pro で作った曲か、を必ず確かめる。システムは押さない）",
              "Extras（有料の追加）・Discovery Pack：チェックしない"]
    if a_left:
        always.insert(0, f"Artist already in 〜?（{'・'.join(a_left)}）：同じ組の前の曲があれば、そのページ（Yes）を選ぶ。無ければ No")
    if r_miss:
        always.insert(0, "画面に無かった／選べなかった項目（出ていれば選ぶ）：" + "・".join(r_miss))
    always.append("AI の申告で Yes のあとに質問が出たら：使ったもの（作曲・歌詞・声など Suno で作った部分）を正直に選ぶ")
    always.append(f"Track credits が出たら：{draft.get('Track credits（Apple の決まり）', 'Performer：アーティスト名／Producer：本名')}")
    print(f"   ○ {song['artist']}「{song['title']}」：入れた {len(done)}・手で {len(manual)}")
    sess.show("DistroKid に下書きを入れました",
              [f"{song['artist']}「{song['title']}」",
               ("✓ 読み返して確かめました：" if not bad else "入れた：") + ("・".join(done) or "なし"),
               *(["⚠ 入っていない欄：", *bad] if bad else []),
               *(["手で入れてください：", *manual] if manual else []), "画面で確かめて選ぶもの：", *always,
               "内容を確かめて、最後の登録ボタンを押してください（押すのは Gon さんだけ）。",
               "押し終わったら、配信日を選んで「登録した」を押すと、記録されます。"],
              [{"id": "registered", "label": "登録した", "primary": True}, {"id": "stop", "label": "閉じる（まだ登録しない）"},
               {"id": "copy_parts", "label": "画面の部品をコピー（入らない欄があるとき・Claude に貼る用）"}],
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


def run_photo(sess: Session, slug: str, service: str) -> int:
    """アーティスト写真をサイトのアーティストのページに入れる（決定 32）。
    システムはサイトのボタンを押さない。Gon さんが「写真を変える」などを押すと、ファイルを選ぶ画面の代わりに整えた画像が選ばれる。
    切り抜きの確認と「保存」は Gon さんが押す"""
    import artist_photo
    svc = artist_photo.SERVICES.get(service)
    if svc is None:
        sys.exit(f"[エラー] サイトは {' / '.join(artist_photo.SERVICES)} のどれか")
    art = artist_photo.artist_of(slug)
    if art is None:
        sys.exit(f"[エラー] 組 {slug} が見つかりません")
    try:
        imgs = artist_photo.prepare(slug)
    except FileNotFoundError as e:
        print(f"【結果】まだ終わっていません：{e}")
        return 2
    kinds = list(svc["images"])
    state = {"kind": kinds[0], "set": []}

    def on_chooser(fc):   # Gon さんがサイトの「写真を変える」を押したとき：ファイルを選ぶ画面の代わりに、整えた画像を入れる
        try:
            fc.set_files(str(imgs[state["kind"]]))
            state["set"].append(state["kind"])
            print(f"   ○ {svc['images'][state['kind']]} を選びました：{imgs[state['kind']].name}")
        except Exception as e:  # noqa: BLE001
            print(f"   ✕ 画像を選べませんでした：{e}")

    def watch(pg):
        try:
            pg.on("filechooser", on_chooser)
        except Exception:  # noqa: BLE001
            pass
    for pg in sess.ctx.pages:
        watch(pg)
    sess.ctx.on("page", watch)
    sess.goto(svc["url"])
    sess.ensure_login(service)

    def lines():
        now = svc["images"][state["kind"]]
        got = "・".join(svc["images"][k] for k in dict.fromkeys(state["set"])) or "まだ"
        return ([f"{art['name']} の写真を {svc['name']} に入れます。", *svc["how"],
                 f"サイトの写真の「編集」「アップロード」などを押すと、ファイルを選ぶ画面の代わりに「{now}」が自動で選ばれます。",
                 "切り抜きを確かめて、「保存」は Gon さんが押してください（公開されます。システムは押しません）。",
                 f"選んだ画像：{got}"]
                + (["⚠ 元の写真が小さいので、サイトに断られるかもしれません"] if imgs.get("_small") else []))

    def buttons():
        bs = [{"id": f"kind_{k}", "label": f"次に選ぶ画像：{v}" + ("（いま）" if k == state["kind"] else ""), "primary": False}
              for k, v in svc["images"].items()] if len(kinds) > 1 else []
        return bs + [{"id": "done", "label": "保存した（記録する）", "primary": True}, {"id": "stop", "label": "閉じる（まだ）"},
                     {"id": "copy_parts", "label": "画面の部品をコピー（うまくいかないとき・Claude に貼る用）"}]
    while True:
        sess.show(f"アーティスト写真：{svc['name']}", lines(), buttons())
        m = sess.wait({"action": "stop"})
        act = m.get("action", "")
        if act.startswith("kind_") and act[5:] in svc["images"]:
            state["kind"] = act[5:]
            continue
        break
    if act == "done":
        artist_photo.mark_done(slug, service, imgs.get("_source"))
        print(f"【結果】{art['name']} の写真を {svc['name']} に入れたと記録しました")
        return 0
    if act == "closed":
        raise BrowserClosed()
    print(f"【結果】まだ終わっていません：{svc['name']} の写真は「保存した」が押されていません"
          + (f"（選んだ画像：{'・'.join(dict.fromkeys(state['set']))}）" if state["set"] else ""))
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
    name = {"suno": "Suno に入力する", "distrokid": "DistroKid に入力する"}.get(site, "写真を入れる")
    print(f"【結果】ログイン用に Chrome を開きました。ログインが済んだら、もう一度「{name}」を押してください"
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
    ap.add_argument("what", choices=["suno", "distrokid", "photo", "login", "forget", "reset", "parts"])
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
    if a.what == "photo" and len(a.args or []) < 2:
        sys.exit("[エラー] 組とサイトを付けてください（例 photo tidewool spotify）")
    code, need = 0, None
    with sync_playwright() as pw:
        sess = Session(pw)
        try:
            if a.what == "suno":
                code = run_suno(sess, a.args)
            elif a.what == "distrokid":
                code = run_distrokid(sess, a.args[0])
            elif a.what == "photo":
                code = run_photo(sess, a.args[0], a.args[1])
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
        except Challenge:
            try:
                sess.keep_overlay = True
                sess.show("ロボット確認が出たので止まりました",
                          ["サイトの「私はロボットではありません」の確認が出ました。システムはこれ以上何も触りません。",
                           "Gon さんがご自分でチェックしてください。続きは、確認が終わってから入力し直すか、手で入れてください。",
                           "何度も出て通らないときは、この専用の Chrome は使わず、ふだんの Chrome か Safari で http://localhost:8765/phone を開き、"
                           "曲を選んで「写す」ボタンで Suno に貼ってください。続けて何回も作らず、時間もあけてください。"],
                          [{"id": "dismiss", "label": "この案内を消す"}])
            except Exception:  # noqa: BLE001
                pass
            print("【結果】まだ終わっていません：サイトのロボット確認が出たので、システムは止まりました（確認は Gon さんがチェックしてください。システムは押しません）")
            code = 2
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

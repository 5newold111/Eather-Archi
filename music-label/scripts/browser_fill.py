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
         # 登録のあとに歌詞を出す画面（DistroKid の「Enhance your music」→ Lyrics。決定 52）
         "distrokid_lyrics": os.environ.get("POSTDOG_DISTROKID_LYRICS_URL", "https://distrokid.com/lyrics/"),
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
    """その番号の窓口が、ふつうの Chrome のものか（ほかのアプリ（Electron など）の窓口にはつながない）"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            v = json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return False
    return str(v.get("Browser", "")).startswith(("Chrome/", "HeadlessChrome/")) and "Electron" not in str(v.get("User-Agent", ""))


def _debug_chrome_running() -> bool:
    """専用の Chrome が、つなぎ口つき（入力用）で動いているか"""
    out = subprocess.run(["ps", "-axww", "-o", "command="], capture_output=True, text=True).stdout
    key = f"--user-data-dir={PROFILE}"
    return any(key in line and "--remote-debugging-port" in line and "--type=" not in line for line in out.splitlines())


def _running_port() -> int | None:
    """専用の Chrome がもう開いていれば、そのつなぎ口の番号。
    Mac の再起動などで記録（DevToolsActivePort）だけが残り、同じ番号を別のアプリが使っていることがあるので、
    専用の Chrome がつなぎ口つきで動いていて、その番号がふつうの Chrome の窓口のときだけ使う（2026-10-22）"""
    f = PROFILE / "DevToolsActivePort"
    try:
        port = int(f.read_text().split()[0])
    except Exception:  # noqa: BLE001
        return None
    if not _debug_chrome_running():
        f.unlink(missing_ok=True)   # 古い記録
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
            try:
                self.browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{chrome_port()}")
            except Exception as e:  # noqa: BLE001  つなげない：専用の Chrome を閉じて、開き直してから 1 回だけやり直す
                print(f"   … 専用の Chrome につなげませんでした（{str(e).splitlines()[0][:120]}）。閉じて開き直します")
                quit_chrome()
                (PROFILE / "DevToolsActivePort").unlink(missing_ok=True)
                try:
                    self.browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{chrome_port()}")
                except Exception as e2:  # noqa: BLE001
                    sys.exit("[案内] 専用の Chrome につなげませんでした。① 開いている Chrome をすべて ⌘Q で閉じる "
                             "② ターミナルで  python3 -m pip install --user -U playwright  を実行する ③ もう一度押す。"
                             f"それでもだめなら、この文を Claude に貼ってください：{str(e2).splitlines()[0][:200]}")
            self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        else:          # Chrome が無い：Playwright のブラウザ（Google でのログインは断られることがある）
            opts = dict(user_data_dir=str(PROFILE), headless=AUTO, viewport=None, args=["--start-maximized"])
            self.ctx = pw.chromium.launch_persistent_context(executable_path=exe, **opts) if exe else \
                pw.chromium.launch_persistent_context(**opts)
        self.msg: dict | None = None
        try:
            self.ctx.expose_binding("postdog", lambda source, payload: setattr(self, "msg", payload))
        except Exception as e:  # noqa: BLE001  前の入力のつなぎが残っている：開き直してもらうため上に知らせる
            raise RuntimeError(f"案内とのつなぎを作れませんでした（{str(e).splitlines()[0][:120]}）") from e
        self.ctx.add_init_script(INIT_JS)
        self.ctx.add_init_script(f"if (!({FRAME_GUARD})) window.__postdogParts = {PARTS_JS};")
        self.page = self.ctx.new_page() if self.cdp or not self.ctx.pages else self.ctx.pages[0]
        if self.cdp:
            self.page.bring_to_front()
        self.overlay: dict | None = None

    def goto(self, url: str) -> None:
        for i in range(2):   # 開いたばかりの Chrome で、移動が空振りして about:blank のままになることがあるので 1 回だけやり直す（決定 63）
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:  # noqa: BLE001
                if i:
                    raise
                print(f"   … {url} を開けませんでした（{str(e).splitlines()[0][:120]}）。もう一度開きます")
                self.page.wait_for_timeout(1500)
                continue
            if not self.page.url.startswith("about:"):
                break
            self.page.wait_for_timeout(1500)
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

    def wait(self, auto: dict | None = None, tick=None) -> dict:
        """右下の案内のボタン（またはクリックで教える）を待つ。ブラウザを閉じられたら {'action': 'closed'}。
        tick：待っている間に 2 秒ごとに呼ぶ見張り（DistroKid の差し戻しの見張りなど）"""
        self.msg = None
        if AUTO:
            return auto or {"action": "continue"}
        n = 0
        while self.msg is None:
            try:
                self.page.wait_for_timeout(400)
                n += 1
                if tick and n % 5 == 0:
                    tick()
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


# サイト全体の部品（表示の言語の切り替え・クッキーの案内など）。曲の欄と取り違えて触らない（決定 75）
SITE_CHROME_JS = r"""e => /^language selected/i.test(e.getAttribute('aria-label') || '')
    || !!e.closest('footer, [class*="osano" i], [id*="osano" i], [class*="language-select" i], [class*="lang-switch" i]')"""


def _site_chrome(c) -> bool:
    try:
        return bool(c.evaluate(SITE_CHROME_JS))
    except Exception:  # noqa: BLE001
        return False


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
            if not c.is_visible() or _site_chrome(c):
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
    if sel and (_visible(page.locator(sel)) or (f["type"] == "select" and page.locator(sel).count() > 0)) and not _site_chrome(page.locator(sel).first):
        return page.locator(sel).first
    if f.get("css"):   # 本物の画面の部品の一覧でわかった欄の名前（DistroKid。2026-10-22）
        loc = page.locator(f["css"])
        n = loc.count()
        for i in range(n):   # 同じ名前の欄が裏にもあることがある：画面に見えているほうに入れる
            try:
                if loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:  # noqa: BLE001
                pass
        if n and f["type"] in ("select", "file"):
            return loc.first
        if f.get("css_only"):
            return None   # この欄の名前が無い画面（Pop にはサブジャンルの欄が無いなど）：ほかの探し方で別の箱を触らない
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
        if sel and _visible(page.locator(sel)) and not _site_chrome(page.locator(sel).first):
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
            if sel and not _site_chrome(page.locator(sel).first):
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


def _remember_options(key: str, opts: list[str]) -> None:
    import publish
    real = [o for o in opts if o and not re.match(r"(?i)select", o)]
    if not real:
        return
    d = read_json(publish.DK_GENRES) if publish.DK_GENRES.exists() else {}
    if d.get(key) != real:
        d[key] = real
        publish.DK_GENRES.parent.mkdir(parents=True, exist_ok=True)
        publish.DK_GENRES.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


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
            return bool(loc.evaluate(r"""(e, vs) => { const n = s => String(s).replace(/\s+/g, ' ').trim().toLowerCase();
                let o = null;
                for (const v of vs) { o = [...e.options].find(o => n(o.text) === n(v)); if (o) break; }
                if (!o) for (const v of vs) { if (n(v).length < 5) continue; o = [...e.options].find(o => n(o.text).includes(n(v))); if (o) break; }
                if (!o) return false; e.value = o.value;
                e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true}));
                if (window.jQuery) { try { window.jQuery(e).val(o.value).trigger('change'); } catch (x) {} }
                return true; }""", [str(v)] + [str(x) for x in (f.get("alts") or [])]))
        if f["type"] == "select" and tag == "select":
            opts = loc.evaluate("e => [...e.options].map(o => o.text.trim())")
            if f.get("key") in ("genre1", "genre2", "subgenre"):   # DistroKid の実際の選択肢を記録（下書きも、この中の名前で作る）
                _remember_options("sub:" + str(sess.filled_genre1) if f["key"] == "subgenre" and getattr(sess, "filled_genre1", "") else f["key"], opts)
            wants = [str(x) for x in [v] + list(f.get("alts") or []) if x]
            # 候補を順に：まず同じ名前、無ければ名前を含むもの（5 文字以上の候補だけ。「Pop」が「K-Pop」に当たらないように）
            hits = [o for w in wants for o in opts if _norm(o) == _norm(w)] + \
                [o for w in wants if len(_norm(w)) >= 5 for o in opts if _norm(w) in _norm(o)]
            for hit in hits[:1]:
                if hit:
                    loc.select_option(label=hit)
                    if f.get("css"):   # 同じ名前の箱が裏にもあるときは、全部に同じものを入れてサイトに知らせる（DistroKid のジャンル）
                        sess.page.evaluate("""([css, text]) => { for (const e of document.querySelectorAll(css)) {
                            const o = [...e.options].find(o => o.text.trim() === text); if (!o) continue;
                            if (e.value !== o.value) { e.value = o.value; }
                            e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true}));
                            if (window.jQuery) { try { window.jQuery(e).val(o.value).trigger('change'); } catch (x) {} } } }""", [f["css"], hit])
                    f["chosen"] = hit
                    if f.get("key") == "genre1":
                        sess.filled_genre1 = hit
                    return True
            return False
        if f["type"] == "date" and (loc.get_attribute("type") or "") == "date":
            lo = loc.get_attribute("min") or ""
            if lo and str(v) < lo:   # 選べるいちばん早い日より前：その日にする（DistroKid は早すぎる日を受け付けず空にする）
                v = lo
                f["chosen"] = lo
            loc.fill(str(v))
            if (loc.input_value() or "") != str(v):   # 入らなかった：値を直接入れて、サイトに知らせる
                loc.evaluate("(e, v) => { e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));"
                             " e.dispatchEvent(new Event('change', {bubbles: true})); if (window.jQuery) window.jQuery(e).trigger('change'); }", str(v))
            return (loc.input_value() or "") == str(v)
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


def fill_fields(sess: Session, site: str, fields: list[dict], fresh: bool = True) -> tuple[list[str], list[str]]:
    """fresh=False：前に入れた欄の記録を残したまま足す（DistroKid で欄を何回かに分けて入れるとき）"""
    done, manual = [], []
    if fresh or not hasattr(sess, "filled"):
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
        if f.get("css"):   # 欄の名前がわかっているもの：入れたときの箱ではなく、いま画面に見えている箱を読む（作り直されても気づけるように）
            cand = sess.page.locator(f["css"])
            loc = next((cand.nth(i) for i in range(cand.count()) if cand.nth(i).is_visible()), None) or loc
        if loc is None:
            if not f.get("optional"):
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
        elif f["type"] == "select" and f.get("chosen") and f.get("css"):   # 選んだもの（候補の中から実際に選んだ名前）と同じか
            ok = g == _norm(f["chosen"])
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
    let val = raw === null ? '' : ` 中身=${raw.length}文字「${cut(raw, 40)}」 まわり=「${cut(e.parentElement && e.parentElement.parentElement ? e.parentElement.parentElement.innerText : '', 60)}」`;
    if (t === 'select') {   // 選ぶ箱：いま選ばれているもの・選択肢の数（ジャンルは全部の選択肢）
      const sel = e.selectedOptions && e.selectedOptions[0];
      val = ` 選択中=「${cut(sel ? sel.text : '', 40)}」 値=「${cut(e.value, 20)}」 選択肢=${e.options.length}`
        + (/genre/i.test(e.name || '') ? ` 一覧=「${cut([...e.options].map(o => o.text.trim()).join('|'), 900)}」` : '');
    }
    if (t === 'input' && /radio|checkbox/.test(e.type)) val += e.checked ? ' ●選ばれている' : '';
    const hidden = !(e.offsetParent !== null || getComputedStyle(e).position === 'fixed') || getComputedStyle(e).visibility === 'hidden' || getComputedStyle(e).opacity === '0';
    if (hidden) val += ' ［画面に見えない］';
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


def progress(i: int, n: int, song: dict, extra: str = "") -> None:
    """ダッシュボードに「何曲中の何曲目」を出す（webapp.py が【進み具合】の行を読む。決定 65）"""
    print(f"【進み具合】{i}/{n} 曲目：{song['artist']}「{song['title']}」" + (f"（{extra}）" if extra else ""), flush=True)


def step(k: int, n: int, text: str) -> None:
    """ダッシュボードに、その曲の中のいまの工程を出す（【工程】の行）"""
    print(f"【工程】{k}/{n}：{text}", flush=True)


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
        progress(n, len(songs), song, f"作曲待ちはほかに {len(later)} 曲" if later else "")
        step(1, 4, "Suno の作曲画面を開いています")
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
        step(2, 4, "曲名・スタイル・歌詞などの欄を入れています")
        done, manual = fill_fields(sess, "suno", fields)
        step(3, 4, "入れた欄を読み返しています")
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
        step(4, 4, "Gon さんの確認待ち（Create を押す）")
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
    # ジャンルの候補は DistroKid のジャンル名に置き換えたもの（publish.genre_prefs）。画面では、選択肢にあるものだけを選ぶ（決定 44）
    prefs = publish.genre_prefs([f["Primary genre"], f["Secondary genre"]] + list(sound.get("genres") or []))
    genre1_alts = [g for g in prefs if g != f["Primary genre"]]
    cands = prefs + [g.title() for g in sound.get("genres", [])]
    genre2 = [g for i, g in enumerate(cands) if g and g.lower() != str(f["Primary genre"]).lower()
              and g.lower() not in [c.lower() for c in cands[:i]]]
    return [
        {"key": "artist", "label": "Artist / Band name", "type": "text", "value": f["Artist / Band name"], "css": "input[name=bandname]",
         "hints": ["Artist name", "Band name", "Artist"]},
        {"key": "title", "label": "Song title", "type": "text", "value": f["Song title"], "css": "input.uploadFileTitle.track_1",
         "hints": ["Song title", "Track title", "Title"]},
        {"key": "release", "label": "Release date", "type": "date", "value": rel, "css": "input[name=releaseDate]", "hints": ["Release date"],
         "teach": False, "show": f["Release date"]},
        # 曲の言語は、登録画面の欄（name=language）だけ。サイトの表示の言語の切り替え（aria-label「Language selected: …」）は触らない（決定 75）
        {"key": "language", "label": "Language", "type": "select", "value": f["Language"], "css": "select[name=language]", "css_only": True,
         "hints": ["Language"], "below": ["Language"]},
        {"key": "genre1", "label": "Primary genre", "type": "select", "value": f["Primary genre"], "alts": genre1_alts, "css": "select[name=genre1]", "css_only": True, "hints": ["Primary genre", "Genre"],
         "below": ["Primary genre", "Genre"], "tip": "ページを下にスクロールすると「Primary genre」の選ぶ箱があります。その箱そのものをクリックしてください。"},
        # 1 つ目のジャンルの細かい分類（Electronic を選ぶと必須）。候補は 2 つ目のジャンルと同じく、組の音に近い順
        # サブジャンルは組の音の言葉そのもの（Synthwave・House など）を先に、無ければ近い大きな分け方。画面の選択肢にあるものだけ選ぶ
        {"key": "subgenre", "label": f"{f['Primary genre']} のサブジャンル", "type": "select",
         "value": (subs := [g.title() for g in sound.get("genres", [])] + ["Electronica", "Downtempo", "House", "Synth-Pop"])[0],
         "alts": subs[1:], "teach": False, "optional": True, "css": 'select[name*="subgenre" i]', "css_only": True,
         "hints": [f"{f['Primary genre']} subgenre", "Subgenre", "Sub-genre"], "below": [f"{f['Primary genre']} subgenre", "Subgenre", "Sub-genre"],
         "show": "・".join(genre2[:3]) + " など（近いものを選ぶ）"},
        {"key": "genre2", "label": "Secondary genre", "type": "select", "value": genre2[0] if genre2 else "", "alts": genre2[1:], "css": "select[name=genre2]", "css_only": True,
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


def distrokid_radios(song: dict) -> list[tuple[str, str]]:   # 今は dk_sections が使う。下書きの説明用に残す
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


def ai_parts(song: dict) -> list[str]:
    """DistroKid の「Which parts of this song were AI-generated?」で選ぶもの（決定 61。オーナーの指示）。
    この仕組みの曲は、歌詞（Claude と Suno）・作曲・音（声と演奏）をすべて AI で作るので：
      歌のある曲 → lyrics・music・all_audio／インスト → music・all_audio
    人が歌った・弾いた部分がある曲は、ブリーフや作業セットに "ai_parts": ["lyrics", "music", "part_audio"] のように書くと、それを使う"""
    if song.get("ai_parts"):
        return list(song["ai_parts"])
    inst = bool(song.get("instrumental")) or not str(song.get("lyrics") or "").strip()
    return (["music"] if inst else ["lyrics", "music"]) + ["all_audio"]


AI_PART_TEXT = {"lyrics": r"^\s*lyrics\b", "music": r"^\s*music\b", "all_audio": r"^\s*all of the audio", "part_audio": r"^\s*part of the audio"}
AI_PARTS_JS = r"""([want, rx]) => {
  const vis = e => e && e.offsetParent !== null;
  const title = [...document.querySelectorAll('body *')].filter(e => vis(e) && !e.closest('#postdog-overlay')
      && /which parts of this song were ai-generated/i.test(e.textContent || '') && e.children.length < 40);
  if (!title.length) return null;
  // 質問と選択肢を含む、いちばん小さい囲み
  let box = title.reduce((a, b) => (a.textContent.length <= b.textContent.length ? a : b));
  for (let i = 0; i < 6 && box && !/all of the audio/i.test(box.textContent || ''); i++) box = box.parentElement;
  if (!box) return null;
  const isOn = e => { if (!e) return false; if (e.type === 'checkbox' || e.type === 'radio') return e.checked;
      if (e.getAttribute('aria-checked') === 'true' || e.getAttribute('aria-pressed') === 'true') return true;
      const inp = e.querySelector && e.querySelector('input[type=checkbox],input[type=radio]'); if (inp) return inp.checked;
      return /\b(selected|active|checked|is-checked)\b/.test(e.className || ''); };
  const own = e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
  const cands = [...box.querySelectorAll('label, [role=checkbox], [role=option], button, li, div')].filter(vis);
  const out = [];
  for (const [key, pat] of Object.entries(rx)) {
    const re = new RegExp(pat, 'i');
    // その言葉で始まる、いちばん小さい部品（中にチェックの箱があればそれも）
    const hits = cands.filter(e => re.test(own(e)) && own(e).length < 120);
    if (!hits.length) { out.push({key, found: false}); continue; }
    const el = hits.reduce((a, b) => (own(a).length <= own(b).length ? a : b));
    const inp = el.matches('input') ? el : (el.querySelector('input[type=checkbox],input[type=radio]')
        || (el.htmlFor && document.getElementById(el.htmlFor)) || (el.closest('label') && el.closest('label').querySelector('input')));
    const target = inp || el.closest('[role=checkbox]') || el;
    const on = isOn(inp || target);
    const should = want.includes(key);
    let clicked = false;
    if (on !== should && (should || key === 'part_audio' || key === 'all_audio')) {
      (inp && !vis(inp) ? (el.closest('label') || el) : target).click(); clicked = true; }
    out.push({key, found: true, was: on, clicked});
  }
  // 小窓（ダイアログ）なら、その中の保存のボタンを押す（ページの Continue は押さない）
  const dlg = box.closest('[role=dialog], [aria-modal=true], .swal2-popup, .modal, [class*=modal], [class*=Modal], [class*=dialog]');
  let saved = '';
  if (dlg) {
    const btn = [...dlg.querySelectorAll('button, [role=button], input[type=button], input[type=submit]')].filter(vis)
      .find(b => b.id !== 'distroDoneButtonWithAPI' && /^(save|done|confirm|ok|apply|update|save selections|continue)$/i.test(own(b) || b.value || ''));
    if (btn) { setTimeout(() => btn.click(), 400); saved = own(btn) || btn.value; }
  }
  return {parts: out, saved, modal: !!dlg};
}"""


def answer_ai_parts(sess: Session, song: dict, wait_ms: int = 0) -> str:
    """「Which parts of this song were AI-generated?」が出ていれば、ai_parts(song) のとおりに選ぶ。選んだことの一文（出ていなければ ""）。
    wait_ms：「AI を使った？ → Yes」を押した直後など、質問が遅れて出るのを待つ長さ"""
    want = ai_parts(song)
    r = None
    for _ in range(max(1, wait_ms // 400)):
        try:
            r = sess.page.evaluate(AI_PARTS_JS, [want, AI_PART_TEXT])
        except Exception:  # noqa: BLE001
            r = None
        if r or wait_ms <= 0:
            break
        sess.page.wait_for_timeout(400)
    if not r or not (any(p.get("clicked") for p in r["parts"]) or r.get("saved")):
        return ""   # 出ていない・もう選んである（見張りが 2 秒ごとに呼ぶので、何もしなかったときは黙る）
    ja = {"lyrics": "歌詞", "music": "作曲", "all_audio": "音のすべて", "part_audio": "音の一部"}
    missing = [ja[p["key"]] for p in r["parts"] if not p["found"] and p["key"] in want]
    msg = ("AI で作った部分 → " + "・".join(ja[k] for k in want) + " を選びました"
           + (f"（「{r['saved']}」も押しました）" if r.get("saved") else "")
           + (f"。見つからなかった選択肢：{'・'.join(missing)}" if missing else ""))
    print("   … " + msg)
    sess.page.wait_for_timeout(800)
    return msg


def artist_id_radios(song: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """Artist already in 〜?：前の曲がまだ配信されていない（DistroKid が『0 件』と出す）組は「No（新しいページ）」。
    前の曲のページが見つかっている組は、そのページを選ぶ（Spotify は配信の確認で記録した ID）。わからなければ Gon さんに"""
    try:
        import publish
        sp = (publish.artist_links(song["slug"]).get("spotify") or {}).get("id")
    except Exception:  # noqa: BLE001
        sp = None
    pick, texts = [], []
    for name, svc in ARTIST_ID_RADIOS:
        if name == "spotifyArtistID" and sp:   # 前の曲が Spotify に出ている：Yes を選び、下の欄にアーティストの URI を入れる
            pick.append((f"Artist already in Spotify? → Yes（前の曲のページ）", f'input[type=radio][name="{name}"][value=""]'))
            texts.append((f"Spotify のアーティスト URI（spotify:artist:{sp}）", "input.myCustomSpotifyUri", f"spotify:artist:{sp}"))
            continue
        # デビュー曲は前の曲が無いので必ず No（新しいページ）。2 曲目からは DistroKid が「0 件」と出したときだけ No
        cond = "" if song.get("kind") == "debut" else '[class*="ZeroMatches"]'
        pick.append((f"Artist already in {svc}? → No（新しいページ）", f'input[type=radio][name="{name}"][value="new"]{cond}'))
    return pick, texts


# 役割の候補。Singing & vocals は同じ名前の書き方の違いだけ。Sampled artist などほかの役割には決して逃げない（オーナーの指示。決定 71）
ROLE_CANDS = {"Singing & vocals": ["Singing & vocals", "Singing and vocals", "Singing & vocal", "Singer & vocals", "Singer & vocal"],
              "Vocals": ["Vocals", "Lead Vocals", "Vocalist", "Lead Vocalist", "Vocal", "Singer", "Performer"],
              "Performer": ["Performer", "Main Performer", "Featured Performer", "Musician", "Artist"],
              "Producer": ["Producer", "Music Producer", "Record Producer"]}


def _role_key(v) -> str:
    """役割の名前を比べる形に（大文字小文字・「and」と「&」・空白の違いを同じにする）"""
    return re.sub(r"[^a-z0-9&]", "", re.sub(r"\band\b", "&", str(v).lower()))


def role_is(value, role: str) -> bool:
    """箱の中身 value が role（とその書き方の違い）と同じか。前の方が同じだけ（Sampled artist と Sampler など）は違うとみる"""
    got = _role_key(value)
    return bool(got) and any(got == _role_key(c) for c in ROLE_CANDS.get(role, [role]))


# 打って選ぶ箱を打ったときに出る選択肢（DistroKid の作りの名前が違っても拾う）。ふつうの選ぶ箱の中の option は除く
OPTIONS_JS = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  document.querySelectorAll('[data-pd-opt]').forEach(e => e.removeAttribute('data-pd-opt'));
  for (const css of ['.dk-searchable-select__option', '[role=option]', '[class*="option" i]:not(option):not(select)', '[role=listbox] li, [class*="menu" i] li']) {
    const hits = [...document.querySelectorAll(css)].filter(e => vis(e) && !e.closest('#postdog-overlay') && e.tagName !== 'OPTION'
        && !e.querySelector('[role=option], [class*="option" i]') && (e.innerText || '').trim() && (e.innerText || '').length < 60);
    if (hits.length) { hits.slice(0, 80).forEach((e, i) => e.setAttribute('data-pd-opt', i)); return hits.slice(0, 80).map(e => e.innerText.trim()); }
  }
  return [];
}"""


def _visible_options(sess: Session) -> list:
    try:
        names = sess.page.evaluate(OPTIONS_JS)
    except Exception:  # noqa: BLE001
        return []
    return [sess.page.locator(f'[data-pd-opt="{i}"]').first for i in range(len(names))]


def _searchable_pick(sess: Session, inp, text: str) -> bool:
    """DistroKid の「打って選ぶ箱」（Track credits の役割）：候補の言葉を打って画面に出た選択肢を集め、
    候補と名前がまったく同じものだけを選ぶ（近い名前には逃げない。Sampled artist などが選ばれないように。決定 71）。
    選んだあとに箱の中身を読んで確かめ、違えば打ち直す・キーボード（↓と Enter）で選ぶ。出た選択肢は記録する（決定 48）"""
    cands = ROLE_CANDS.get(text, [text])
    seen: dict[str, str] = {}   # 選択肢の名前 → 打った言葉
    # 名前ぜんぶ（例 Singing & vocals）を先に打ち、出なければ最後の言葉の頭（vocals など）で探す（決定 70）
    for word in dict.fromkeys([*cands[:4], *(c.split()[-1][:6] for c in cands)]):
        inp.click()
        inp.fill("")
        inp.type(word, delay=40)
        sess.page.wait_for_timeout(700)
        for o in _visible_options(sess):
            n = (o.inner_text() or "").strip()
            if n:
                seen.setdefault(n, word)
    names = list(seen)
    pick = next((n for c in cands for n in names if _role_key(n) == _role_key(c)), None)
    if names:
        _remember_options("roles", names)
    if pick is None:
        if names:
            print("   － 役割の選択肢に「" + text + "」がありませんでした。出た選択肢：" + "・".join(names[:20]))
        inp.fill("")
        return False
    for typed in dict.fromkeys([pick, seen[pick]]):
        inp.click()
        inp.fill("")
        inp.type(typed, delay=40)
        sess.page.wait_for_timeout(700)
        hit = next((o for o in _visible_options(sess) if (o.inner_text() or "").strip() == pick), None)
        if hit is not None:
            hit.click()
            sess.page.wait_for_timeout(500)
            if role_is(_searchable_value(sess, inp), text):
                return True
    # 押しても入らない作り：↓で選択肢を 1 つずつ動かし、光っている選択肢が同じ名前のときだけ Enter
    inp.click()
    inp.fill("")
    inp.type(pick, delay=40)
    sess.page.wait_for_timeout(700)
    for _ in range(12):
        inp.press("ArrowDown")
        sess.page.wait_for_timeout(150)
        lit = sess.page.evaluate("""() => { const e = document.querySelector('[role=option][aria-selected=true], [class*="option" i][class*="focus" i],'
            + '[class*="option" i][class*="highlight" i], [class*="option" i][class*="active" i]'); return e ? e.innerText.trim() : ''; }""")
        if lit and _role_key(lit) == _role_key(pick):
            inp.press("Enter")
            sess.page.wait_for_timeout(500)
            break
    return role_is(_searchable_value(sess, inp), text)


NEAR_JS = r"""([tag, nameCss, labelRx]) => {
  const vis = e => e && e.offsetParent !== null;
  const SEL = 'input.dk-searchable-select__input';
  let start = null;
  if (nameCss) start = [...document.querySelectorAll(nameCss)].find(vis);
  else {   // 見出し（Primary genre など）の文字で始まる、いちばん小さい部品
    const rx = new RegExp(labelRx, 'i');
    const hits = [...document.querySelectorAll('label, legend, h1, h2, h3, h4, h5, h6, p, span, div, strong, b')]
      .filter(e => vis(e) && !e.closest('#postdog-overlay') && rx.test((e.innerText || '').trim()) && (e.innerText || '').length < 80);
    start = hits.length ? hits.reduce((a, b) => (a.innerText.length <= b.innerText.length ? a : b)) : null;
  }
  if (!start) return false;
  // 上の階層へたどり、打って選ぶ箱が 1 つだけ入っているいちばん小さい囲みを探す（見つけた部品そのものから。見出しを含む囲みが選ばれていることがある）
  let box = start;
  for (let i = 0; i < 7 && box; i++, box = box.parentElement) {
    // 見出しから探すとき：ふつうの選ぶ箱（select）がある・Track credits の欄まで入った囲みは、別の欄なのでやめる
    if (!nameCss && ([...box.querySelectorAll('select')].some(vis) || box.querySelector('input[name=performer-name], input[name=producer-name]'))) return false;
    const inside = [...box.querySelectorAll(SEL)].filter(vis);
    if (inside.length === 1) { document.querySelectorAll(`[data-pd-near="${tag}"]`).forEach(e => e.removeAttribute('data-pd-near'));
      inside[0].setAttribute('data-pd-near', tag); return true; }
    if (inside.length > 1) return false;   // 別の欄の箱まで入ってしまった
  }
  return false;
}"""


def searchable_near(sess: Session, tag: str, name_css: str | None = None, label_rx: str | None = None):
    """名前の欄（name_css）か見出し（label_rx）の近くにある「打って選ぶ箱」。無ければ None"""
    try:
        if sess.page.evaluate(NEAR_JS, [tag, name_css, label_rx]):
            return sess.page.locator(f'[data-pd-near="{tag}"]').first
    except Exception:  # noqa: BLE001
        pass
    return None


def _searchable_value(sess: Session, inp) -> str:
    """打って選ぶ箱で、いま選ばれているもの（箱の中の文字か、箱の近くに出ている選んだものの名前）"""
    try:
        return inp.evaluate("""e => { if (e.value) return e.value.trim();
            const box = e.closest('.dk-searchable-select') || e.parentElement;
            const shown = box && box.querySelector('[class*="single-value" i], [class*="selected" i], [class*="value" i]');
            return ((shown ? shown.innerText : '') || '').replace(/\\s+/g, ' ').trim(); }""")
    except Exception:  # noqa: BLE001
        return ""


# 見出し（Primary genre など）の近くの「押すと一覧が開く箱」か「選ぶ箱」を探して印を付ける（決定 67）
TRIGGER_JS = r"""([tag, labelRx, otherRx]) => {
  const vis = e => e && e.offsetParent !== null;
  const rx = new RegExp(labelRx, 'i'), other = new RegExp(otherRx, 'i');
  const hits = [...document.querySelectorAll('label, legend, h1, h2, h3, h4, h5, h6, p, span, div, strong, b')]
    .filter(e => vis(e) && !e.closest('#postdog-overlay') && rx.test((e.innerText || '').trim()) && (e.innerText || '').length < 80);
  if (!hits.length) return null;
  const start = hits.reduce((a, b) => (a.innerText.length <= b.innerText.length ? a : b));
  // 押す場所は、外側の囲みより中のボタンを先に（囲みの真ん中を押しても、ボタンに当たらないことがある）
  const kinds = [['select', 'select'], ['[role=combobox], [aria-haspopup]', 'menu'], ['button, [role=button]', 'menu'],
                 ['input:not([type=hidden]):not([type=radio]):not([type=checkbox])', 'menu'],
                 ['[class*=select i], [class*=dropdown i], [class*=picker i]', 'menu']];
  let box = start;
  for (let i = 0; i < 6 && box; i++, box = box.parentElement) {
    if (box !== start && other.test(box.innerText || '')) return null;   // 別の欄（Secondary genre など）まで入ってしまった
    for (const [css, kind] of kinds) {
      const el = [...box.querySelectorAll(css)].find(e => vis(e) && !e.closest('#postdog-overlay') && e !== start && !e.contains(start)
          && !/continue|submit|save|done|cancel|remove|delete|edit|upload|learn more|add\b|select all/i.test((e.innerText || e.getAttribute('aria-label') || '').trim()));
      if (el) {
        document.querySelectorAll(`[data-pd-gt="${tag}"]`).forEach(e => e.removeAttribute('data-pd-gt'));
        el.setAttribute('data-pd-gt', tag);
        return {kind: el.tagName === 'SELECT' ? 'select' : kind, text: (el.tagName === 'SELECT' ? ((el.selectedOptions[0] || {}).text || '')
                : (el.innerText || el.value || '')).replace(/\s+/g, ' ').trim().slice(0, 60)};
      }
    }
  }
  return null;
}"""

# 開いた一覧の中から、その名前の選択肢を探して印を付ける（いちばん小さい、名前がちょうど同じもの → 名前で始まるもの）
OPTION_JS = r"""([want, tag]) => {
  const vis = e => e && e.offsetParent !== null;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const w = norm(want);
  const trig = document.querySelector(`[data-pd-gt="${tag}"]`);
  const cands = [...document.querySelectorAll('[role=option], li, [class*=option i], [class*=item i], a, button, div, span')]
    .filter(e => vis(e) && !e.closest('#postdog-overlay') && e !== trig && !(trig && trig.contains(e)) && e.children.length < 4);
  const exact = cands.filter(e => norm(e.innerText) === w);
  const pick = (exact.length ? exact : cands.filter(e => norm(e.innerText).startsWith(w) && norm(e.innerText).length < w.length + 25))
    .sort((a, b) => a.innerText.length - b.innerText.length)[0];
  document.querySelectorAll('[data-pd-opt]').forEach(e => e.removeAttribute('data-pd-opt'));
  if (!pick) return '';
  pick.setAttribute('data-pd-opt', '1');
  return pick.innerText.trim();
}"""


def pick_dropdown_near(sess: Session, key: str, label_rx: str, want: str) -> bool | None:
    """見出しの近くの「選ぶ箱」か「押すと一覧が開く箱」で want を選ぶ。None：そういう箱が無い／True：選べた／False：選べなかった"""
    other = r"secondary genre" if key == "genre1" else r"primary genre"
    try:
        r = sess.page.evaluate(TRIGGER_JS, [key, label_rx, other])
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    trig = sess.page.locator(f'[data-pd-gt="{key}"]').first
    bare = lambda t: _norm(re.sub(r"[▾▼⌄˅›»\u25bc\u25be]+", " ", str(t or "")))  # noqa: E731  開く印を外して比べる
    if bare(r.get("text")) == _norm(want):
        return True
    try:
        if r["kind"] == "select":
            opts = trig.evaluate("e => [...e.options].map(o => o.text.trim())")
            hit = next((o for o in opts if _norm(o) == _norm(want)), None) or next((o for o in opts if _norm(o).startswith(_norm(want))), None)
            if hit is None:
                return False
            trig.select_option(label=hit)
            ok = _norm(trig.evaluate("e => (e.selectedOptions[0] || {}).text || ''")) == _norm(hit)
        else:
            trig.click()
            sess.page.wait_for_timeout(700)
            if sess.page.evaluate("() => { const a = document.activeElement; return !!a && a.tagName === 'INPUT' && a.type !== 'radio' && a.type !== 'checkbox'; }"):
                sess.page.keyboard.type(want.split("/")[0], delay=40)   # 一覧に絞り込みの欄があれば打つ
                sess.page.wait_for_timeout(700)
            name = sess.page.evaluate(OPTION_JS, [want, key])
            if not name:
                sess.page.keyboard.press("Escape")
                return False
            sess.page.locator('[data-pd-opt="1"]').first.click()
            sess.page.wait_for_timeout(600)
            again = sess.page.evaluate(TRIGGER_JS, [key, label_rx, other]) or {}
            ok = bare(again.get("text")) in (_norm(want), _norm(name))
        if ok:
            print(f"   … {({'genre1': 'Primary genre', 'genre2': 'Secondary genre'}).get(key, key)} の一覧から「{want}」を選びました")
        return ok
    except Exception:  # noqa: BLE001
        return False


def fill_genre_boxes(sess: Session, fields: list[dict]) -> tuple[list[str], list[str]]:
    """Primary genre・Secondary genre が「打って選ぶ箱」の画面：見出しの近くの箱に、DistroKid の一覧の名前で入れる（決定 61）。
    ふつうの選ぶ箱（select）だけの画面では何もしない（fill_fields が入れる）"""
    done, manual = [], []
    for key, rx in (("genre1", r"^(primary genre|genre)\b"), ("genre2", r"^secondary genre")):
        f = next((x for x in fields if x["key"] == key), None)
        want = (f or {}).get("chosen") or (f or {}).get("value")
        if not want:
            continue
        box = searchable_near(sess, key, label_rx=rx)
        if box is None:   # 打って選ぶ箱ではない：押すと一覧が開く箱・見出しの近くの選ぶ箱で入れる（決定 67）
            r = pick_dropdown_near(sess, key, rx, want)
            if r is True:
                done.append(f"{f['label']}（{want}）")
            elif r is False:
                manual.append(f"{f['label']}：{want}（一覧から選べませんでした）")
            continue
        if _norm(_searchable_value(sess, box)).startswith(_norm(want)):
            done.append(f"{f['label']}（{want}）")
            continue
        ok = False
        try:
            for word in dict.fromkeys([want, want.split("/")[0], want.split()[0]]):
                box.click()
                box.fill("")
                box.type(word, delay=40)
                sess.page.wait_for_timeout(700)
                opts = _visible_options(sess)
                hit = next((o for o in opts if _norm(o.inner_text()) == _norm(want)), None) or \
                    next((o for o in opts if _norm(o.inner_text()).startswith(_norm(want))), None)
                if hit is not None:
                    hit.click()
                    sess.page.wait_for_timeout(500)
                    ok = True
                    break
        except Exception:  # noqa: BLE001
            ok = False
        (done if ok else manual).append(f"{f['label']}（{want}）" if ok else f"{f['label']}：{want}（打って選ぶ箱に入りませんでした）")
        if ok:
            print(f"   … {f['label']} を打って選ぶ箱で「{want}」にしました")
    if manual:   # 選べなかった：本物の画面に合わせて直すため、画面の部品の一覧を残す（決定 67）
        try:
            from datetime import datetime as _dt
            p = save_parts(sess, "distrokid")
            if p and p.exists():
                keep = p.with_name(f"distrokid_parts_genre_{_dt.now():%Y%m%d_%H%M%S}.txt")
                keep.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"   記録：ジャンルを選べなかった画面の部品の一覧を保存しました → {keep}")
        except Exception:  # noqa: BLE001
            pass
    return done, manual


# Apple Music の「Additional requirements」：Performer と Producer の credits の欄は、「＋」を押すまで出ない画面がある（決定 66）
PLUS_JS = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const rx = /apple music requires at least one performer and producer|add credits for each song/i;
  const t = [...document.querySelectorAll('body *')].filter(e => vis(e) && !e.closest('#postdog-overlay')
      && rx.test(e.textContent || '') && e.children.length < 30);
  if (!t.length) return {found: false};
  let box = t.reduce((a, b) => (a.textContent.length <= b.textContent.length ? a : b));
  const bad = /remove|delete|close|trash|minus|cancel|edit/i;   // 消す・閉じるの印は押さない
  const label = b => [(b.getAttribute('aria-label') || ''), (b.title || ''), (typeof b.className === 'string' ? b.className : '')].join(' ');
  const isPlus = b => { const s = (b.innerText || b.value || '').trim();
    if (bad.test(label(b)) || bad.test(s)) return false;
    return s === '+' || s === '＋' || /^[+＋]\s*\w*/.test(s) || /^add( a)? credit/i.test(s) || /\b(add|plus)\b/i.test(label(b)); };
  for (let i = 0; i < 6 && box; i++, box = box.parentElement) {
    const btns = [...box.querySelectorAll('button, [role=button], a, span, i, div')].filter(b => vis(b) && b !== box && isPlus(b));
    const pick = btns.find(b => b.matches('button, [role=button], a')) || btns[0];
    if (pick) {
      document.querySelectorAll('[data-pd-plus]').forEach(e => e.removeAttribute('data-pd-plus'));
      pick.setAttribute('data-pd-plus', '1');
      return {found: true, plus: true, text: (pick.innerText || pick.getAttribute('aria-label') || '').trim().slice(0, 40)};
    }
  }
  return {found: true, plus: false};
}"""

# credits の欄が小窓（ダイアログ）の中にあれば、その中の保存のボタンを押す（ページの Continue は押さない）
CREDITS_SAVE_JS = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const inp = [...document.querySelectorAll('input[name=performer-name], input[name=producer-name]')].find(vis);
  const dlg = inp && inp.closest('[role=dialog], [aria-modal=true], .swal2-popup, .modal, [class*=modal], [class*=Modal], [class*=dialog]');
  if (!dlg) return '';
  const own = b => (b.innerText || b.value || '').replace(/\s+/g, ' ').trim();
  const btn = [...dlg.querySelectorAll('button, [role=button], input[type=button], input[type=submit]')].filter(vis)
    .find(b => b.id !== 'distroDoneButtonWithAPI' && /^(save|save credits|done|apply|confirm|ok|add|add credits|update)$/i.test(own(b)));
  if (!btn) return '';
  btn.click();
  return own(btn);
}"""


# Apple Music の「Add credits for each song」の欄：＋で出る行の「役割の箱」と、そのすぐ下の「名前の欄」を組にして印を付ける（決定 70）
APPLE_ROWS_JS = r"""() => {
  const vis = e => e && e.offsetParent !== null;
  const rx = /apple music requires at least one performer and producer|add credits for each song/i;
  const t = [...document.querySelectorAll('body *')].filter(e => vis(e) && !e.closest('#postdog-overlay')
      && rx.test(e.textContent || '') && e.children.length < 30);
  if (!t.length) return null;
  let block = t.reduce((a, b) => (a.textContent.length <= b.textContent.length ? a : b));
  const ROLE = 'input.dk-searchable-select__input, select, [role=combobox]';
  // 役割の箱が入るまで外側へ広げる（＋を押す前は箱が無いので、＋のある囲みまで）。ジャンルや言語の箱まで入ったら広げすぎ
  let scope = block;
  for (let i = 0; i < 6 && scope.parentElement; i++) {
    const up = scope.parentElement;
    if (up.querySelector('select[name=genre1], select[name=language], input[name=bandname]')) break;
    scope = up;
    if ([...scope.querySelectorAll(ROLE)].some(vis)) break;
  }
  const dlg = [...document.querySelectorAll('[role=dialog], [aria-modal=true], .swal2-popup, [class*=modal i]')].find(d => vis(d) && [...d.querySelectorAll(ROLE)].some(vis));
  if (dlg) scope = dlg;   // ＋で小窓が開く作り
  const status = /not yet complete/i.test(block.closest('div, section') ? (scope.innerText || '') : '') ? 'incomplete' : '';
  document.querySelectorAll('[data-pd-crole],[data-pd-cname]').forEach(e => { e.removeAttribute('data-pd-crole'); e.removeAttribute('data-pd-cname'); });
  // 欄を上から順に読む。1 行は「役割の欄（Performer の下）」→「名前の欄（そのさらに下）」（オーナーの説明。決定 74）。
  // 役割の欄は、選ぶ箱・打って選ぶ箱のほか、ふつうの文字の欄のこともある：欄の説明（placeholder など）で見分け、
  // 見分けられなければ、上から 2 つずつ（上＝役割・下＝名前）の組にする
  const hint = e => [e.placeholder, e.getAttribute('aria-label'), e.name, e.id, (e.labels && e.labels[0] ? e.labels[0].innerText : '')].join(' ').toLowerCase();
  const fields = [...scope.querySelectorAll('input, select, [role=combobox]')].filter(e => vis(e) && !e.closest('#postdog-overlay')
      && !/^(file|checkbox|radio|hidden|submit|button|date|number)$/i.test(e.type || ''));
  const isRole = e => e.matches(ROLE) || (/role|select|search|instrument|choose|type to/.test(hint(e)) && !/name/.test(hint(e)));
  const isName = e => !isRole(e) && /name|credit/.test(hint(e));
  const pairs = [];
  for (let k = 0; k < fields.length; k++) {
    const f = fields[k];
    if (isName(f)) continue;
    const nx = fields[k + 1];
    if (nx && !isRole(nx)) { pairs.push([f, nx]); k++; }
    else if (isRole(f)) pairs.push([f, null]);
  }
  const rows = [];
  pairs.forEach(([rl, nm], i) => {
    rl.setAttribute('data-pd-crole', String(i));
    if (nm) nm.setAttribute('data-pd-cname', String(i));
    // 行の見出し（Performer・Producer）：役割の箱と名前の欄をどちらも含む、いちばん小さい囲みの文字
    let row = rl.parentElement;
    while (row && nm && !row.contains(nm)) row = row.parentElement;
    const txt = ((row || rl.parentElement).innerText || '').toLowerCase();
    const label = /producer/.test(txt) && !/performer/.test(txt) ? 'producer' : (/performer/.test(txt) && !/producer/.test(txt) ? 'performer' : '');
    const val = rl.tagName === 'SELECT' ? ((rl.selectedOptions[0] || {}).text || '') : (rl.value || '');
    const kind = rl.tagName === 'SELECT' ? 'select' : (rl.matches('input.dk-searchable-select__input, [role=combobox]') ? 'searchable' : 'text');
    rows.push({i, label, kind, role: val.trim(), name: nm ? nm.value : null});
  });
  return {rows, status};
}"""


def _pick_role(sess: Session, box, role: str) -> bool:
    """役割の箱（打って選ぶ箱か、ふつうの選ぶ箱）で role を選び、選べたかを箱の中身で確かめる"""
    cands = ROLE_CANDS.get(role, [role])
    is_select = box.evaluate("e => e.tagName === 'SELECT'")
    is_plain = box.evaluate("e => e.tagName === 'INPUT' && !e.matches('input.dk-searchable-select__input, [role=combobox]')")
    if is_plain:   # ふつうの文字の欄：打って、候補が出れば同じ名前の候補を押す。出なければ打った文字のまま（決定 74）
        box.click()
        box.fill("")
        box.type(cands[0], delay=40)
        sess.page.wait_for_timeout(700)
        hit = next((o for o in _visible_options(sess) if _role_key(o.inner_text() or "") == _role_key(cands[0])), None)
        if hit is not None:
            hit.click()
            sess.page.wait_for_timeout(400)
        if not role_is(box.input_value(), role):
            box.fill(cands[0])
        return role_is(box.input_value(), role)
    if is_select:
        opts = box.evaluate("e => [...e.options].map(o => o.text.trim())")
        hit = next((o for c in cands for o in opts if _role_key(o) == _role_key(c)), None)
        if hit is None:
            return False
        box.select_option(label=hit)
        return role_is(box.evaluate("e => (e.selectedOptions[0] || {}).text || ''"), role)
    return _searchable_pick(sess, box, role) and role_is(_searchable_value(sess, box), role)


def fill_apple_rows(sess: Session, want: dict[str, tuple[str, str]]) -> tuple[list[str], list[str]]:
    """Apple Music の credits の欄（＋で出る行）に入れる。want：{"performer": (役割, 名前), "producer": (役割, 名前)}。
    行の見出しで Performer・Producer を見分け、見出しが無ければ上から Performer → Producer。足りなければ ＋ をもう一度押す"""
    done, manual = [], []
    filled: set[str] = set()
    for attempt in range(3):
        try:
            r = sess.page.evaluate(APPLE_ROWS_JS)
        except Exception:  # noqa: BLE001
            return done, manual
        if r is None:
            return done, manual   # Apple Music の credits の欄が無い画面
        rows = r["rows"]
        free = [k for k in ("performer", "producer") if k not in filled]
        plan = []
        used: set[int] = set()
        for row in rows:   # 見出し（Performer・Producer）がある行は見出しどおり。無い行は上から Performer → Producer
            k = row["label"] if row["label"] in free else (free[0] if not row["label"] and free else None)
            if k and k in free:
                plan.append((row, k))
                free.remove(k)
                used.add(row["i"])
        for row in rows:   # まだ足りない（どの行も見出しが Performer など）：残りの行に入れる。もう入っている行は使わない（決定 74）
            if free and row["i"] not in used and not (row["role"] and row.get("name") and any(role_is(row["role"], want[x][0]) for x in want)):
                plan.append((row, free.pop(0)))
                used.add(row["i"])
        for row, k in plan:
            role, name = want[k]
            box = sess.page.locator(f'[data-pd-crole="{row["i"]}"]').first
            ok = _pick_role(sess, box, role)
            nm = sess.page.locator(f'[data-pd-cname="{row["i"]}"]')
            if nm.count() and name:
                nm.first.fill(name)
                ok = ok and _norm(_read(nm.first)) == _norm(name)
            else:
                ok = False
            label = {"performer": "Performer", "producer": "Producer"}[k]
            if ok:
                filled.add(k)
                done.append(f"Apple Music の credits の {label}（{role}）")
                print(f"   … Apple Music の credits：{label} を「{role}」・名前ありで入れました")
            else:
                print(f"   － Apple Music の credits：{label} の役割「{role}」か名前が入りませんでした（いま：{row['role'] or '空'}）")
        if not free or len(filled) == 2:
            break
        if len(rows) >= 2:   # 行はもうある（入らなかっただけ）：＋を押して行を増やさない
            break
        try:   # 足りない行がある：＋をもう一度押して、新しい行を出す
            pr = sess.page.evaluate(PLUS_JS)
        except Exception:  # noqa: BLE001
            break
        if not (pr or {}).get("plus"):
            break
        sess.page.locator('[data-pd-plus="1"]').first.click()
        print(f"   … Apple Music の credits の「{pr.get('text') or '＋'}」を押して、行を足しました")
        sess.page.wait_for_timeout(1500)
    for k in ("performer", "producer"):
        if k not in filled:
            role, name = want[k]
            manual.append(f"Apple Music の credits の {k.title()}：役割 {role}（Sampled artist などほかの役割は選ばない）／名前 {name}（＋を押した行に入れてください）")
    return done, manual


def open_apple_credits(sess: Session) -> str:
    """Apple Music の credits の欄が「＋」の後ろに隠れていれば、＋を押して出す。
    ""：その欄が無い画面／"ok"：欄が出た／"noplus"：＋が見つからない／"nofields"：押しても欄が出ない"""
    def ready() -> bool:
        # 見えている欄だけを数える（閉じた小窓の中に、隠れた欄が前からあることがある）
        return bool(sess.page.locator("input[name=performer-name]:visible").count() and sess.page.locator("input[name=producer-name]:visible").count())
    if ready():
        return "ok"

    def rows_now() -> int:   # ＋で出た行（役割の欄＋名前の欄）がもういくつあるか（決定 74）
        try:
            return len((sess.page.evaluate(APPLE_ROWS_JS) or {}).get("rows") or [])
        except Exception:  # noqa: BLE001
            return 0
    have = rows_now()
    if have >= 2:
        return "ok"   # 行はもうある：＋を押して行を増やさない（入れ直しのたびに行が増えていた）
    for k in range(2 - have):   # 1 回で Performer と Producer の両方が出る作りと、1 行ずつ出る作りの両方に合わせる
        try:
            r = sess.page.evaluate(PLUS_JS)
        except Exception:  # noqa: BLE001
            return ""
        if not r.get("found"):
            return ""
        if not r.get("plus"):
            print("   … Apple Music の credits の欄の「＋」が見つかりませんでした")
            return "noplus"
        sess.page.locator('[data-pd-plus="1"]').first.click()
        print(f"   … Apple Music の credits の「{r.get('text') or '＋'}」を押しました（{k + 1} 回目）")
        sess.page.wait_for_timeout(1500)
        if ready() or rows_now() >= 2:
            return "ok"
    return "ok" if sess.page.locator("input[name=performer-name]:visible, input[name=producer-name]:visible").count() else "nofields"


def credit_rows(song: dict) -> list[tuple[str, str, str, str]]:
    """Track credits に入れるもの：（行・役割・名前の欄・名前）。
    Performer：インストの組は本名（演奏した人）・役割 Performer、それ以外はアーティスト名・役割 Singing & vocals。Producer：全組とも本名（.env の OWNER_LEGAL_NAME）。決定 44・62"""
    import publish
    from _common import load_artist
    art = load_artist(song["slug"], song.get("label_slug"), missing_ok=True) or {}
    owner = os.environ.get("OWNER_LEGAL_NAME", "").strip()
    inst = bool(song.get("instrumental")) or (art.get("lyrics") or {}).get("mode") == "instrumental"
    return [("Performer", "Performer" if inst else publish._credit_role(art), "input[name=performer-name]", owner if inst else song["artist"]),
            ("Producer", "Producer", "input[name=producer-name]", owner)]


def fill_credits(sess: Session, song: dict) -> tuple[list[str], list[str]]:
    """Track credits（Apple の決まり：Performer と Producer を 1 人ずつ以上）。Performer＝組の名前、Producer＝本名"""
    opened = open_apple_credits(sess)   # 「＋」の後ろに隠れている credits の欄を出す（決定 66）
    if opened in ("noplus", "nofields"):
        try:   # 本物の画面に合わせて直すため、画面の部品の一覧を残す
            p = save_parts(sess, "distrokid")
            if p and p.exists():
                keep = p.with_name(f"distrokid_parts_{song['id']}_credits.txt")
                keep.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"   記録：Apple Music の credits の欄の画面の部品の一覧を保存しました → {keep}")
        except Exception:  # noqa: BLE001
            pass
    rows = credit_rows(song)
    done, manual = [], []
    for i, (who, role, name_css, name) in enumerate(rows):
        name_loc = sess.page.locator(name_css + ":visible")
        if not name_loc.count():
            name_loc = sess.page.locator(name_css)
        if not name_loc.count():
            continue   # この画面には Track credits の欄が無い
        ok = True
        try:
            # 役割の箱は「名前の欄と同じ行」にあるものを使う。画面の上からの順番では選ばない
            # （Primary genre も同じ「打って選ぶ箱」になっていると、1 つ目はジャンルの箱になってしまう。決定 61）
            box = searchable_near(sess, f"role-{i}", name_css=name_css)
            if box is None:   # 同じ行に見つからない作り：打って選ぶ箱が役割の 2 つだけの画面なら、これまでどおり上からの順番で
                allbox = sess.page.locator("input.dk-searchable-select__input:visible")
                box = allbox.nth(i) if allbox.count() == len(rows) else None
            if box is None or not _searchable_pick(sess, box, role):
                ok = False
            if name:
                name_loc.first.fill(name)
            else:
                ok = False
        except Exception:  # noqa: BLE001
            ok = False
        if ok and _norm(_read(name_loc.first)) == _norm(name):
            done.append(f"Track credits の {who}（{role}）")
        else:
            manual.append(f"Track credits の {who}：役割 {role}（ほかの役割は選ばない）／名前 {name or '（.env の OWNER_LEGAL_NAME が空）'}")
    # Apple Music の「Add credits for each song」の欄：＋で出た行（役割の箱＋その下の名前の欄）にも入れる（決定 70）。
    # 「＋」を押すと、役割は DistroKid の初めの値（Sampler など）・名前は空の行が出るので、ここで役割と名前を入れ直す
    a_done, a_manual = fill_apple_rows(sess, {"performer": (rows[0][1], rows[0][3]), "producer": (rows[1][1], rows[1][3])})
    done += a_done
    if a_done:
        manual = [m for m in manual if not m.startswith("Track credits")]   # Apple の行に入ったなら、ふつうの欄の分は足りている
    manual += a_manual
    if opened in ("noplus", "nofields") and not done:
        manual.append("Apple Music の credits（Additional requirements）：「＋」を押して Performer と Producer を入れてください"
                      "（欄の作りが読めませんでした。画面の部品の一覧を保存したので、Claude に見せると次から自動にできます）")
    if done and len(done) == len(rows):
        try:   # 小窓の中の credits なら、その小窓の保存を押す（ページの Continue は押さない）
            saved = sess.page.evaluate(CREDITS_SAVE_JS)
            if saved:
                print(f"   … Apple Music の credits の小窓の「{saved}」を押しました")
                sess.page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001
            pass
    return done, manual


def _visible_first(loc):
    """同じ名前の丸が画面に見えるものと隠れたもの（作り直しの残り）の両方にあるとき、見えている方"""
    for i in range(min(loc.count(), 8)):
        try:
            if loc.nth(i).is_visible() or loc.nth(i).evaluate("e => { const l = e.closest('label') || e.parentElement; return !!(l && l.offsetParent); }"):
                return loc.nth(i)
        except Exception:  # noqa: BLE001
            pass
    return loc.first


def set_radios(sess: Session, items: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """選ぶ（見えない作りのボタンは、要素そのものを押す）。選べたもの／画面に無かったもの"""
    done, missing = [], []
    for label, css in items:
        loc = sess.page.locator(css)
        if loc.count() == 0:
            missing.append(label)
            continue
        el = _visible_first(loc)
        try:
            if not el.is_checked():
                try:
                    el.check(timeout=3000)
                except Exception:  # noqa: BLE001  見た目だけの丸の下に隠れたボタン
                    # まず丸の文字（label）を人と同じように押す。スクリプトで押すと取り消す作りがある（決定 71）
                    try:
                        lab = el.locator("xpath=ancestor::label[1]")
                        if not lab.count():
                            rid = el.get_attribute("id")
                            lab = sess.page.locator(f'label[for="{rid}"]') if rid else lab
                        if lab.count() and lab.first.is_visible():
                            lab.first.click(timeout=3000)
                            sess.page.wait_for_timeout(300)
                    except Exception:  # noqa: BLE001
                        pass
                    if not el.is_checked():
                        el.evaluate("e => { e.click(); e.dispatchEvent(new Event('change', {bubbles: true})); }")
            (done if el.is_checked() else missing).append(label)
        except Exception:  # noqa: BLE001
            missing.append(label)
    return done, missing


# DistroKid の差し戻し（Validation Errors）の欄。まだ見ていないものだけを読む
VALIDATION_JS = r"""() => {
  // 見出しだけでなく、エラーの行まで入ったいちばん小さい囲み。前に読んだ囲み（とその外側・内側）は読まない
  // 画面に見えている部品だけ（ページの中の script の文字を読まない。決定 68）
  const all = [...document.querySelectorAll('body *')].filter(e => !/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(e.tagName)
      && e.offsetParent !== null && !e.closest('#postdog-overlay')
      && !e.closest('[data-postdog-seen]') && !e.querySelector('[data-postdog-seen]')
      && /Validation Errors/.test(e.innerText || '') && (e.innerText || '').split('\n').filter(x => x.trim()).length >= 2);
  if (!all.length) return '';
  const el = all.reduce((a, b) => (a.innerText.length <= b.innerText.length ? a : b));
  el.dataset.postdogSeen = '1';
  return el.innerText.slice(0, 3000);
}"""


# DistroKid のページの中に置く見張り：Artist already in の欄が作り直されて「No（新しいページ）」が消えたら、選び直す。
# システムが Chrome から離れたあとも、そのページが開いている間は働く（DistroKid の検索がとても遅いことがあるため。決定 48）
GUARD_NO_JS = r"""([names, rxs]) => {
  window.__pdNo = names;
  const [rx, ctx, danger] = rxs.map(x => new RegExp(x, 'i'));
  const confirmBox = () => { const box = document.querySelector('.swal2-popup'); const t = box ? (box.innerText || '') : '';
      if (box && !danger.test(t) && (rx.test(t) || ctx.test(t))) { const b = box.querySelector('.swal2-confirm'); if (b) b.click(); } };
  window.__pdTries = window.__pdTries || {};
  const fix = () => { for (const n of window.__pdNo || []) {
      const no = document.querySelector(`input[type=radio][name="${n}"][value="new"]`);
      if (no && no.checked) { window.__pdTries[n] = 0; continue; }
      // スクリプトで押しても戻される丸は 2 回までにする（取り合いにならないよう、あとは外から文字を押して直す。決定 71）
      if (no && (window.__pdTries[n] || 0) < 2) { window.__pdTries[n] = (window.__pdTries[n] || 0) + 1;
        no.click(); no.dispatchEvent(new Event('change', {bubbles: true})); setTimeout(confirmBox, 600); } } };
  if (!window.__pdObs) {
    window.__pdObs = new MutationObserver(() => { clearTimeout(window.__pdT); window.__pdT = setTimeout(fix, 400); });
    window.__pdObs.observe(document.body, {childList: true, subtree: true});
  }
  fix();
}"""


# 「No（新しいページ）」を選んだときに DistroKid が出す確認の小窓（SweetAlert）。文がこの内容のときだけ OK を押す（決定 49）
NEW_PAGE_CONFIRM = r"new (artist )?(page|profile)|don'?t (yet )?have|no (other )?releases|create (a )?new|separate (artist )?(page|profile)|not grouped|won'?t be grouped"
# 「No」を自分で押した直後に出た小窓は、その「No」の確認とみて、アーティスト・ページ・サイトの話なら OK を押す（Apple の文は Spotify と違うため。決定 50）。
# 消す・取り消すなどの小窓は、どちらのときも押さない
PICKED_CONTEXT = r"artist|page|profile|spotify|apple|itunes|youtube|google|music|sure|confirm"
DANGER = r"delete|remove|discard|erase|lose|cancel (the|this|your) (release|upload)|refund"
MODAL_JS = r"""([rx, ctx, danger, loose]) => {
  const box = document.querySelector('.swal2-popup.swal2-show, .swal2-popup');
  if (!box || box.offsetParent === null && getComputedStyle(box).position !== 'fixed') return null;
  const text = (box.innerText || '').replace(/\s+/g, ' ').trim();
  const ok = !new RegExp(danger, 'i').test(text) && (new RegExp(rx, 'i').test(text) || (loose && new RegExp(ctx, 'i').test(text)));
  if (ok) { const b = box.querySelector('.swal2-confirm'); if (b) b.click(); }
  return {text: text.slice(0, 400), clicked: ok};
}"""


def confirm_new_page(sess: Session, picked: bool = False) -> list[str]:
    """確認の小窓が出ていれば、文を読み、新しいページを作る確認なら OK を押す。出ていた文の一覧。
    picked：いま「No」を押した直後（小窓が遅れて出ることがあるので 4 秒まで待ち、アーティスト・ページの話なら OK）"""
    out = []
    for i in range(4):
        r = None
        for _ in range(14 if picked and i == 0 else 2):
            sess.page.wait_for_timeout(300)
            r = sess.page.evaluate(MODAL_JS, [NEW_PAGE_CONFIRM, PICKED_CONTEXT, DANGER, picked])
            if r:
                break
        if not r:
            break
        out.append(("OK を押しました：" if r["clicked"] else "押していません（内容が違う）：") + r["text"])
        print("   … DistroKid の確認の小窓：" + out[-1][:300])
        if not r["clicked"]:
            break
    return out


# ジャンルの見張り：システムが選んだジャンルが、ほかの何かに書き換えられたら選び直す。変わったときは記録する。
# Gon さんがその箱を自分で触ったら、その箱の見張りはやめる（決定 49）
GUARD_SEL_JS = r"""(want) => {
  window.__pdSel = Object.assign(window.__pdSel || {}, want);
  window.__pdLog = window.__pdLog || [];
  if (!window.__pdSelHooked) {
    window.__pdSelHooked = true;
    const mine = e => e.target && e.target.name && window.__pdSel && (e.target.name in window.__pdSel);
    for (const ev of ['mousedown', 'keydown']) document.addEventListener(ev, e => { if (mine(e) && e.isTrusted) delete window.__pdSel[e.target.name]; }, true);
    document.addEventListener('change', e => { if (e.target && /genre/.test(e.target.name || ''))
        window.__pdLog.push(`${new Date().toISOString().slice(11, 19)} ${e.target.name}=${(e.target.selectedOptions[0] || {}).text}${e.isTrusted ? '（手）' : ''}`); }, true);
    const fix = () => { for (const [name, text] of Object.entries(window.__pdSel || {})) {
        for (const e of document.querySelectorAll(`select[name="${name}"]`)) {
          const cur = (e.selectedOptions[0] || {}).text || '';
          if (cur.trim() === text) continue;
          const o = [...e.options].find(o => o.text.trim() === text); if (!o) continue;
          window.__pdLog.push(`${new Date().toISOString().slice(11, 19)} ${name} が「${cur}」に変わっていたので「${text}」に戻した`);
          e.value = o.value; e.dispatchEvent(new Event('change', {bubbles: true}));
          if (window.jQuery) { try { window.jQuery(e).val(o.value).trigger('change'); } catch (x) {} } } } };
    window.__pdSelFix = fix;
    new MutationObserver(() => { clearTimeout(window.__pdSelT); window.__pdSelT = setTimeout(fix, 500); })
      .observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'value']});
    setInterval(fix, 2000);
  }
}"""


def guard_genres(sess: Session, fields: list[dict]) -> None:
    want = {f["css"].split("=")[1].rstrip("]"): f["chosen"] for f in fields
            if f["key"] in ("genre1", "genre2") and f.get("chosen") and f.get("css")}
    if want:
        try:
            sess.page.evaluate(GUARD_SEL_JS, want)
        except Exception:  # noqa: BLE001
            pass


def guard_artist_no(sess: Session, song: dict, a_done: list[str]) -> None:
    """「No」を選んだ Artist already in を、ページの中の見張りで守る（デビュー曲と、DistroKid が 0 件と出した組だけ）"""
    names = [n for n, svc in ARTIST_ID_RADIOS if any(f"in {svc}?" in d and "→ No" in d for d in a_done)]
    if song.get("kind") == "debut":
        names = [n for n, _ in ARTIST_ID_RADIOS]
    if names:
        try:
            sess.page.evaluate(GUARD_NO_JS, [names, [NEW_PAGE_CONFIRM, PICKED_CONTEXT, DANGER]])
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# DistroKid の登録画面を「上から順に 1 項目ずつ」入れて確かめる（決定 68）
# 本物の画面の並び（2026-10-22 の画面の部品の一覧から）。前は同じ項目を何か所もの処理で入れ直していたのを、ここ 1 か所にまとめた。
# 入れる・確かめる・入れ直す（読み返し・待っている間の見張り・差し戻し）は、すべてこの表を使う。
# 画面の上から：前に出した？ → アーティスト名 → Artist already in ×5 → 配信日 → 言語 → ジャンル → ジャケット →
#   曲名 → featured → バージョン → 音源 → Dolby → カバー？ → Songwriter → Explicit → Clean → インスト？ → AI → 試聴 → credits
# そのあとは Gon さん：Extras は押さない → 宣誓のチェック → Continue
# ---------------------------------------------------------------------------
class DK:
    """1 曲ぶんの入力の材料（欄の決め方・入れたもの）"""
    def __init__(self, sess: Session, song: dict, fields: list[dict]):
        self.sess, self.song = sess, song
        self.f = {x["key"]: x for x in fields}
        self.fields = fields
        self.notes: dict[str, str] = {}   # 項目ごとのひとこと（ポップアップの一覧に出す）


def _radio(css: str):
    def do(c: DK) -> None:
        set_radios(c.sess, [("", css)])

    def ok(c: DK):
        loc = c.sess.page.locator(css)
        return None if not loc.count() else _visible_first(loc).is_checked()
    return do, ok


def _field(*keys: str):
    def do(c: DK) -> None:
        fs = [c.f[k] for k in keys if k in c.f and c.f[k].get("value") not in (None, "")]
        if fs:
            fill_fields(c.sess, "distrokid", fs, fresh=False)

    def ok(c: DK):
        fs = [c.f[k] for k in keys if k in c.f and c.f[k].get("value") not in (None, "")]
        if not fs:
            return None
        return not verify(c.sess, fs)
    return do, ok


def _sec_artist_do(c: DK) -> None:
    fill_fields(c.sess, "distrokid", [c.f["artist"]], fresh=False)
    c.sess.page.keyboard.press("Tab")   # 名前の欄から離れる（DistroKid はここで同じ名前を探し始め、Artist already in を作り直す）
    c.sess.settle(3000)


NO_TEXT_JS = r"""(name) => {
  const vis = e => e && e.offsetParent !== null;
  const r = [...document.querySelectorAll(`input[type=radio][name="${name}"][value="new"]`)].find(e => vis(e) || vis(e.parentElement)) ||
            document.querySelector(`input[type=radio][name="${name}"][value="new"]`);
  if (!r) return '';
  const cands = [r.closest('label'), r.id && document.querySelector(`label[for="${r.id}"]`), r.parentElement, r.nextElementSibling].filter(Boolean);
  // 丸のまわりに無ければ、その質問の囲みの中で「No -」で始まる文字の部品
  let box = r.parentElement;
  for (let i = 0; i < 4 && box; i++, box = box.parentElement) {
    const t = [...box.querySelectorAll('label, span, div, p')].find(e => vis(e) && /^no\s*[-–—]/i.test((e.innerText || '').trim()));
    if (t) { cands.push(t); break; }
  }
  const el = cands.find(e => vis(e) && /^no\b/i.test((e.innerText || '').trim())) || cands.find(vis);
  if (!el) return '';
  document.querySelectorAll('[data-pd-notext]').forEach(e => e.removeAttribute('data-pd-notext'));
  el.setAttribute('data-pd-notext', '1');
  return (el.innerText || '').trim().slice(0, 60);
}"""


def _click_no_text(sess: Session, css: str) -> None:
    m = re.search(r'name="([^"]+)"', css)
    if not m:
        return
    try:
        t = sess.page.evaluate(NO_TEXT_JS, m.group(1))
        if t:
            sess.page.locator('[data-pd-notext="1"]').first.click()
            print(f"   … 「{t}」の文字を押して選びました")
    except Exception:  # noqa: BLE001
        pass


def _sec_ids_do(c: DK) -> None:
    items, texts = artist_id_radios(c.song)
    for item in items:   # 1 つずつ選び、確認の小窓（新しいページの話）に OK を押してから次へ。取り消されたら 3 回までやり直す
        for t in range(4):
            loc = c.sess.page.locator(item[1])
            if not loc.count() or _visible_first(loc).is_checked():
                break   # もう選ばれている丸は押さない（押し直すと取り消す作りがある）
            if t:
                print(f"   … {item[0]} が取り消されました。やり直します（{t} 回目）")
            if t == 0:
                set_radios(c.sess, [item])
            else:   # 2 回目から：丸ではなく「No - …」の文字そのものを人と同じように押す（丸が隠れている・スクリプトで押すと戻る作り。決定 70・71）
                _click_no_text(c.sess, item[1])
            confirm_new_page(c.sess, picked=True)
            c.sess.page.wait_for_timeout(800)
            if t:
                c.sess.settle(1500)
    for label, css, val in texts:   # 前の曲のページがわかっている組：「Yes」の下の欄にページの住所を入れる
        loc = c.sess.page.locator(css)
        if loc.count() and loc.first.is_visible():
            loc.first.fill(val)
    names = [n for n, _ in ARTIST_ID_RADIOS if any(css.startswith(f'input[type=radio][name="{n}"][value="new"]') for _, css in items)]
    if names:   # 「No」にした丸は、ページの中の見張りで守る（DistroKid の検索が遅れて作り直しても選び直す）
        try:
            c.sess.page.evaluate(GUARD_NO_JS, [names, [NEW_PAGE_CONFIRM, PICKED_CONTEXT, DANGER]])
        except Exception:  # noqa: BLE001
            pass


def _sec_ids_ok(c: DK):
    items, _ = artist_id_radios(c.song)
    states = []
    for _, css in items:
        loc = c.sess.page.locator(css)
        if loc.count():
            states.append(_visible_first(loc).is_checked())
    if not states:   # 2 曲目からで、0 件と出ていない：どれを選ぶかは Gon さん（Yes の候補を選ぶ）
        c.notes["ids"] = "前の曲のページの候補が出ていれば、それ（Yes）を選んでください"
        return None
    return all(states)


def _sec_genre1_do(c: DK) -> None:
    f = c.f["genre1"]
    fill_fields(c.sess, "distrokid", [f], fresh=False)          # 1 欄の名前の選ぶ箱
    if verify(c.sess, [f]):
        fill_genre_boxes(c.sess, [x for x in c.fields if x["key"] == "genre1"])   # 2・3 打って選ぶ箱・押すと一覧が開く箱
    if "subgenre" in c.f:
        fill_fields(c.sess, "distrokid", [c.f["subgenre"]], fresh=False)
    guard_genres(c.sess, c.fields)


def _genre_ok(key: str, label_rx: str):
    def ok(c: DK):
        f = c.f.get(key)
        if not f or not f.get("value"):
            return None
        sel = c.sess.page.locator(f["css"])
        if sel.count() and any(sel.nth(i).is_visible() for i in range(sel.count())):
            if key == "genre2":   # 任意の欄：一覧に候補が 1 つも無ければ、空のままでよい
                opts = [_norm(o) for o in sel.first.evaluate("e => [...e.options].map(o => o.text)")]
                if not any(_norm(x) in opts for x in [f["value"], *(f.get("alts") or [])] if x):
                    c.notes[key] = "一覧に合うジャンルが無いので空のまま（任意の欄）"
                    return None
            return not verify(c.sess, [f])
        want = f.get("chosen") or f["value"]
        box = searchable_near(c.sess, key, label_rx=label_rx)
        if box is not None:
            return _norm(_searchable_value(c.sess, box)).startswith(_norm(want))
        try:
            r = c.sess.page.evaluate(TRIGGER_JS, [key, label_rx, r"secondary genre" if key == "genre1" else r"primary genre"])
        except Exception:  # noqa: BLE001
            r = None
        if not r:
            return False if key == "genre1" else None
        return _norm(re.sub(r"[▾▼⌄˅›»]+", " ", r.get("text") or "")) == _norm(want)
    return ok


def _sec_genre2_do(c: DK) -> None:
    f = c.f.get("genre2")
    if not f or not f.get("value"):
        return
    fill_fields(c.sess, "distrokid", [f], fresh=False)
    if verify(c.sess, [f]):
        fill_genre_boxes(c.sess, [f])
    guard_genres(c.sess, c.fields)


def _sec_ai_do(c: DK) -> None:
    set_radios(c.sess, [("", 'input[type=radio][name^="ai_gate_"][value="1"]')])
    answer_ai_parts(c.sess, c.song, wait_ms=2400)   # 「どの部分を AI で作ったか」の質問（決定 61）


def _sec_credits_do(c: DK) -> None:
    fill_credits(c.sess, c.song)


def _sec_credits_ok(c: DK):
    try:
        st = c.sess.page.evaluate(STATE_JS)
    except Exception:  # noqa: BLE001
        return False
    (_, p_role, _, p_name), (_, r_role, _, r_name) = credit_rows(c.song)
    try:   # Apple Music の「＋」で出た行：役割が同じ名前（Sampled artist などは「まだ」）で、名前も入っているか（決定 70・71）
        ar = c.sess.page.evaluate(APPLE_ROWS_JS)
    except Exception:  # noqa: BLE001
        ar = None
    rows = (ar or {}).get("rows") or []
    if rows:
        has = lambda role, name: any(role_is(x["role"], role) and _norm(x.get("name") or "") == _norm(name) for x in rows)  # noqa: E731
        if not (has(p_role, p_name) and has(r_role, r_name)):
            bad = [x["role"] for x in rows if x["role"] and not (role_is(x["role"], p_role) or role_is(x["role"], r_role))]
            c.notes["credits"] = "役割が違う行があります：" + "・".join(bad) if bad else "Performer か Producer の行が足りません"
            return False
    if st.get("performers", 0) == 0 and st.get("producers", 0) == 0:
        if rows:
            return True
        try:   # ＋の後ろに隠れている作り：Apple Music の欄があれば「まだ」
            return False if (c.sess.page.evaluate(PLUS_JS) or {}).get("found") else None
        except Exception:  # noqa: BLE001
            return None
    return (_norm(st.get("performer")) == _norm(p_name) and role_is(st.get("performer_role"), p_role)
            and _norm(st.get("producer")) == _norm(r_name) and role_is(st.get("producer_role"), r_role))


def dk_sections(song: dict) -> list[dict]:
    """登録画面の項目を、本物の画面の上から順に。do：入れる／ok：入っているか（None＝この画面に無い）／again：見張りで入れ直してよいか"""
    inst = "1" if song.get("instrumental") else "0"
    S_ = lambda key, name, pair, again=True: {"key": key, "name": name, "do": pair[0], "ok": pair[1], "again": again}  # noqa: E731
    return [
        S_("previous", "前に出したことがある？ → No", _radio('input[type=radio][name^="previouslyReleased_"][value="0"]')),
        {"key": "artist", "name": "アーティスト名", "do": _sec_artist_do, "ok": _field("artist")[1], "again": False},
        {"key": "ids", "name": "Artist already in（Spotify・Apple Music・YouTube Music・Instagram・Facebook）", "do": _sec_ids_do, "ok": _sec_ids_ok, "again": True},
        S_("release", "配信日", _field("release")),
        S_("language", "言語", _field("language")),
        {"key": "genre1", "name": "Primary genre", "do": _sec_genre1_do, "ok": _genre_ok("genre1", r"^(primary genre|genre)\b"), "again": True},
        {"key": "genre2", "name": "Secondary genre", "do": _sec_genre2_do, "ok": _genre_ok("genre2", r"^secondary genre"), "again": True},
        S_("cover", "ジャケット", _field("cover"), again=False),
        S_("title", "曲名", _field("title")),
        S_("feat", "曲名に featured artist を出す？ → No", _radio('input[type=radio][name^="feat_"][value="0"]')),
        S_("version", "バージョン → ふつうの版", _radio('input[type=radio][name^="version_"][value=""]')),
        S_("audio", "音源", _field("audio"), again=False),
        S_("dolby", "Dolby Atmos → No", _radio('input[type=radio][name="dolby_1"][value="0"]')),
        S_("coversong", "カバー曲？ → No（自分で書いた曲）", _radio('input[type=radio][name^="coversong_"][value="0"]')),
        S_("songwriter", "Songwriter（役割・名・姓）", _field("songwriter_role", "songwriter_first", "songwriter_middle", "songwriter_last")),
        S_("explicit", "Explicit → No", _radio('input[type=radio][name^="explicit_"][value="0"]')),
        S_("cleaned", "Clean 版が別にある？ → No", _radio('input[type=radio][name^="cleaned_"][value="0"]')),
        S_("instrumental", "インスト？ → " + ("歌詞なし" if inst == "1" else "歌詞あり"),
           _radio(f'input[type=radio][name^="instrumental_"][value="{inst}"]')),
        {"key": "ai", "name": "AI を使った？ → Yes（作った部分：" + "・".join({"lyrics": "Lyrics", "music": "Music", "all_audio": "All of the audio",
                                                                            "part_audio": "Part of the audio"}[k] for k in ai_parts(song)) + "）",
         "do": _sec_ai_do, "ok": _radio('input[type=radio][name^="ai_gate_"][value="1"]')[1], "again": True},
        S_("preview", "試聴の始まり → サービスにまかせる", _radio('input[type=radio][name="previewStart_1"][value="no"]')),
        {"key": "credits", "name": "Track credits（Performer・Producer）", "do": _sec_credits_do, "ok": _sec_credits_ok, "again": True},
    ]


def dk_check(c: DK, secs: list[dict]) -> dict[str, bool | None]:
    out = {}
    for s in secs:
        try:
            out[s["key"]] = s["ok"](c)
        except Exception:  # noqa: BLE001
            out[s["key"]] = False
    return out


def dk_fill(c: DK, secs: list[dict], show_steps: bool = True) -> None:
    """上から順に入れる（工程の表示は「k/N：項目の名前」）"""
    for i, s in enumerate(secs, 1):
        if show_steps:
            step(i, len(secs) + 2, s["name"])
        try:
            s["do"](c)
        except BrowserClosed:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"   － 「{s['name']}」を入れられませんでした（{str(e).splitlines()[0][:120]}）")


def dk_refill(c: DK, secs: list[dict], quiet: bool = False, rounds: int = 3) -> dict[str, bool | None]:
    """読み返して、入っていない項目だけを上から入れ直す（最大 rounds 回）。最後の状態"""
    st = dk_check(c, secs)
    for i in range(rounds):
        bad = [s for s in secs if st[s["key"]] is False and s["again"]]
        if not bad:
            break
        if not quiet:
            print(f"   … 入っていない項目を、上から入れ直します（{i + 1} 回目）：" + "・".join(s["name"][:30] for s in bad))
        for s in bad:
            try:
                s["do"](c)
            except BrowserClosed:
                raise
            except Exception:  # noqa: BLE001
                pass
        c.sess.settle(1500)
        st = dk_check(c, secs)
    return st


def dk_checklist(secs: list[dict], st: dict, c: DK) -> list[str]:
    """ポップアップに出す、画面の上から順の一覧（✓ 入った／✕ 入っていない／－ この画面に無い）"""
    mark = {True: "✓", False: "✕", None: "－"}
    lines = []
    for i, s in enumerate(secs, 1):
        tail = "（入っていません。画面で入れてください）" if st[s["key"]] is False else ("（この画面に無い）" if st[s["key"]] is None and s["key"] not in c.notes else "")
        if s["key"] in c.notes and st[s["key"]] is not True:
            tail = f"（{c.notes[s['key']]}）"
        lines.append(f"{mark[st[s['key']]]} {i}. {s['name']}{tail}")
    return lines


def same_page_ready(song: dict) -> bool:
    """2 曲目からの曲を、デビュー曲と同じ Spotify のアーティストのページにまとめられるか（ページの ID がわかっているか）。
    デビュー曲が配信される前に 2 曲目を「No（新しいページ）」で出すと、ページが別々にできるおそれがある（決定 53）"""
    if song.get("kind") == "debut":
        return True
    try:
        import publish
        from _common import load_artist
        a = load_artist(song["slug"], song.get("label_slug"), missing_ok=True) or {}
        return bool((a.get("distribution") or {}).get("spotify_uri") or (publish.artist_links(song["slug"]).get("spotify") or {}).get("id"))
    except Exception:  # noqa: BLE001
        return False


def waiting_for_distrokid() -> list[dict]:
    """DistroKid に登録する段階の曲（公開セットができて、まだ登録していない）。デビュー曲を先に、曲 ID の順。
    2 曲目からの曲は、その組の Spotify のアーティストのページがわかってから（決定 53）"""
    out = [x for x in _songs.all_songs() if _songs.state(x).get("status") == "ready"]
    held = [x for x in out if not same_page_ready(x)]
    if held:
        print("   ・Spotify のアーティストのページがまだわからないので、あとにする曲：" + "・".join(f"{x['artist']}「{x['title']}」" for x in held))
    return sorted([x for x in out if x not in held], key=lambda x: (x.get("kind") != "debut", x["id"]))


def run_distrokid_all(sess: Session) -> int:
    """残りの曲を続けて入れる（決定 51）。1 曲ごとに Gon さんが Continue と「登録した」を押すと、次の曲を入れ始める"""
    songs = waiting_for_distrokid()
    if not songs:
        print("【結果】DistroKid に登録を待っている曲はありません")
        return 0
    print("▶ DistroKid に続けて入れる曲：" + "・".join(f"{x['artist']}「{x['title']}」" for x in songs))
    done = []
    for i, x in enumerate(songs):
        nxt = songs[i + 1] if i + 1 < len(songs) else None
        print(f"\n▶ {i + 1}/{len(songs)} 曲目：{x['artist']}「{x['title']}」（{x['id']}）")
        progress(i + 1, len(songs), x)
        code = run_distrokid(sess, x["id"], f"{nxt['artist']}「{nxt['title']}」" if nxt else "")
        if _songs.state(x).get("status") == "registered":
            done.append(x)
        if code == 3:
            break
    rest = [x for x in songs if x not in done]
    print(f"【結果】登録した {len(done)} 曲：" + ("・".join(x["artist"] for x in done) or "なし")
          + ("／まだの曲：" + "・".join(x["artist"] for x in rest) if rest else "／残りはありません"))
    return 0 if not rest else 2


def lyrics_for(song: dict) -> str:
    """配信サイトに出す歌詞（公開セットの 歌詞_配信用.txt。無ければ作る）。インストは空"""
    try:
        import store_lyrics
        return store_lyrics.write_for_song(song).get("store") or ""
    except Exception:  # noqa: BLE001
        return ""


def lyrics_todo() -> list[dict]:
    """歌詞をまだ出していない曲（DistroKid に登録済み・配信中で、歌詞がある）"""
    out = [x for x in _songs.all_songs() if _songs.state(x).get("status") in ("registered", "live")
           and not _songs.state(x).get("lyrics_submitted_at") and not x.get("instrumental") and str(x.get("lyrics") or "").strip()]
    return sorted(out, key=lambda x: (x.get("kind") != "debut", x["id"]))


def _open_song_lyrics(sess: Session, song: dict) -> bool:
    """歌詞の画面で、この曲の歌詞を入れる欄を開く。曲名の行を探して押す（「Submit」などは押さない）。欄が見えたら True"""
    box = "textarea, [contenteditable=true]"
    for _ in range(2):
        if _visible(sess.page.locator(box)):
            return True
        for role in ("link", "button"):
            loc = sess.page.get_by_role(role, name=re.compile(re.escape(song["title"]), re.I))
            for i in range(min(loc.count(), 5)):
                el = loc.nth(i)
                try:
                    name = (el.inner_text(timeout=1000) or "").strip()
                    if el.is_visible() and not NEVER.search(name.replace(song["title"], "")):
                        el.click(timeout=3000)
                        sess.settle(2500)
                        break
                except Exception:  # noqa: BLE001
                    pass
            if _visible(sess.page.locator(box)):
                return True
    return _visible(sess.page.locator(box))


def run_lyrics(sess: Session, song_id: str, nxt: str = "") -> int:
    """登録した曲の歌詞を、DistroKid の歌詞の画面に入れる（決定 52）。「Submit」は Gon さんが押す。
    0：出した／2：まだ出していない／3：続けての入力をやめる"""
    song = _songs.by_id(song_id)
    if song is None:
        sys.exit(f"[エラー] 曲 {song_id} が見つかりません")
    text = lyrics_for(song)
    if not text:
        print(f"【結果】{song['artist']}「{song['title']}」は歌詞がありません（インスト）")
        return 0
    step(1, 4, "DistroKid の歌詞の画面を開いています")
    sess.goto(SITES["distrokid_lyrics"])
    if not sess.ensure_login("distrokid"):
        print("【結果】まだ終わっていません：DistroKid にログインできていません")
        return 2
    sess.settle(2000)
    step(2, 4, "この曲の歌詞の欄を探しています")
    for _ in range(3):
        if _open_song_lyrics(sess, song):
            break
        sess.show("この曲の歌詞の画面を開いてください",
                  [f"{song['artist']}「{song['title']}」の歌詞を入れる画面（歌詞の欄がある画面）を開いて、「続ける」を押してください。",
                   "DistroKid の「Enhance your music」→「Lyrics」、または登録した曲のページの「Lyrics」から開けます。"],
                  [{"id": "continue", "label": "続ける", "primary": True}, {"id": "stop", "label": "やめる"},
                   {"id": "copy_parts", "label": "画面の部品をコピー（Claude に貼る用）"}])
        if sess.wait({"action": "continue"}).get("action") != "continue":
            print("【結果】まだ終わっていません：歌詞の画面が開けませんでした")
            return 2
    f = {"key": "lyrics", "label": "歌詞（Lyrics）", "type": "text", "value": text, "largest_textarea": True,
         "hints": ["Lyrics", "Paste your lyrics", "Enter lyrics", "Type lyrics", "歌詞"], "avoid": r"search|title|name|email|artist",
         "tip": "DistroKid の歌詞を入れる大きな欄をクリックしてください"}
    step(3, 4, "歌詞を入れています")
    done, manual = fill_fields(sess, "distrokid_lyrics", [f])
    bad = verify(sess, [f]) if done else ["歌詞（Lyrics）"]
    notes = []
    try:
        import store_lyrics
        notes = [n for n in store_lyrics.write_for_song(song)["notes"]][:6]
    except Exception:  # noqa: BLE001
        pass
    later = ([{"id": "stop", "label": f"この曲は後で（次の曲へ：{nxt}）"}, {"id": "quit", "label": "続けての入力をここでやめる"}] if nxt
             else [{"id": "stop", "label": "閉じる（まだ出さない）"}])
    sess.show("歌詞を入れました" if not bad else "歌詞の欄に入りませんでした",
              [f"{song['artist']}「{song['title']}」",
               "Suno の札（[Verse] など）と音の指示を外し、配信サイトの決まりに合わせた歌詞です（公開セットの 歌詞_配信用.txt）。",
               *(["⚠ 欄に入りませんでした。公開セットの 歌詞_配信用.txt をコピーして貼ってください"] if bad else []),
               *(["確かめること：", *notes] if notes else []),
               "曲を聞きながら読んで、違うところがあれば直してから「Submit」を押してください（押すのは Gon さんだけ）。",
               "押したら「出した」を押すと記録されます。"],
              [{"id": "submitted", "label": "出した", "primary": True}, *later,
               {"id": "copy_parts", "label": "画面の部品をコピー（Claude に貼る用）"}])
    step(4, 4, "Gon さんの確認待ち（Submit と「出した」を押す）")
    m = sess.wait({"action": "stop"})
    if m.get("action") == "submitted":
        st = _songs.state(song)
        st["lyrics_submitted_at"] = date.today().isoformat()
        _songs.save_state(song, st)
        print(f"【結果】{song['artist']}「{song['title']}」の歌詞を出したことを記録しました（DistroKid の確認に 1 日ほど、各サイトに出るまで 1〜2 週間）")
        return 0
    if nxt and m.get("action") in ("quit", "closed"):
        return 3
    print("【結果】まだ終わっていません：歌詞の「出した」はまだ押されていません")
    return 2


def run_lyrics_all(sess: Session) -> int:
    songs = lyrics_todo()
    if not songs:
        print("【結果】歌詞を出していない曲はありません")
        return 0
    print("▶ 歌詞を続けて入れる曲：" + "・".join(f"{x['artist']}「{x['title']}」" for x in songs))
    for i, x in enumerate(songs):
        nxt = songs[i + 1] if i + 1 < len(songs) else None
        print(f"\n▶ {i + 1}/{len(songs)} 曲目：{x['artist']}「{x['title']}」（{x['id']}）")
        progress(i + 1, len(songs), x)
        if run_lyrics(sess, x["id"], f"{nxt['artist']}「{nxt['title']}」" if nxt else "") == 3:
            break
    rest = lyrics_todo()
    print("【結果】" + ("歌詞は全部出しました" if not rest else "歌詞がまだの曲：" + "・".join(x["artist"] for x in rest)))
    return 0 if not rest else 2


STATE_JS = r"""() => { const vis = x => x.offsetParent !== null;
    const v = s => { const e = [...document.querySelectorAll(s)].find(vis); return e ? (e.type === 'radio' ? null : e.value) : '（欄が無い）'; };
    const r = n => { const all = [...document.querySelectorAll(`input[type=radio][name^="${n}"]`)];
        const on = all.filter(e => e.checked).map(e => (e.value || 'Yes') + (vis(e) || vis(e.parentElement || e) ? '' : '（隠れ）'));
        return (on.join('+') || '（未選択）') + `/${all.length}個`; };
    const g = [...document.querySelectorAll('select[name=genre1]')].map(x => `${vis(x) ? '見える' : '隠れ'}:${(x.selectedOptions[0] || {}).text || ''}/${x.options.length}件`).join(' ');
    // Track credits の役割（打って選ぶ箱）：Performer・Producer の名前の欄の近くの役割の箱の中身
    const role = n => { const e = [...document.querySelectorAll(`input[name=${n}]`)].find(vis); if (!e) return '（欄が無い）';
        let box = e.parentElement; for (let i = 0; i < 5 && box && !box.querySelector('.dk-searchable-select__input, .dk-searchable-select'); i++) box = box.parentElement;
        const sel = box && box.querySelector('.dk-searchable-select');
        const inp = box && box.querySelector('.dk-searchable-select__input');
        return ((inp && inp.value) || (sel && sel.innerText.trim().slice(0, 40)) || '（空）'); };
    const appleUri = v('input.myCustomAppleUri, input[name*="AppleUri" i], input[class*="AppleUri" i]');
    return {release: v('input[name=releaseDate]'), genre1: v('select[name=genre1]'), genre1_boxes: g, first: v('input[name=songwriter_real_name_first1]'),
            last: v('input[name=songwriter_real_name_last1]'), performer: v('input[name=performer-name]'), performer_role: role('performer-name'),
            producer: v('input[name=producer-name]'), producer_role: role('producer-name'),
            performers: document.querySelectorAll('input[name=performer-name]').length, producers: document.querySelectorAll('input[name=producer-name]').length,
            spotify: r('spotifyArtistID'), apple: r('appleArtistID'), apple_uri: appleUri, youtube: r('googleArtistID'),
            instagram: r('instagramProfileArtistID'), facebook: r('facebookProfileArtistID'),
            modal: ((document.querySelector('.swal2-popup.swal2-show') || {}).innerText || '').replace(/\s+/g, ' ').slice(0, 120)}; }"""


SCREENS = OUT / "browser" / "screens"


def shoot(sess: Session, song: dict, when: str) -> Path | None:
    """ページ全体の画面の写しを残す（ダッシュボードのポップアップから見られる。決定 69）。
    when：filled（入れ終わったとき）・rejected（差し戻されたとき）・refilled（入れ直したあと）"""
    try:
        SCREENS.mkdir(parents=True, exist_ok=True)
        p = SCREENS / f"{song['id']}_{when}.png"
        sess.page.screenshot(path=str(p), full_page=True)
        print(f"   記録：画面の写し（{ {'filled': '入れ終わったとき', 'rejected': '差し戻されたとき', 'refilled': '入れ直したあと'}.get(when, when) }）→ {p}")
        return p
    except Exception:  # noqa: BLE001
        return None


def log_state(sess: Session, when: str) -> None:
    """画面の状態をログに 1 行で残す（本名は「（あり）」とだけ書く）"""
    try:
        snap = sess.page.evaluate(STATE_JS)
        glog = sess.page.evaluate("() => (window.__pdLog || []).slice(-12)")
        if glog:
            print("   記録：ジャンルの変わり方 → " + " ／ ".join(glog))
        print(f"   記録：{when} → " + "・".join(f"{k}={'（あり）' if k in ('first', 'last', 'producer') and v and v != '（欄が無い）' else v}"
                                         for k, v in snap.items()))
    except Exception as e:  # noqa: BLE001
        print(f"   記録：画面を読めませんでした（{e}）")


def run_distrokid(sess: Session, song_id: str, nxt: str = "") -> int:
    """1 曲を DistroKid に入れる。0：登録した／2：まだ登録しない／3：続けての入力をここでやめる（nxt があるとき：次の曲の名前）"""
    song = _songs.by_id(song_id)
    if song is None:
        sys.exit(f"[エラー] 曲 {song_id} が見つかりません")
    step(0, len(dk_sections(song)) + 2, "DistroKid の登録画面を開いています")
    sess.goto(SITES["distrokid"])
    if not sess.ensure_login("distrokid"):
        print("【結果】まだ終わっていません：DistroKid にログインできていません")
        return 2
    if not wait_for_form(sess, "DistroKid", "登録の画面（Upload）を開いてから"):
        print("【結果】まだ終わっていません：DistroKid の登録画面が開けませんでした")
        return 2
    if not same_page_ready(song):   # 2 曲目から：デビュー曲と同じページにまとめるため、ページの ID がわかるまで待つ（決定 53）
        sess.show("まだ登録しない方がよい曲です",
                  [f"{song['artist']} の Spotify のアーティストのページが、まだわかっていません。",
                   "いま登録すると、デビュー曲と別のアーティストのページができるおそれがあります。",
                   "デビュー曲が Spotify に出ると、毎日の確認でページが自動で記録されます。早く進めたいときは、"
                   "DistroKid のデビュー曲のページに出る Spotify の URI（spotify:artist:…）を、ダッシュボードの「Spotify のページを記録する」に貼ってください。"],
                  [{"id": "stop", "label": "待つ（閉じる）", "primary": True},
                   {"id": "anyway", "label": "それでも入れる（別のページになるかもしれない）"}])
        if sess.wait({"action": "stop"}).get("action") != "anyway":
            print("【結果】まだ終わっていません：Spotify のアーティストのページがわかるまで待ちます")
            return 3 if nxt else 2
    fields, draft = distrokid_fields(song)
    owner = os.environ.get("OWNER_LEGAL_NAME", "").strip()
    owner_ng = "" if re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*( [A-Za-z][A-Za-z.'\-]*)+", owner) else \
        ("本名（.env の OWNER_LEGAL_NAME）が空です" if not owner else "本名（.env の OWNER_LEGAL_NAME）がローマ字の「名 姓」になっていません")
    if owner_ng:
        print(f"   ⚠ {owner_ng}。Songwriter と Track credits の Producer を入れられません")
    # 画面の上から 1 項目ずつ入れて、確かめる（決定 68）
    c = DK(sess, song, fields)
    secs = dk_sections(song)
    n_steps = len(secs) + 2
    try:   # DistroKid の表示の言語（画面のいちばん下の Language）。英語でないと、英語の見出しで探す欄が見つからないことがある（決定 75）
        ui_lang = sess.page.evaluate("""() => { const e = [...document.querySelectorAll('[aria-label]')].find(x => /^language selected/i.test(x.getAttribute('aria-label')));
            return e ? e.getAttribute('aria-label').replace(/^language selected:\\s*/i, '').trim() : ''; }""")
    except Exception:  # noqa: BLE001
        ui_lang = ""
    if ui_lang and not re.match(r"english", ui_lang, re.I):
        print(f"   ⚠ DistroKid の表示が「{ui_lang}」になっています。画面のいちばん下の Language を English にすると、自動入力が確実です"
              "（システムはこの切り替えを触りません）")
    dk_fill(c, secs)
    step(len(secs) + 1, n_steps, "読み返して、入っていない項目を上から入れ直しています")
    st = dk_refill(c, secs)
    log_state(sess, "入れ終わったときの画面")
    shoot(sess, song, "filled")
    try:   # Gon さんが Continue を押したことを、ページの中で覚えておく（登録が済んだかを見分けるため。同じタブなら次のページでも残る）
        sess.page.evaluate("""() => { document.addEventListener('click', e => {
            const b = e.target.closest && e.target.closest('#distroDoneButtonWithAPI, .distroDoneButtonWithAPI');
            if (b && e.isTrusted) { try { sessionStorage.setItem('pdContinue', String(Date.now())); } catch (x) {} } }, true); }""")
    except Exception:  # noqa: BLE001
        pass
    bad = [s_["name"] for s_ in secs if st[s_["key"]] is False]
    gon = ["▼ ここから Gon さん（システムは押しません）",
           *([f"⚠ {owner_ng}：Songwriter と Producer に本名をローマ字で入れてください（.env の OWNER_LEGAL_NAME）"] if owner_ng else []),
           "・Extras（有料の追加）・Discovery Pack：チェックしない",
           "・宣誓のチェック：文を読んで、当てはまるものだけチェック（Non-standard cApiTaliZatiOn を含む）",
           "・Continue を押す → 登録のあとの画面に変わると、システムが自動で「登録した」を記録します"]
    print(f"   ○ {song['artist']}「{song['title']}」：上から {len(secs)} 項目のうち、入った {sum(1 for v in st.values() if v)}・入っていない {len(bad)}")
    later = ([{"id": "stop", "label": f"この曲は後で（次の曲へ：{nxt}）"}, {"id": "quit", "label": "続けての入力をここでやめる"}] if nxt
             else [{"id": "stop", "label": "閉じる（まだ登録しない）"}])
    buttons = [{"id": "registered", "label": "登録した（自動で記録されないとき）", "primary": False}, *later,
               {"id": "copy_parts", "label": "画面の部品をコピー（Claude に貼る用）"}]
    sess.show("DistroKid に入れました（画面の上から順）" if not bad else "DistroKid に入れました（入っていない項目があります）",
              [f"{song['artist']}「{song['title']}」", *dk_checklist(secs, st, c), *gon], buttons, date_value=date.today().isoformat())

    last = {"release": c.f.get("release", {}).get("value") or date.today().isoformat(), "n": 0}

    def watch_errors():   # 待っている間：差し戻し・戻された項目・登録が済んだか を見張る
        last["n"] += 1
        try:   # 登録が済んだか：Continue を押したあと、登録の画面から別の画面に変わり、差し戻しが無い（決定 68）
            pushed = sess.page.evaluate("() => { try { return Number(sessionStorage.getItem('pdContinue') || 0); } catch (x) { return 0; } }")
            on_form = bool(sess.page.locator("input[name=bandname]").count())
            if not on_form:
                if pushed and (time.time() * 1000 - pushed) < 30 * 60 * 1000 and not sess.js(VALIDATION_JS, default=""):
                    sess.msg = {"action": "registered", "date": last["release"], "auto": True}
                return
            v = sess.page.evaluate("() => (document.querySelector('input[name=releaseDate]') || {}).value || ''")
            if v:
                last["release"] = v
        except Exception:  # noqa: BLE001
            return
        txt = sess.js(VALIDATION_JS, default="")
        if not txt:
            confirm_new_page(sess)   # 見張りが選び直したときの確認の小窓
            answer_ai_parts(sess, song)   # Gon さんが「Edit selections」を押したときなどに出る、AI で作った部分の質問
            if last["n"] % 3 == 0:   # 約 6 秒ごとに、上から読み返して、戻された項目を黙って入れ直す
                before = dk_check(c, secs)
                if any(v is False for k, v in before.items() if next(x for x in secs if x["key"] == k)["again"]):
                    after = dk_refill(c, secs, quiet=True, rounds=1)
                    fixed = [x["name"] for x in secs if before[x["key"]] is False and after[x["key"]]]
                    if fixed:
                        print("   … あとから戻された項目を入れ直しました：" + "・".join(n[:30] for n in fixed))
            return
        print("   … DistroKid に差し戻されました：" + " ／ ".join(x.strip() for x in txt.splitlines()[1:6] if x.strip()))
        log_state(sess, "差し戻されたときの画面")
        shoot(sess, song, "rejected")
        try:   # 画面の部品の一覧を自動で残す（決定 60）
            from datetime import datetime as _dt
            pp = save_parts(sess, "distrokid")
            if pp and pp.exists():
                kp = pp.with_name(f"distrokid_parts_{song['id']}_{_dt.now():%Y%m%d_%H%M%S}.txt")
                kp.write_text(pp.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"   記録：差し戻されたときの画面の部品の一覧を保存しました → {kp}")
        except Exception:  # noqa: BLE001
            pass
        before = dk_check(c, secs)
        st2 = dk_refill(c, secs)
        log_state(sess, "入れ直したあとの画面")
        shoot(sess, song, "refilled")
        fixed = [x["name"] for x in secs if before[x["key"]] is False and st2[x["key"]]]
        sess.show("DistroKid に差し戻されたので、上から入れ直しました",
                  ["DistroKid のエラー：", *[x.strip() for x in txt.splitlines()[1:10] if x.strip()],
                   ("入れ直したもの：" + "・".join(n[:30] for n in fixed)) if fixed else "入れ直せるものはありませんでした（下の ✕ を画面で入れてください）",
                   *dk_checklist(secs, st2, c), *gon], buttons, date_value=date.today().isoformat())
    step(n_steps, n_steps, "Gon さんの確認待ち（宣誓・Continue）")
    m = sess.wait({"action": "stop"}, tick=watch_errors)
    if m.get("action") == "registered":
        day = m.get("date") or date.today().isoformat()
        r = subprocess.run([sys.executable, str(S / "publish.py"), "registered", song["id"], "--date", day], capture_output=True, text=True)
        print(r.stdout.strip())
        print(f"【結果】DistroKid に登録したことを記録しました（配信日 {day}）" + ("。登録のあとの画面に変わったので、自動で記録しました" if m.get("auto") else ""))
        has_lyr = bool(lyrics_for(song))
        lyr_btn = [{"id": "lyrics", "label": "続けて歌詞を出す（DistroKid の歌詞の画面に自動で入れる）", "primary": True}] if has_lyr else []
        if nxt:   # 続けて入れるとき：歌詞を出してから次の曲へ／次の曲へ／ここでやめる
            sess.show("記録しました", [f"DistroKid 登録済み（配信日 {day}）。", *(["（登録のあとの画面に変わったので、自動で記録しました）"] if m.get("auto") else []),
                                     f"次の曲：{nxt}"],
                      lyr_btn + [{"id": "next", "label": f"次の曲へ（{nxt}）", "primary": not has_lyr},
                                 {"id": "quit", "label": "続けての入力をここでやめる"}])
        else:
            sess.show("記録しました", [f"DistroKid 登録済み（配信日 {day}）。配信が始まると、ISRC などが自動で入ります。",
                                     *(["登録のあとの画面に変わったので、自動で記録しました。違っていたら、ダッシュボードの「← 戻る」で戻せます。"] if m.get("auto") else [])],
                      lyr_btn + [{"id": "stop", "label": "閉じる", "primary": not has_lyr}])
        go = sess.wait({"action": "next" if nxt else "stop"})
        if go.get("action") == "lyrics":
            lc = run_lyrics(sess, song["id"], nxt)
            return 3 if lc == 3 else 0
        if nxt:
            return 0 if go.get("action") == "next" else 3
        return 0
    if nxt and m.get("action") in ("quit", "closed"):
        print("【結果】続けての入力をここでやめました（この曲はまだ登録していません）")
        return 3
    if bad:
        print("【結果】まだ終わっていません：DistroKid の入っていない項目 → " + "／".join(b[:30] for b in bad))
    else:
        print("【結果】全部の項目に入りましたが、登録（Continue）はまだです")
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
    ap.add_argument("what", choices=["suno", "distrokid", "lyrics", "photo", "login", "forget", "reset", "parts"])
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
        import shlex
        py = shlex.quote(sys.executable)
        sys.exit("[案内] ブラウザで入力する部品（Playwright）が、この Python に入っていません：" + sys.executable + "\n"
                 "       （Python の版が上がると、前に入れた部品が見えなくなります）\n"
                 "       ターミナルに次をコピーして 1 回実行してください：\n"
                 f"       {py} -m pip install --user playwright || {py} -m pip install --user --break-system-packages playwright\n"
                 "       ダッシュボードのポップアップの「ブラウザ入力の部品を入れる」でも同じことができます")
    if a.what == "distrokid" and not a.args:
        sys.exit("[エラー] 曲 ID を付けてください（例 PDR-003。残りを続けて入れるときは all）")
    if a.what == "photo" and len(a.args or []) < 2:
        sys.exit("[エラー] 組とサイトを付けてください（例 photo tidewool spotify）")
    code, need = 0, None
    with sync_playwright() as pw:
        try:
            sess = Session(pw)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001  つないだあとの準備で失敗：専用の Chrome を開き直して 1 回だけやり直す（決定 63）
            import traceback
            first = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
            print(f"   … 専用の Chrome の準備でつまずきました（{first}）。Chrome を開き直してやり直します")
            (OUT / "browser").mkdir(parents=True, exist_ok=True)
            (OUT / "browser" / "last_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            quit_chrome()
            try:
                sess = Session(pw)
            except Exception as e2:  # noqa: BLE001
                (OUT / "browser" / "last_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                print(f"【結果】まだ終わっていません：専用の Chrome の準備ができませんでした（{str(e2).splitlines()[0][:200] if str(e2) else type(e2).__name__}）。"
                      "この文を Claude に貼ってください（くわしくは out/browser/last_error.txt）")
                sys.exit(2)
        try:
            if a.what == "suno":
                code = run_suno(sess, a.args)
            elif a.what == "distrokid":
                if a.args[0] != "all" and _songs.by_id(a.args[0]):
                    progress(1, 1, _songs.by_id(a.args[0]))
                code = run_distrokid_all(sess) if a.args[0] == "all" else run_distrokid(sess, a.args[0])
            elif a.what == "lyrics":   # 歌詞を DistroKid の歌詞の画面に入れる（曲 ID か all。決定 52）
                if a.args and a.args[0] != "all" and _songs.by_id(a.args[0]):
                    progress(1, 1, _songs.by_id(a.args[0]))
                code = run_lyrics_all(sess) if not a.args or a.args[0] == "all" else run_lyrics(sess, a.args[0])
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

#!/usr/bin/env python3
"""
初期化した Mac・買い替えた Mac を、外付け SSD（DS72）から立て直す（決定 180）

  なぜ：この仕組みは Mac 1 台で動いている。Mac を初期化すると、プログラム（GitHub にある）は戻せても、
        「どの曲がどこまで進んだか」の記録（out/）・鍵（.env）・定期実行の登録が消える。
        SSD の保管庫（Postdog Records）には、楽曲管理.xlsx（1 曲 1 行の台帳）と、曲ごとのフォルダ（音源・ジャケット・歌詞）が残っているので、
        そこから Mac の中の記録を作り直す。

  使い方（上から順に。どれも何度動かしても安全）
    python3 scripts/recover.py check                 # いまどこまで戻っているかを、上から順に見る（何も変えない）
    python3 scripts/recover.py from-ssd              # SSD から何を戻すかを見るだけ（何も書かない）
    python3 scripts/recover.py from-ssd --apply      # 実際に戻す（Mac にもうある記録は上書きしない）
    python3 scripts/recover.py cloud                 # 戻した内容で、R2 の予備と Notion を作り直す（鍵があるものだけ）
    python3 scripts/recover.py check --json          # 機械が読む形で
    python3 scripts/recover.py check --ssd "/Volumes/SP DS72"   # SSD の場所を自分で指すとき

  SSD のどこから、何を戻すか（archive_sync.py・song_book.py・publish.py が書いた形のとおり）
    01_管理表/楽曲管理.xlsx                 → 曲ごとの記録（out/publish/state/<曲>.json）：段階・配信日・ISRC・UPC・配信の住所・選んだテイク
                                             毎週の曲は、作曲の指示（out/briefs/<曲>.json）も最小限で作り直す：曲名・別案・Suno の指示・歌詞・配信予定日
    02_アーティスト/<名前>/04_楽曲/<曲名>/   → …_Suno元データ.wav：選んだテイク（out/takes/<曲>/）。登録前の曲は、ここから仕上げ直す
                                             <曲名>_<名前>_<日付>.wav：配信用の音源（out/masters/<曲>/master.wav）。登録済み・配信中の曲だけ
                                             <名前>_<曲名>_<番号>.jpg：ジャケット（毎週の曲は out/visuals/…/cover_final.jpg、デビュー曲は debut_kit/<日付>/images/）
                                             …_歌詞_配信用.txt・…_歌詞_日本語訳.txt・…_エピソード.md → out/publish/<曲>/
    02_アーティスト/<名前>/01_情報/          → プロフィール（out/profiles/）。設定書は GitHub のものを正とし、違いだけ知らせる
    02_アーティスト/<名前>/02_写真/          → デビュー用の写真（debut_kit/<日付>/images/。画像は GitHub に入らないため）
    既存楽曲_解析シート/aud-*.json           → 参考曲の解析シート（out/reference_db/audio/）。解析し直さない

  決まり
    ・Mac にもうある記録・ファイルは上書きしない（配信の記録のうち、空いている ISRC・UPC・住所だけは足す）
    ・SSD には書かない（見るだけ。楽曲管理.xlsx は、戻す前に Mac の out/restore/ に写しを取る）
    ・鍵の「値」は読まないし、出さない（名前と、入っているかどうかだけ）
    ・check は、Mac に最初から入っている古い Python（3.9）でも動く。ほかは新しい Python（3.10 以上）で
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

S = Path(__file__).resolve().parent
ROOT = S.parent
OUT = ROOT / "out"
PY = sys.executable
sys.path.insert(0, str(S))

MIN_PY = (3, 10)
VOLUMES = Path("/Volumes")
VOLUME_HINT = "DS72"
LABEL_DIR = "Postdog Records"
BOOK_DIR, ARTIST_DIR, KIT_DIR, OLD_DIR, INBOX_DIR = "01_管理表", "02_アーティスト", "03_作業書", "09_移行前", "楽曲情報"
SUBS = {"info": "01_情報", "photo": "02_写真", "cover": "03_ジャケット", "music": "04_楽曲"}
BOOK_NAME, BOOK_SHEET = "楽曲管理.xlsx", "楽曲"
REF_HINT, SHEET_HINT, MELODY_HINT = "既存楽曲", "解析シート", "メロディー"
RAW_MARK = "_Suno元データ"
AUDIO = {".wav", ".mp3", ".m4a", ".flac", ".aif", ".aiff", ".aac", ".ogg", ".opus"}
IMAGES = {".jpg", ".jpeg", ".png"}
DOCS = ("歌詞_配信用.txt", "歌詞_日本語訳.txt", "エピソード.md")   # out/publish/<曲>/ に戻す書類（下書きは作り直せるので戻さない）
RESTORE = OUT / "restore"
STATE_DIR = OUT / "publish" / "state"
RELEASE_KEYS = ("isrc", "upc", "url", "released", "release_date")   # Mac に記録があっても、空いていれば足す
KEY_JA = {"isrc": "ISRC", "upc": "UPC", "url": "配信の住所", "released": "配信が始まった日", "release_date": "配信日"}
STATUS_JA = {"live": "配信中", "registered": "DistroKid 登録済み", "imported": "取り込み済み（仕上げ直します）", "": "作曲の前（Suno で作る番）"}
# requirements.txt の名前 → Python の中での名前（違うものだけ）
IMPORT_NAME = {"pillow": "PIL", "fonttools": "fontTools", "faster-whisper": "faster_whisper"}
EXTRA_PARTS = [("scipy", "仕上げ（マスタリング）"), ("playwright", "Suno・DistroKid への自動入力")]
# いつも「戻せない」もの（SSD にも GitHub にも無い）
NEVER = [
    "鍵（.env）：SSD には入れていません → python3 scripts/setup_keys.py で入れ直す（復旧キットがあれば python3 scripts/vault.py restore）",
    "仕上げた音を「聴いて確かめた」印：登録前の曲は、もう一度聴いて確かめます",
    "作曲の指示の細かい所（除外する言葉・場面・参考曲の割り当て・Suno の細かい設定）：曲名・Suno の指示・歌詞は戻ります",
    "ジャケットの候補と、文字を入れる前の版：選んだ完成品は戻ります",
    "再生数の記録・お金の記録：再生数は次に集めたときから。お金は明細のファイルを置き直すと読み直します",
    "仕上げ（マスタリング）の設定と、既存楽曲から学んだ目標：ダッシュボードで設定し直す・学び直す",
    "曲の案（不定期配信）・SNS の投稿の予定・Suno の残り回数の記録・Chrome のログイン",
]


# ---------------------------------------------------------------------------
# 小道具（Mac 付属の古い Python でも動くように、ほかの部品に頼らない）
def _nfc(s: object) -> str:
    return unicodedata.normalize("NFC", str(s or ""))


def _norm(s: object) -> str:
    """見比べるための形（全角半角・大文字小文字・Mac の濁点の持ち方・記号の揺れをそろえる）"""
    s = unicodedata.normalize("NFKC", str(s or "")).casefold()
    s = re.sub(r"[’'`\"“”]", "", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", s)).strip()


def _same(a: str, b: str) -> bool:
    return unicodedata.normalize("NFKC", a).strip().casefold() == unicodedata.normalize("NFKC", b).strip().casefold()


def _kids(p: Path | None) -> list:
    """フォルダの中身（隠しファイル・Mac の付き添いファイル・Excel の一時ファイルは除く）"""
    if p is None:
        return []
    try:
        return sorted(c for c in p.iterdir() if not c.name.startswith((".", "~$", "$")))
    except OSError:
        return []


def _find(parent: Path | None, name: str):
    for c in _kids(parent):
        if c.is_dir() and _same(c.name, name):
            return c
    return None


def _count(p: Path | None, exts: set, pattern: str = "") -> int:
    """フォルダの中（下の階層も）の、その種類のファイルの数"""
    if p is None or not p.is_dir():
        return 0
    n = 0
    for root, dirs, files in os.walk(str(p)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith((".", "~$")):
                continue
            if os.path.splitext(f)[1].lower() in exts and (not pattern or re.match(pattern, f)):
                n += 1
    return n


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _env_names(path: Path | None = None) -> dict:
    """.env の「名前 → 値が入っているか」。値そのものは返さない"""
    path = path or ROOT / ".env"
    out = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = bool(v.strip().strip('"').strip("'"))
    return out


def _load_env() -> None:
    """.env を読んで環境変数にする（SSD の場所・R2 の鍵など。すでに設定済みのものは上書きしない）"""
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def _mod(name: str):
    """ほかの部品を読み込む。読めなければ (None, 理由)。古い Python・部品の不足でも、点検そのものは止めない"""
    try:
        return importlib.import_module(name), ""
    except BaseException as e:  # noqa: BLE001  SystemExit（部品が自分で止まる）も受ける
        return None, f"{type(e).__name__}: {str(e)[:120]}"


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:  # noqa: BLE001
        return False


def _which(name: str) -> str:
    """道具の場所。ターミナルを開き直す前でも、Homebrew が入れる場所は見る"""
    p = shutil.which(name)
    if p:
        return p
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        if os.access(f"{d}/{name}", os.X_OK):
            return f"{d}/{name}"
    return ""


def _version(cmd: list) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        m = re.search(r"\d+(?:\.\d+)+", (r.stdout or r.stderr or ""))
        return m.group(0) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def _day(v) -> str:
    """表の日付の欄 → YYYY-MM-DD（Excel の日付・文字・通し番号のどれでも）"""
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:   # Excel の日付の通し番号
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", str(v))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


# ---------------------------------------------------------------------------
# SSD を探す・中身を数える（読むだけ。フォルダを作らない）
def find_ssd(path: str | None = None):
    """(ドライブの一番上, 保管庫「Postdog Records」, 説明)。見つからなければ (None, None, 理由)"""
    given = (path or os.environ.get("ARCHIVE_ROOT", "")).strip()
    if given:
        p = Path(given).expanduser()
        if not p.is_dir():
            return None, None, f"SSD がつながっていません（{p} が見つかりません）"
        if _same(p.name, LABEL_DIR):
            return p.parent, p, str(p)
        base = _find(p, LABEL_DIR)
        if base is None:
            return p, None, f"{p} の中に「{LABEL_DIR}」のフォルダが見つかりません"
        return p, base, str(base)
    vols = [v for v in _kids(VOLUMES) if v.is_dir() and VOLUME_HINT.casefold() in v.name.casefold()] if VOLUMES.is_dir() else []
    if not vols:
        return None, None, f"SSD（名前に {VOLUME_HINT} を含むドライブ）が見つかりません。つないでから、もう一度"
    if len(vols) > 1:
        return None, None, f"名前に {VOLUME_HINT} を含むドライブが複数あります（--ssd で場所を指してください）"
    base = _find(vols[0], LABEL_DIR)
    if base is None:
        return vols[0], None, f"SSD（{vols[0]}）はつながっていますが、中に「{LABEL_DIR}」のフォルダが見つかりません"
    return vols[0], base, str(base)


def _xlsx_rows(path: Path, sheet: str) -> list:
    """Excel の表を、追加の部品なしで読む（まっさらな Mac でも台帳を読めるように）。行ごとの値の一覧"""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "p": "http://schemas.openxmlformats.org/package/2006/relationships"}
    with zipfile.ZipFile(str(path)) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                # 文字そのもの（t）と、飾り付きの文字（r/t）だけ。Excel が付けるふりがな（rPh）は読まない
                shared.append("".join(t.text or "" for t in si.findall("m:t", ns) + si.findall("m:r/m:t", ns)))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall("p:Relationship", ns)}
        sheets = [(s.get("name"), rels.get(s.get("{%s}id" % ns["r"]), "")) for s in wb.findall("m:sheets/m:sheet", ns)]
        target = next((t for n, t in sheets if n == sheet), sheets[0][1] if sheets else "")
        if not target:
            return []
        target = target.lstrip("/")
        member = target if target.startswith("xl/") else "xl/" + target
        rows = []
        for row in ET.fromstring(z.read(member)).findall("m:sheetData/m:row", ns):
            vals = []
            for c in row.findall("m:c", ns):
                ref = re.match(r"[A-Z]+", c.get("r") or "")
                col = 0
                for ch in (ref.group(0) if ref else ""):
                    col = col * 26 + ord(ch) - 64
                col = col - 1 if col else len(vals)
                t = c.get("t")
                v = c.find("m:v", ns)
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.findall("m:is/m:t", ns) + c.findall("m:is/m:r/m:t", ns))
                elif v is None or v.text is None:
                    val = None
                elif t == "s":
                    val = shared[int(v.text)] if int(v.text) < len(shared) else ""
                elif t in ("str", "e"):
                    val = v.text
                elif t == "b":
                    val = v.text == "1"
                else:
                    try:
                        val = float(v.text)
                    except ValueError:
                        val = v.text
                while len(vals) <= col:
                    vals.append(None)
                vals[col] = val
            rows.append(vals)
        return rows


def read_book(path: Path) -> list:
    """楽曲管理.xlsx の行（{見出し: 値}）。表計算の部品（openpyxl）があればそれで、無ければ自前の読み方で"""
    rows = None
    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        ws = wb[BOOK_SHEET] if BOOK_SHEET in wb.sheetnames else wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    except ImportError:
        rows = _xlsx_rows(path, BOOK_SHEET)
    if not rows:
        return []
    head = [_text(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        d = {h: v for h, v in zip(head, r) if h}
        if _text(d.get("曲 ID")):
            out.append(d)
    return out


def song_files(folder: Path | None) -> dict:
    """曲のフォルダ（04_楽曲/<曲名>/）の中身を見分ける：配信用の音源・Suno の元データ・ジャケット・書類"""
    out = {"folder": folder, "master": None, "raw": None, "cover": None, "docs": {}}
    masters, raws, covers = [], [], []
    for f in _kids(folder):
        if not f.is_file():
            continue
        stem, ext = _nfc(f.stem), f.suffix.lower()
        if stem.endswith("_old"):
            continue
        if ext in AUDIO:
            (raws if RAW_MARK in stem else masters).append(f)
        elif ext in IMAGES:
            m = re.search(r"_(\d+)$", stem)
            covers.append((int(m.group(1)) if m else 0, f.stat().st_mtime, f))
        else:
            for d in DOCS:
                if _nfc(f.name).endswith("_" + d):
                    out["docs"][d] = f
    newest = lambda fs: max(fs, key=lambda x: x.stat().st_mtime) if fs else None  # noqa: E731
    out["master"] = newest([f for f in masters if f.suffix.lower() == ".wav"]) or newest(masters)
    out["raw"] = newest(raws)
    out["cover"] = max(covers)[2] if covers else None
    return out


def inventory(drive: Path | None, base: Path | None) -> dict:
    """SSD の中身の数（読むだけ）"""
    inv: dict = {"drive": str(drive or ""), "base": str(base or ""), "books": [], "book_rows": None, "book_error": "", "phases": {},
                 "artists": [], "kits": 0, "inbox": 0, "old": 0, "others": [], "refs": {"audio": [], "sheets": [], "melody": []}}
    if base is not None:
        books = _find(base, BOOK_DIR)
        for f in _kids(books):
            if f.is_file():
                st = f.stat()
                inv["books"].append({"name": _nfc(f.name), "bytes": st.st_size, "date": date.fromtimestamp(st.st_mtime).isoformat()})
        book = next((f for f in _kids(books) if f.is_file() and _same(f.name, BOOK_NAME)), None)
        if book is not None:
            try:
                rows = read_book(book)
                inv["book_rows"] = len(rows)
                for r in rows:
                    ph = _text(r.get("フェーズ")) or "（段階なし）"
                    inv["phases"][ph] = inv["phases"].get(ph, 0) + 1
            except Exception as e:  # noqa: BLE001
                inv["book_error"] = f"{type(e).__name__}: {str(e)[:100]}"
        for a in _kids(_find(base, ARTIST_DIR)):
            if not a.is_dir():
                continue
            songs = [d for d in _kids(_find(a, SUBS["music"])) if d.is_dir()]
            files = [song_files(d) for d in songs]
            inv["artists"].append({
                "name": _nfc(a.name), "songs": len(songs), "sets": sum(1 for x in files if x["master"] and x["cover"]),
                "masters": sum(1 for x in files if x["master"]), "raws": sum(1 for x in files if x["raw"]),
                "covers": _count(_find(a, SUBS["cover"]), IMAGES), "photos": _count(_find(a, SUBS["photo"]), IMAGES),
                "info": len([f for f in _kids(_find(a, SUBS["info"])) if f.is_file()])})
        inv["kits"] = len([d for d in _kids(_find(base, KIT_DIR)) if d.is_dir()])
        inv["inbox"] = _count(_find(base, INBOX_DIR), AUDIO)
        inv["old"] = sum(1 for _r, _d, fs in os.walk(str(_find(base, OLD_DIR) or "/nonexistent")) for f in fs if not f.startswith("."))
        known = (BOOK_DIR, ARTIST_DIR, KIT_DIR, OLD_DIR, INBOX_DIR)
        inv["others"] = [_nfc(c.name) for c in _kids(base) if not any(_same(c.name, k) for k in known) and REF_HINT not in _nfc(c.name)]
    # 既存楽曲（参考曲）：ドライブの一番上と、保管庫の中を 3 段まで
    seen = set()

    def walk(top, depth):
        for c in _kids(top):
            if not c.is_dir():
                continue
            name = _nfc(c.name)
            if REF_HINT in name and str(c) not in seen:
                seen.add(str(c))
                if SHEET_HINT in name:
                    inv["refs"]["sheets"].append({"path": str(c), "count": _count(c, {".json"}, r"aud-.*\.json$")})
                elif MELODY_HINT in name:
                    inv["refs"]["melody"].append({"path": str(c), "count": _count(c, {".mid", ".midi"})})
                else:
                    inv["refs"]["audio"].append({"path": str(c), "count": _count(c, AUDIO)})
                continue   # 見つけたフォルダの中は、それ以上は探さない
            if depth > 1 and c.name not in ("System Volume Information",):
                walk(c, depth - 1)

    for top in [t for t in (drive, base) if t is not None]:
        walk(top, 3)
    return inv


# ---------------------------------------------------------------------------
# 点検（check）
def _sec(sid: str, title: str, ok, lines: list, nxt: str = "", cmd: str = "") -> dict:
    """ok：True＝そろっている／False＝足りない／None＝ここでは分からない・あとでよい"""
    return {"id": sid, "title": title, "ok": ok, "lines": lines, "next": nxt, "cmd": cmd}


def check_tools() -> dict:
    mac = platform.system() == "Darwin"
    v = sys.version_info
    py_ok = v >= MIN_PY
    lines = [("○" if py_ok else "✕") + f" Python {v.major}.{v.minor}.{v.micro}"
             + ("" if py_ok else "（古い版です。Mac に最初から入っているもの。この仕組みは 3.10 以上で動きます）")]
    git = _which("git")
    lines.append(("○ git " + _version([git, "--version"])) if git else "✕ git（プログラムを GitHub から取ってくる道具）がありません")
    brew = _which("brew")
    ff = _which("ffmpeg")
    chrome = any((b / "Applications" / "Google Chrome.app").exists() for b in (Path("/"), Path.home()))
    if mac:
        lines.append("○ Homebrew（道具を入れる仕組み）" if brew else "✕ Homebrew（道具を入れる仕組み）がありません")
        lines.append("○ Chrome" if chrome else "✕ Chrome がありません（Suno・DistroKid への入力に使います）")
    lines.append(("○ ffmpeg（音を扱う道具）" if ff else "✕ ffmpeg（音を扱う道具）がありません：仕上げ・解析・縦動画づくりが止まります"))
    ok = py_ok and bool(git) and bool(ff) and (not mac or (bool(brew) and chrome))
    script = not py_ok or not ff or not git or (mac and not brew)   # 用意の台本で入るもの
    nxt = "" if ok else ("用意の台本を流す（足りない道具を調べて、入れ方を順に案内します）" if script else "Chrome を入れる（https://www.google.com/chrome/）")
    return _sec("tools", "道具", ok, lines, nxt, "bash setup_mac.sh" if script else "")


def check_parts() -> dict:
    names = []
    try:
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r"[A-Za-z0-9_.\-]+", line)
            if m and m.group(0) not in names:
                names.append(m.group(0))
    except OSError:
        pass
    missing = [n for n in names if not _has(IMPORT_NAME.get(n.lower(), n.replace("-", "_")))]
    lines = [f"○ 必要な部品 {len(names) - len(missing)} / {len(names)} 個"] if names else ["－ requirements.txt が読めません"]
    if missing:
        lines.append("✕ 入っていない部品：" + "・".join(missing))
    for mod, what in EXTRA_PARTS:
        if not _has(mod):
            lines.append(f"・{mod} はまだ（{what}に使います）"
                         + ("：ダッシュボードの「ブラウザ入力の部品を入れる」で入ります" if mod == "playwright" else ""))
    ok = bool(names) and not missing
    return _sec("parts", "Python の部品", ok, lines, "" if ok else "Python の部品を入れる（用意の台本が、足りないものだけ入れます）",
                "" if ok else "bash setup_mac.sh")


def check_keys() -> dict:
    env = _env_names()
    lines = []
    if not (ROOT / ".env").exists():
        return _sec("keys", "鍵（.env）", False, ["✕ 鍵のファイル（.env）がまだありません（鍵は GitHub にも SSD にも入れていません）"],
                    "鍵を入れ直す（1 つずつ案内が出ます。復旧キットを控えてあれば  python3 scripts/vault.py restore  で全部戻ります）",
                    "python3 scripts/setup_keys.py")
    vault, _why = _mod("vault")
    services = {}
    if vault is not None:
        try:
            services = vault.services()
        except Exception:  # noqa: BLE001
            services = {}
    if services:
        for svc, s in services.items():
            ks = [k for k, _secret in s.get("keys") or []]
            have = [k for k in ks if env.get(k)]
            if not ks:
                continue
            mark = "○" if len(have) == len(ks) else ("△" if have else "－")
            lines.append(f"{mark} {s.get('title') or svc}：" + "・".join(f"{k}{'' if env.get(k) else '（まだ）'}" for k in ks))
    else:
        lines.append("入っている鍵の名前：" + ("・".join(sorted(k for k, v in env.items() if v)) or "（なし）"))
    n = sum(1 for v in env.values() if v)
    lines.insert(0, f"○ 鍵のファイルがあります（値の入っている項目 {n} 個。値は表示しません）")
    core = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "R2_ACCESS_KEY_ID") if not env.get(k)]
    ok = not core
    if core:
        lines.append("✕ まだ入っていない大事な鍵：" + "・".join(core))
    return _sec("keys", "鍵（.env）", ok, lines, "" if ok else "足りない鍵を入れる（入力欄に貼るだけ。チャットには貼らない）",
                "" if ok else "python3 scripts/setup_keys.py")


def check_ssd(path: str | None = None) -> tuple:
    drive, base, how = find_ssd(path)
    if base is None:
        return _sec("ssd", "SSD（保管庫）", False, ["✕ " + how], "SSD をつなぐ（つないだら、もう一度この点検を）"), None
    inv = inventory(drive, base)
    lines = [f"○ つながっています：{base}"]
    if inv["books"]:
        lines.append("管理表：" + "・".join(f"{b['name']}（{b['date']}）" for b in inv["books"]))
    if inv["book_rows"] is not None:
        ph = "・".join(f"{k} {v} 曲" for k, v in sorted(inv["phases"].items()))
        lines.append(f"○ 楽曲管理.xlsx：{inv['book_rows']} 曲（{ph}）")
    else:
        lines.append("✕ 楽曲管理.xlsx が" + (f"読めません（{inv['book_error']}）" if inv["book_error"] else "見つかりません") + "：曲の段階・ISRC などは戻せません")
    for a in inv["artists"]:
        lines.append(f"・{a['name']}：曲のフォルダ {a['songs']}（配信用の音源 {a['masters']}・Suno の元データ {a['raws']}・音源とジャケットがそろった公開セット {a['sets']}）"
                     f"／ジャケット {a['covers']} 枚・写真 {a['photos']} 枚")
    total = sum(a["songs"] for a in inv["artists"])
    lines.append(f"アーティスト {len(inv['artists'])} 組・曲のフォルダ {total}・公開セット {sum(a['sets'] for a in inv['artists'])}")
    lines.append(f"{KIT_DIR}（デビューの作業書）：{inv['kits']} 回ぶん／{INBOX_DIR}（Suno のダウンロードの受け口）：音源 {inv['inbox']} 本"
                 + ("（まだ取り込んでいない曲。立て直したあとの取り込みで拾います）" if inv["inbox"] else "")
                 + f"／{OLD_DIR}（Google から移したもの。機械は触りません）：{inv['old']} 件")
    if inv["others"]:
        lines.append("そのほか（機械は触りません）：" + "・".join(inv["others"]))
    r = inv["refs"]
    if r["audio"] or r["sheets"]:
        lines.append("既存楽曲（参考曲）：音源 " + "・".join(f"{x['count']} 本（{Path(x['path']).name}）" for x in r["audio"])
                     + "／解析シート " + ("・".join(f"{x['count']} 件" for x in r["sheets"]) or "なし")
                     + "／メロディー（MIDI） " + ("・".join(f"{x['count']} 件" for x in r["melody"]) or "なし"))
    else:
        lines.append("・既存楽曲（参考曲）のフォルダは見つかりません")
    ok = inv["book_rows"] is not None
    return _sec("ssd", "SSD（保管庫）", ok, lines, "" if ok else "SSD の 01_管理表/楽曲管理.xlsx を確かめる"), inv


def local_counts() -> dict:
    g = lambda pat: len(list(ROOT.glob(pat)))  # noqa: E731
    return {"state": g("out/publish/state/*.json"), "briefs": len([p for p in ROOT.glob("out/briefs/*.json") if "." not in p.stem]),
            "masters": g("out/masters/*/master.wav"), "takes": g("out/takes/*/selection.json"),
            "covers": g("out/visuals/*/cover/*/cover_final.jpg") + g("debut_kit/*/images/*_cover.jpg"),
            "sheets": g("out/reference_db/audio/aud-*.json"), "references": g("out/references/*.json"), "kits": g("debut_kit/*/kit.json")}


def check_local(inv: dict | None, ssd: str | None = None) -> dict:
    """Mac の中の記録が足りているか：SSD から「見るだけ」で数えて、まだ戻せるものが残っていれば足りていない"""
    c = local_counts()
    lines = [f"曲の記録 {c['state']}・作曲の指示 {c['briefs']}・選んだテイク {c['takes']}・配信用の音源 {c['masters']}・ジャケット {c['covers']}",
             f"参考曲：解析シート {c['sheets']}・登録済み {c['references']}／デビューの作業セット {c['kits']}（GitHub から）"]
    if inv is None:
        return _sec("local", "Mac の中の記録", None, lines + ["－ SSD がつながっていないので、足りているかは分かりません"])
    try:
        res = from_ssd(apply=False, ssd=ssd)
    except Exception as e:  # noqa: BLE001
        res = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
    if res.get("error"):
        return _sec("local", "Mac の中の記録", None, lines + ["－ SSD と見比べられませんでした：" + res["error"]])
    left = res["written"]
    if left:
        songs = sum(1 for x in res["songs"] if x["got"])
        lines.append(f"✕ SSD から戻せるものが、あと {left} 件あります（曲 {songs} 曲ぶん・解析シート {res['sheets']['missing']} 件 など）")
    else:
        lines.append(f"○ SSD の台帳（{res['rows']} 曲）と解析シートから戻せるものは、全部そろっています")
    return _sec("local", "Mac の中の記録", not left, lines, "" if not left else "SSD から記録を作り直す（先に「見るだけ」で中身を確かめられます：--apply を外す）",
                "" if not left else "python3 scripts/recover.py from-ssd --apply")


def check_r2(offline: bool = False) -> dict:
    env = _env_names()
    need = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    if not all(env.get(k) or os.environ.get(k) for k in need):
        return _sec("r2", "クラウドの予備（R2）", None, ["－ R2 の鍵がまだ無いので、確かめられません（鍵を入れたあとで）"])
    if offline:
        return _sec("r2", "クラウドの予備（R2）", None, ["－ 鍵はあります（今回は通信しません）"])
    rr, why = _mod("restore_r2")
    if rr is None:
        return _sec("r2", "クラウドの予備（R2）", None, [f"－ 確かめる部品が読めません（{why}）"])
    try:
        account, bucket, ak, sk = rr.creds()
        objs = rr.list_objects(account, bucket, ak, sk)
    except Exception as e:  # noqa: BLE001
        return _sec("r2", "クラウドの予備（R2）", False, [f"✕ R2 につながりません：{type(e).__name__}: {str(e)[:120]}"],
                    "R2 の鍵を確かめる", "python3 scripts/setup_keys.py --only r2")
    keys = [o.get("key", "") for o in objs]
    states = sum(1 for k in keys if k.startswith("label/out/publish/state/"))
    ssd = sum(1 for k in keys if k.startswith("label/ssd/"))
    vault = any(k.startswith("label/vault/") for k in keys)
    newest = max((o.get("modified") or 0 for o in objs), default=0)
    lines = [f"○ つながりました：預けてあるファイル {len(keys)} 件"
             + (f"（いちばん新しいもの {datetime.fromtimestamp(newest):%Y-%m-%d}）" if newest else ""),
             f"曲の記録 {states} 件・SSD の写し {ssd} 件・鍵の金庫 {'あり' if vault else 'なし'}"]
    ok = bool(keys) and states > 0
    return _sec("r2", "クラウドの予備（R2）", ok, lines, "" if ok else "いまの内容を R2 に預け直す", "" if ok else "python3 scripts/recover.py cloud")


def check_launchd() -> dict:
    if platform.system() != "Darwin":
        return _sec("launchd", "定期実行（常駐）", None, ["－ Mac だけの項目です"])
    agents = Path.home() / "Library" / "LaunchAgents"
    sched, why = _mod("schedule")
    prefix = getattr(sched, "PREFIX", "jp.etherarchi.label")
    have = sorted(p.name for p in agents.glob(f"{prefix}.*.plist")) if agents.is_dir() else []
    want = [*getattr(sched, "JOBS", {}), *getattr(sched, "SERVICES", {})] if sched is not None else []
    missing = [j for j in want if f"{prefix}.{j}.plist" not in have]
    lines = [f"登録されている定期実行 {len(have)} 個" + (f"（全部で {len(want)} 個）" if want else "")]
    if sched is None:
        lines.append(f"－ 定期実行の一覧が読めません（{why}）")
    if missing:
        lines.append("✕ まだ登録されていないもの：" + "・".join(missing))
    ok = bool(have) and not missing
    return _sec("launchd", "定期実行（常駐）", ok, lines, "" if ok else "定期実行を Mac に登録する（ダッシュボードもこれで立ち上がります）",
                "" if ok else "python3 scripts/schedule.py install")


def check_vault() -> dict:
    vault, why = _mod("vault")
    if vault is None:
        return _sec("vault", "鍵の金庫", None, [f"－ 金庫の部品が読めません（{why}）"])
    try:
        st = vault.status()
    except Exception as e:  # noqa: BLE001
        return _sec("vault", "鍵の金庫", None, [f"－ 金庫の状態が読めません（{type(e).__name__}）"])
    if not (ROOT / ".env").exists():
        return _sec("vault", "鍵の金庫", None, ["－ 鍵を入れたあとで作ります"])
    lines = [("○ 合言葉があります" if st.get("initialized") else "✕ まだ作っていません（鍵は、この Mac の中にしかありません）"),
             ("○ R2 に預けてあります（" + str(st.get("last_backup") or "")[:10] + "）" if st.get("uploaded") else "・R2 にはまだ預けていません")]
    ok = bool(st.get("initialized") and st.get("uploaded"))
    return _sec("vault", "鍵の金庫", ok, lines, "" if ok else "鍵の金庫を作る（次に Mac が壊れても、鍵を 1 回で戻せるように）",
                "" if ok else "python3 scripts/vault.py init")


def check(ssd: str | None = None, offline: bool = False) -> dict:
    _load_env()
    ssd_sec, inv = check_ssd(ssd)
    sections = [check_tools(), check_parts(), ssd_sec, check_local(inv, ssd), check_keys(), check_r2(offline), check_launchd(), check_vault()]
    first = next((s for s in sections if s["ok"] is False), None)
    nxt = {"id": first["id"], "what": first["next"], "cmd": first["cmd"]} if first else \
        {"id": "", "what": "立て直しは済んでいます。ダッシュボードの「点検して自動で直す」を 1 回押すと、細かい食い違いも直ります", "cmd": ""}
    return {"at": datetime.now().isoformat(timespec="seconds"), "python": ".".join(map(str, sys.version_info[:3])),
            "sections": sections, "next": nxt, "inventory": inv}


def print_check(res: dict) -> None:
    print("▶ 立て直しの点検（何も変えません）")
    if sys.version_info < MIN_PY:
        print(f"   ※ いま動かしている Python は {res['python']}（Mac に最初から入っている古い版）です。点検はできますが、"
              "仕組みを動かすには新しい Python が要ります（用意の台本  bash setup_mac.sh  が案内します）")
    for i, s in enumerate(res["sections"], 1):
        mark = {True: "○", False: "✕", None: "－"}[s["ok"]]
        print(f"\n■ {i}. {s['title']}　{mark}")
        for l in s["lines"]:
            print("   " + l)
    n = res["next"]
    print("\n【次にすること】" + n["what"])
    if n["cmd"]:
        print("     ターミナルで：  " + n["cmd"])
    done = sum(1 for s in res["sections"] if s["ok"] is True)
    print(f"【結果】{len(res['sections'])} 項目のうち {done} 項目がそろっています。" + ("" if n["id"] else "立て直しは済んでいます"))


# ---------------------------------------------------------------------------
# SSD から作り直す（from-ssd）
class Plan:
    """何を戻すかの一覧。apply=False なら、どこにも書かない"""

    def __init__(self, apply: bool):
        self.apply = apply
        self.songs: list = []        # 曲ごとの結果
        self.artists: list = []      # アーティストごとの行
        self.failed: list = []       # 戻せなかったもの
        self.review: list = []       # 人が確かめること
        self.written = 0             # 書いた（書く予定の）ファイルの数
        self.kept = 0                # もうあるので触らなかった数
        self.audio = True            # 登録済み・配信中の曲の、配信用の音源も写すか
        self.used: set = set()       # 台帳の行と結び付いた、SSD の曲のフォルダ

    def copy(self, src: Path, dst: Path) -> bool:
        """無いときだけ写す（もうあれば触らない）。写した・写す予定なら True"""
        if dst.exists():
            self.kept += 1
            return False
        self.written += 1
        if self.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
        return True

    def write(self, dst: Path, data, fresh_only: bool = True) -> bool:
        if fresh_only and dst.exists():
            self.kept += 1
            return False
        self.written += 1
        if self.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True


def _target_status(row: dict, files: dict) -> str:
    """台帳の段階と、SSD に残っているファイルから、戻す先の段階を決める。
    登録前の曲は「取り込み済み」まで（Suno の元データから、仕上げ・下書きを作り直す。前の仕上げの記録が無いので、同じ道を通し直す）"""
    ph = _text(row.get("フェーズ"))[:1]
    if ph == "8":
        return "live"
    if ph == "7" or _text(row.get("ISRC")):   # ISRC が書いてあれば、登録は済んでいる
        return "registered"
    return "imported" if files.get("raw") else ""


def _song_folder(base: Path, artist: str, title: str, week: str):
    music = _find(_find(_find(base, ARTIST_DIR), artist), SUBS["music"])
    if music is None:
        return None
    safe = re.sub(r"\s+", " ", str(title).replace("/", "／").replace(":", "：")).strip()
    for name in [n for n in (safe, week) if n]:
        d = _find(music, name)
        if d is not None:
            return d
    if title:   # 記号や全角半角が違うだけのフォルダ
        return next((d for d in _kids(music) if d.is_dir() and _norm(d.name) == _norm(title)), None)
    return None


def _restore_song(plan: Plan, base: Path, row: dict, song: dict, now: str) -> dict:
    """1 曲ぶん。song は {key, kind, slug, artist, title, week, kit（デビュー曲だけ）}"""
    key, artist, title = song["key"], song["artist"], song["title"]
    folder = None
    for t in [title] + [x for x in song.get("titles", []) if x and x != title]:   # 台帳の曲名と作業セットの曲名が違うデビュー曲は、両方で探す
        folder = folder or _song_folder(base, artist, t, song["week"] if song["kind"] == "weekly" else "")
    if folder is None and song["kind"] == "debut":
        folder = _song_folder(base, artist, "", song["week"])
    if folder is not None:
        plan.used.add(str(folder))
    files = song_files(folder)
    status = _target_status(row, files)
    done = status in ("live", "registered")
    item = {"id": _text(row.get("曲 ID")), "key": key, "artist": artist, "title": title, "phase": _text(row.get("フェーズ")),
            "status": status, "got": [], "notes": []}
    day = _day(row.get("配信予定日"))
    # 1 作曲の指示（毎週の曲だけ。デビュー曲は GitHub の作業セットにある）
    if song["kind"] == "weekly":
        alts = [x.strip() for x in _text(row.get("曲名の別案")).split(" / ") if x.strip()]
        brief = {"artist_slug": song["slug"], "week_start": song["week"], "title": title, "title_candidates": [t for t in [title] + alts if t],
                 "suno_style_prompt": _text(row.get("Suno の指示（スタイル）")), "suno_lyrics": _text(row.get("歌詞")),
                 "restored": {"from": "ssd", "at": now, "note": "SSD の楽曲管理.xlsx から作り直した最小限の指示（除外する言葉・場面・参考曲の割り当ては入っていません）"}}
        if song.get("label_slug"):
            brief["label_slug"] = song["label_slug"]
        if _text(row.get("言語")):
            brief["language"] = _text(row.get("言語"))
        if day:
            brief["release_at"] = f"{day}T21:00:00+00:00"
        try:
            brief["bpm"] = int(float(_text(row.get("BPM"))))
        except ValueError:
            pass
        if not brief["suno_style_prompt"]:
            brief.pop("suno_style_prompt")
        if plan.write(OUT / "briefs" / f"{key}.json", brief):
            item["got"].append("作曲の指示")
    # 2 ジャケット（小さいので、どの段階の曲も戻す）
    cover = files["cover"]
    if cover is None:   # 曲のフォルダに無ければ、03_ジャケット の <名前>_<曲名>_<番号>.jpg
        stem = _norm(f"{artist}_{title or song['week']}")
        cands = []
        for f in _kids(_find(_find(_find(base, ARTIST_DIR), artist), SUBS["cover"])):
            m = re.search(r"^(.*)_(\d+)$", _nfc(f.stem))
            if f.is_file() and m and _norm(m.group(1)) == stem and not _nfc(f.stem).endswith("_old"):
                cands.append((int(m.group(2)), str(f), f))
        cover = max(cands)[2] if cands else None
    if cover is not None:
        dst = (song["kit"] / "images" / f"{song['slug']}_cover.jpg") if song["kind"] == "debut" else \
            (OUT / "visuals" / song["slug"] / "cover" / song["week"] / "cover_final.jpg")
        if cover.suffix.lower() in (".jpg", ".jpeg") and plan.copy(cover, dst):
            item["got"].append("ジャケット")
            # 文字（アーティスト名・曲名）はもう入っている、という印。これが無いと、次の取り込みで文字が二重に入る
            texted = song["kind"] == "weekly" or done or _text(row.get("フェーズ"))[:1] == "6"
            if texted and title:
                plan.write(dst.with_suffix(".text.json"), {"ok": True, "artist": artist, "title": title, "restored": True,
                                                           "note": "SSD から戻したジャケット。文字は入っている前提（入れ直さない）"})
            elif not done:
                plan.review.append(f"{artist}「{title}」：戻したジャケットに文字（アーティスト名・曲名）が入っているか分かりません。"
                                   "次の取り込みで文字を入れるので、二重になっていないかダッシュボードで見てください")
    elif status:
        item["notes"].append("ジャケットは SSD に見つかりません" + ("" if done else "（取り込みのあと、作り直します）"))
    # 3 音源：登録前の曲は Suno の元データ（選んだテイク）、登録済み・配信中の曲は配信用の音源
    take_name = ""
    if files["raw"] is not None and not done:
        raw = files["raw"]
        astem = re.sub(r"(%s)+(_\d+)?$" % re.escape(RAW_MARK), "", _nfc(raw.stem))
        take_name = f"{astem}{raw.suffix.lower()}"
        tdir = OUT / "takes" / key
        if plan.copy(raw, tdir / take_name):
            item["got"].append("選んだテイク")
        plan.write(tdir / "selection.json", {"decision": {"selected": take_name, "by": "SSD から復元（Suno の元データ）", "at": now},
                                               "takes": [{"take": take_name, "path": str(tdir / take_name)}], "source": str(raw)})
    if files["master"] is not None and done and plan.audio:
        if plan.copy(files["master"], OUT / "masters" / key / "master.wav"):
            item["got"].append("配信用の音源")
    if not done and files["raw"] is None and files["master"] is not None:
        plan.review.append(f"{artist}「{title}」：SSD に配信用の音源はありますが、Suno の元データがありません。"
                           "このままでは仕上げ直せないので、Suno でもう一度ダウンロードするか、SSD の音源を受け口（楽曲情報）へ写してください")
    for name, src in files["docs"].items():   # 配信用の歌詞・日本語訳・エピソード
        if plan.copy(src, OUT / "publish" / key / name):
            item["got"].append(name.rsplit(".", 1)[0])
    # 4 曲の記録
    st = {}
    if status == "live":
        st = {"status": "live", "released": day, "release_date": day, "isrc": _text(row.get("ISRC")), "upc": _text(row.get("UPC")),
              "url": _text(row.get("配信 URL")), "live_marked_by": "recover"}
    elif status == "registered":
        st = {"status": "registered", "release_date": day, "registered_at": _day(row.get("更新日")) or now[:10],
              "isrc": _text(row.get("ISRC")), "upc": _text(row.get("UPC")), "url": _text(row.get("配信 URL"))}
        if not day:
            item["notes"].append("配信日が台帳にありません（ダッシュボードで「← 戻す」→「登録した」で入れ直してください）")
    elif status == "imported":
        st = {"status": "imported", "take": take_name, "source": str(files["raw"]), "imported_at": now}
    if st:
        m = re.search(r"_(\d{4}-\d{2}-\d{2})$", Path(take_name).stem if take_name else _nfc((files["master"] or Path("x")).stem))
        if m:
            st["generated_on"] = m.group(1)
        if not st.get("take") and _text(row.get("選んだテイク")):
            st["take"] = _text(row.get("選んだテイク"))
        if _text(row.get("Suno のモデル")):
            st["suno_model"] = _text(row.get("Suno のモデル"))
        st = {k: v for k, v in st.items() if v not in ("", None)}
        st["restored"] = {"from": "ssd", "at": now}
        sp = STATE_DIR / f"{key}.json"
        old = _read_json(sp) if sp.exists() else None
        if old is None:
            plan.written += 1
            item["got"].append("曲の記録")
            item["state"] = st
        else:
            add = {k: st[k] for k in RELEASE_KEYS if st.get(k) and not old.get(k)}
            plan.kept += 1
            if add:
                item["state"] = {**old, **add}
                item["got"].append("配信の記録の空いている所（" + "・".join(KEY_JA.get(k, k) for k in add) + "）")
                plan.written += 1
            item["notes"].append("Mac に記録があるので、そのまま残します")
            item["status"] = old.get("status", "")
    elif song["kind"] == "weekly" and _text(row.get("フェーズ"))[:1] in ("3", "4", "5", "6"):
        item["notes"].append("台帳では作曲のあとまで進んでいましたが、SSD に音源がありません（作曲の前に戻ります）")
    if status == "live":
        miss = [n for n, k in (("ISRC", "ISRC"), ("UPC", "UPC"), ("配信の住所", "配信 URL")) if not _text(row.get(k))]
        if miss:
            item["notes"].append("台帳に " + "・".join(miss) + " がありません（空のまま戻します。分かったら、SSD の楽曲管理シートかダッシュボードで書き足してください）")
    return item


def _restore_artists(plan: Plan, base: Path, common) -> None:
    kits = sorted((ROOT / "debut_kit").glob("*/kit.json"), reverse=True)
    kit_of = {}
    for kj in kits:   # 新しい作業セットを優先
        for s in _read_json(kj).get("songs", []):
            kit_of.setdefault(s.get("slug"), kj.parent)
    acts = {a.get("name") or a.get("slug"): a for a in common.all_artists(include_retiring=True) if a.get("slug")}
    for adir in [d for d in _kids(_find(base, ARTIST_DIR)) if d.is_dir()]:
        a = next((v for n, v in acts.items() if _same(n, adir.name)), None)
        if a is None:
            plan.failed.append(f"アーティスト「{_nfc(adir.name)}」：GitHub に設定書がありません（外した組か、名前を変えた組）。SSD のフォルダはそのまま残ります")
            continue
        slug, got = a["slug"], []
        for f in [f for f in _kids(_find(adir, SUBS["info"])) if f.is_file() and not _nfc(f.stem).endswith("_old")]:
            name = _nfc(f.name)
            for ext in ("md", "json"):
                if name.endswith(f"_プロフィール.{ext}") and plan.copy(f, OUT / "profiles" / f"{slug}.{ext}"):
                    got.append(f"プロフィール（{ext}）")
            if name.endswith("_設定書.json"):   # 設定書は GitHub を正とする。配信先のページ・デビュー週が SSD とだけ違うときに知らせる
                ssd_a, cur = _read_json(f), a
                diffs = []
                if (ssd_a.get("debut_week") or "") != (cur.get("debut_week") or "") and ssd_a.get("debut_week"):
                    diffs.append("デビュー週")
                sd, cd = ssd_a.get("distribution") or {}, cur.get("distribution") or {}
                if sd.get("spotify_uri") and sd.get("spotify_uri") != cd.get("spotify_uri"):
                    diffs.append("Spotify のページ")
                for k, v in (sd.get("artist_pages") or {}).items():
                    if v and (cd.get("artist_pages") or {}).get(k) != v:
                        diffs.append(f"配信先のページ（{k}）")
                if diffs:
                    plan.review.append(f"{a.get('name')}：設定書の {'・'.join(diffs)} が、GitHub と SSD（{name}）で違います。GitHub のものを使います"
                                       "（SSD のほうが正しければ、ダッシュボードの「アーティスト」で入れ直してください）")
        kit = kit_of.get(slug)
        if kit is not None:   # デビュー用の写真（画像は GitHub に入らない）
            for f in [f for f in _kids(_find(adir, SUBS["photo"])) if f.is_file()]:
                for kind in ("photo", "photo2"):
                    if _same(_nfc(f.name), f"{a.get('name')}_{kind}.jpg") and plan.copy(f, kit / "images" / f"{slug}_{kind}.jpg"):
                        got.append("写真" + ("" if kind == "photo" else " 2"))
        plan.artists.append({"name": a.get("name") or slug, "slug": slug, "got": got})


def _restore_sheets(plan: Plan, inv: dict) -> dict:
    """既存楽曲の解析シートを Mac に読み込む（解析し直さない）。reference_db.ensure_sheets の「読み込む」所と同じ決まり：
    シートの名前は、音源の場所と大きさから決まる番号（aud-…）。SSD に音源が残っている曲のシートだけを読み込み、どの音源のものかも記録する"""
    have = {}
    for x in inv["refs"]["sheets"]:
        for sp in Path(x["path"]).glob("aud-*.json"):
            have.setdefault(sp.name, sp)
    res = {"ssd": len(have), "missing": 0, "restored": 0, "orphans": 0, "error": ""}
    if not have:
        return res
    rd, why = _mod("reference_db")
    if rd is None:
        res["error"] = f"参考曲の部品が読めません（{why}）"
        return res
    try:
        cache, todo, live = rd.AUDIO_CACHE, [], set()
        for x in inv["refs"]["audio"]:
            top = Path(x["path"])
            for f in rd.files_in(top):
                if f.suffix.lower() not in rd.AUDIO_TAKE:
                    continue
                rid = rd._rid(top, f)
                live.add(f"{rid}.json")
                if f"{rid}.json" in have and not (cache / f"{rid}.json").exists():
                    todo.append((rid, f, have[f"{rid}.json"]))
        res["missing"] = len(todo)
        res["orphans"] = sum(1 for n in have if n not in live and not (cache / n).exists())   # 音源が SSD から無くなった曲のシート（読み込まない）
        plan.written += len(todo)
        if not plan.apply or not todo:
            return res
        cache.mkdir(parents=True, exist_ok=True)
        index = json.loads(rd.AUDIO_INDEX.read_text(encoding="utf-8")) if rd.AUDIO_INDEX.exists() else {}
        for rid, f, src in todo:
            shutil.copy2(str(src), str(cache / f"{rid}.json"))
            st = f.stat()
            index[rid] = {"sig": f"{st.st_size}|{int(st.st_mtime)}", "file": str(f), "at": datetime.now().isoformat(timespec="seconds"), "restored": True}
            res["restored"] += 1
        rd.AUDIO_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {str(e)[:140]}"
    return res


def from_ssd(apply: bool = False, ssd: str | None = None, audio: bool = True) -> dict:
    _load_env()
    if sys.version_info < MIN_PY:
        return {"error": f"いまの Python（{sys.version_info.major}.{sys.version_info.minor}）は古い版です。先に用意の台本（bash setup_mac.sh）で新しい Python を入れてください"}
    drive, base, how = find_ssd(ssd)
    if base is None:
        return {"error": how}
    os.environ["ARCHIVE_ROOT"] = str(base)   # ほかの部品にも、同じ保管庫を見せる
    common, why = _mod("_common")
    songs_m, why2 = _mod("_songs")
    if common is None or songs_m is None:
        return {"error": f"仕組みの部品が読めません（{why or why2}）。先に用意の台本を：  bash setup_mac.sh"}
    inv = inventory(drive, base)
    book = next((f for f in _kids(_find(base, BOOK_DIR)) if f.is_file() and _same(f.name, BOOK_NAME)), None)
    plan = Plan(apply)
    plan.audio = audio
    now = datetime.now().isoformat(timespec="seconds")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows = []
    if book is None:
        plan.failed.append(f"SSD に {BOOK_DIR}/{BOOK_NAME} がありません：曲の段階・ISRC・UPC・配信の住所は戻せません")
    else:
        try:
            rows = read_book(book)
        except Exception as e:  # noqa: BLE001
            plan.failed.append(f"{BOOK_NAME} が読めません（{type(e).__name__}: {str(e)[:100]}）")
        if apply:   # 台帳の写しを Mac に取っておく（このあとの保管で台帳が書き直されても、元の値を見返せる）
            keep = RESTORE / "ssd_book" / f"楽曲管理_{stamp}.xlsx"
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(book), str(keep))
    _restore_artists(plan, base, common)
    debut = songs_m.debut_songs()
    seen_keys = set()
    for row in rows:
        sid, artist, title = _text(row.get("曲 ID")), _text(row.get("アーティスト")), _text(row.get("曲名"))
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_(.+)$", sid)
        if m:   # 毎週の曲・不定期の曲：曲 ID が <制作週>_<組>
            a = common.load_artist(m.group(2), None, missing_ok=True)
            if a is None:
                plan.failed.append(f"{artist}「{title}」（{sid}）：組 {m.group(2)} の設定書が GitHub にありません")
                continue
            lab = a.get("label_slug")
            song = {"key": sid, "kind": "weekly", "slug": m.group(2), "artist": a.get("name") or artist or m.group(2), "title": title,
                    "week": m.group(1), "kit": None, "label_slug": lab if lab and lab != getattr(common, "MAIN_LABEL", "drive") else None}
        else:   # デビュー曲：アーティスト名と曲名で、GitHub の作業セットの曲と突き合わせる（曲 ID は作業セットごとに 001 から振り直すため）
            d = next((x for x in debut if _norm(x["artist"]) == _norm(artist) and _norm(x["title"]) == _norm(title)), None) \
                or next((x for x in debut if x["id"] == sid and _norm(x["artist"]) == _norm(artist)), None)
            if d is None:
                plan.failed.append(f"{artist}「{title}」（{sid}）：GitHub の作業セット・設定書に、この曲が見つかりません（手で足した行か、外した組の曲）")
                continue
            song = {"key": d["key"], "kind": "debut", "slug": d["slug"], "artist": d["artist"], "title": d["title"] or title, "week": d["week"], "kit": d["kit"],
                    "titles": [title]}
            if title and d["title"] and _norm(title) != _norm(d["title"]):
                plan.review.append(f"{d['artist']} のデビュー曲：台帳の曲名「{title}」と、GitHub の作業セットの曲名「{d['title']}」が違います。"
                                   "作業セットの曲名を使います（台帳のほうが正しければ、ダッシュボードの「✎ 曲名を変える」で直してください）")
        if song["key"] in seen_keys:
            plan.failed.append(f"{artist}「{title}」（{sid}）：台帳に同じ曲の行が 2 つあります（あとの行は飛ばしました）")
            continue
        seen_keys.add(song["key"])
        plan.songs.append(_restore_song(plan, base, row, song, now))
    if apply:   # 曲の記録は、作曲の指示を書いたあとに、いつもの入口（_songs.save_state）から書く：登録データの写しも一緒にできる
        for it in plan.songs:
            if "state" not in it:
                continue
            song = songs_m.by_key(it["key"])
            if song is not None:
                songs_m.save_state(song, it["state"])
            else:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                (STATE_DIR / f"{it['key']}.json").write_text(json.dumps(it["state"], ensure_ascii=False, indent=1), encoding="utf-8")
    for adir in [d for d in _kids(_find(base, ARTIST_DIR)) if d.is_dir()]:   # 台帳に行が無い曲のフォルダ（手で作った・台帳を書く前に初期化した）
        loose = [_nfc(d.name) for d in _kids(_find(adir, SUBS["music"])) if d.is_dir() and str(d) not in plan.used]
        if loose and rows:
            plan.review.append(f"{_nfc(adir.name)}：SSD に曲のフォルダはありますが、台帳（{BOOK_NAME}）に行が無い曲 → " + "・".join(loose)
                               + "（記録は作りません。配信中の曲なら、台帳に行を足してからもう一度）")
    sheets = _restore_sheets(plan, inv)
    res = {"apply": apply, "base": str(base), "rows": len(rows), "songs": plan.songs, "artists": plan.artists, "failed": plan.failed,
           "review": plan.review, "sheets": sheets, "written": plan.written, "kept": plan.kept, "inbox": inv["inbox"], "never": NEVER, "at": now}
    if apply:
        log = RESTORE / f"from_ssd_{stamp}.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        slim = {**res, "songs": [{k: v for k, v in s.items() if k != "state"} for s in plan.songs]}
        log.write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
        res["log"] = str(log)
    return res


def print_from_ssd(res: dict) -> None:
    if res.get("error"):
        print("【結果】戻せませんでした：" + res["error"])
        return
    mode = "戻しました" if res["apply"] else "見るだけ（まだ何も書いていません）"
    print(f"▶ SSD から Mac の記録を作り直す：{mode}\n   SSD：{res['base']}\n   楽曲管理.xlsx：{res['rows']} 曲")
    verb = "戻した" if res["apply"] else "戻す"
    print(f"\n■ 曲（{len(res['songs'])} 曲）")
    for s in res["songs"]:
        mark = "○" if s["got"] else "・"
        what = "・".join(s["got"]) if s["got"] else "戻すものはありません"
        print(f"   {mark} {s['artist']}「{s['title'] or '（曲名なし）'}」 → {STATUS_JA.get(s['status'], s['status'])}／{verb}もの：{what}")
        for n in s["notes"]:
            print(f"        ※ {n}")
    counts = {}
    for s in res["songs"]:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    print("   段階ごと：" + ("・".join(f"{STATUS_JA.get(k, k).split('（')[0]} {v} 曲" for k, v in counts.items()) or "—"))
    print(f"\n■ アーティスト（{len(res['artists'])} 組）")
    for a in res["artists"]:
        print(f"   {'○' if a['got'] else '・'} {a['name']}：" + ("・".join(a["got"]) if a["got"] else "戻すものはありません（設定書は GitHub にあります）"))
    sh = res["sheets"]
    print("\n■ 参考曲（既存楽曲）の解析シート")
    if sh.get("error"):
        print("   ✕ " + sh["error"])
    elif res["apply"]:
        print(f"   SSD に {sh['ssd']} 件 → Mac に読み込んだもの {sh['restored']} 件")
    else:
        print(f"   SSD に {sh['ssd']} 件・そのうち Mac に読み込めるもの {sh['missing']} 件（解析し直しません）")
    if sh.get("orphans") and not sh.get("error"):
        print(f"   ・音源が SSD に見つからないシート {sh['orphans']} 件は読み込みません（音源を消した・場所を変えた曲）")
    if res["inbox"]:
        print(f"\n■ 受け口（SSD の 楽曲情報）に、まだ取り込んでいない音源が {res['inbox']} 本あります（立て直したあとの取り込みで拾います）")
    if res["review"]:
        print("\n■ 確かめてほしいこと")
        for l in res["review"]:
            print("   △ " + l)
    print("\n■ 戻せなかったもの")
    for l in res["failed"]:
        print("   ✕ " + l)
    for l in res["never"]:
        print("   － " + l)
    if res["apply"]:
        print(f"\n   記録：{res.get('log', '')}")
        print("【次にすること】ダッシュボードの「点検して自動で直す」（ターミナルなら  python3 scripts/health.py heal ）を 1 回。"
              "登録前の曲の仕上げ直しと、下書きの作り直しが動きます。そのあと  python3 scripts/recover.py cloud")
        print(f"【結果】{res['written']} 件を戻しました（もうあったので触らなかったもの {res['kept']} 件・戻せなかった行 {len(res['failed'])} 件）")
    else:
        print("\n【次にすること】この内容でよければ：  python3 scripts/recover.py from-ssd --apply")
        print(f"【結果】戻せるもの {res['written']} 件（もうあるので触らないもの {res['kept']} 件・戻せない行 {len(res['failed'])} 件）。まだ何も書いていません")


# ---------------------------------------------------------------------------
# クラウドを作り直す（cloud）
def cloud(dry: bool = False) -> int:
    """戻した内容で、R2 の予備と Notion を作り直す。どれも、いつもの部品をそのまま動かすだけ"""
    _load_env()
    env = _env_names()
    has = lambda *ks: all(env.get(k) or os.environ.get(k) for k in ks)  # noqa: E731
    c = local_counts()
    print("▶ クラウドを作り直す（R2 の予備・鍵の金庫・Notion）")
    if not c["state"]:
        _d, base, _h = find_ssd()
        print("   ※ Mac に曲の記録がまだありません。" + ("先に  python3 scripts/recover.py from-ssd --apply  を" if base else "SSD をつないで、先に from-ssd を"))
    steps = []
    if has("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        steps.append(("R2 の予備（Mac の記録と、SSD の中身の写し）", [PY, str(S / "backup_r2.py")] + (["--dry-run"] if dry else [])))
        vault, _w = _mod("vault")
        try:
            inited = bool(vault and vault.status().get("initialized"))
        except Exception:  # noqa: BLE001
            inited = False
        if inited:
            if not dry:
                steps.append(("鍵の金庫（暗号にして R2 へ）", [PY, str(S / "vault.py"), "backup", "--if-changed"]))
        else:
            print("   － 鍵の金庫：まだ作っていません（あとで  python3 scripts/vault.py init ）")
    else:
        print("   － R2 の予備：飛ばします（R2 の鍵がまだ入っていません →  python3 scripts/setup_keys.py --only r2 ）")
    if has("NOTION_TOKEN", "NOTION_PARENT_PAGE"):
        steps.append(("Notion の表", [PY, str(S / "notion_sync.py")] + (["--dry-run"] if dry else [])))
    else:
        print("   － Notion：飛ばします（Notion の鍵がまだ入っていません →  python3 scripts/setup_keys.py --only notion ）")
    bad = 0
    for i, (title, cmd) in enumerate(steps, 1):
        print(f"【進み具合】{i}/{len(steps)}：{title}", flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            bad += 1
            print(f"   ✕ {title} が途中で止まりました（上の表示を見てください）")
    if not steps:
        print("【結果】動かせるものがありませんでした（鍵を入れてから、もう一度）")
        return 0
    print(f"【結果】{len(steps) - bad} / {len(steps)} 個が終わりました" + ("" if not bad else "（止まったものは、原因を直してからもう一度）"))
    return 1 if bad else 0


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="初期化した Mac・買い替えた Mac を、SSD から立て直す")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="どこまで戻っているかを見る（何も変えない）")
    c.add_argument("--json", action="store_true", help="機械が読む形で出す")
    c.add_argument("--offline", action="store_true", help="通信しない（R2 を確かめない）")
    c.add_argument("--ssd", help="SSD の場所（例 \"/Volumes/SP DS72\"）")
    f = sub.add_parser("from-ssd", help="SSD から Mac の記録を作り直す（付けなければ見るだけ）")
    f.add_argument("--apply", action="store_true", help="実際に書く")
    f.add_argument("--no-audio", action="store_true", help="登録済み・配信中の曲の、配信用の音源は写さない（速い）")
    f.add_argument("--json", action="store_true")
    f.add_argument("--ssd", help="SSD の場所")
    k = sub.add_parser("cloud", help="R2 の予備と Notion を作り直す（鍵があるものだけ）")
    k.add_argument("--dry-run", action="store_true", help="何を上げるかだけ表示")
    a = ap.parse_args()
    if a.cmd == "from-ssd":
        res = from_ssd(a.apply, a.ssd, not a.no_audio)
        if a.json:
            print(json.dumps({**res, "songs": [{k2: v for k2, v in s.items() if k2 != "state"} for s in res.get("songs", [])]},
                             ensure_ascii=False, indent=1))
        else:
            print_from_ssd(res)
        return 2 if res.get("error") else 0
    if a.cmd == "cloud":
        return cloud(a.dry_run)
    res = check(getattr(a, "ssd", None), getattr(a, "offline", False))
    if getattr(a, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print_check(res)
    return 0



def _use_venv() -> None:
    """用意の台本が作った専用の置き場（.venv）があれば、そちらの Python で動き直す。
    ターミナルの窓を開き直す前は、python3 と打つと部品の入っていない Python が動くため（決定 180）"""
    v = ROOT / ".venv" / "bin" / "python3"
    if sys.prefix == sys.base_prefix and v.is_file() and not os.environ.get("POSTDOG_NO_VENV"):
        os.execv(str(v), [str(v)] + sys.argv)

if __name__ == "__main__":
    _use_venv()
    sys.exit(main())

#!/usr/bin/env python3
"""
SSD に置いた「データベース」（参考曲・資料）を読み、楽曲制作（ブリーフ）の参考にする

探す場所（上から順に）：
  1. .env の REFERENCE_DB（フォルダかファイル。`--setup <場所>` で書ける）
  2. SSD（DS72）の一番上と「Postdog Records」の中で、名前に
     データベース・database・DB・参考・リファレンス・reference・ライブラリ・library を含むフォルダかファイル、
     と、.db・.sqlite のファイル

読めるもの：
  ・解析シート（templates/analysis_sheet.schema.json の形の JSON）… そのまま参考曲として使う
  ・表（.xlsx・.csv・.tsv・JSON の一覧・SQLite の表）… 1 行 = 1 曲として、見出しから
      曲名・アーティスト・BPM・キー・ジャンル・タグ（雰囲気）・テーマ・ボーカル・楽器・構成・言語・年・メモ を読み取る。
      読み取れなかった欄も「その他」として残す
  ・文章（.md・.txt）… 制作メモとして傾向の要約に入れる
  ・音源（.mp3・.wav など）… データベースの中のものは数だけ数える
  ・SSD の「既存楽曲」フォルダの音源（WAV・MP3・M4A）… 1 曲ずつ機械で解析して、参考曲の解析シートにする（決定 76）
      BPM・キー・長さ・セクション・エネルギーの起伏・サビの入り（analyze_track.py の計測）。曲名・アーティストは
      ファイルの情報（タグ）か、ファイル名「アーティスト - 曲名」・フォルダ名から。1 回に --audio-limit 曲まで（既定 20。続きは次の回）。
      解析した曲は覚えておき、2 回目からは新しく入れた曲だけ解析する。場所は .env の REFERENCE_AUDIO でも指定できる
  ・Numbers（.numbers）は読めないので、Numbers で「書き出す → Excel」してから置く

出すもの（Mac の中。SSD が無いときも、前回取り込んだものを使い続ける）：
  ・out/references/db-<番号>.json … 参考曲の解析シート（select_references.py・write_brief.py が読む）
  ・out/reference_db/digest.json・digest.md … データベース全体の傾向（タグ・ジャンル・BPM・テーマ・メモ）。
      write_brief.py が Claude に渡す。実在のアーティスト名・曲名は入れない
  ・out/reference_db/summary.md … 何が見つかり、どう読んだかの一覧（--show でも表示）
  ・out/reference_db/artists/<組>.json・artists.md … 組ごとの「近い参考曲」と傾向（既存の組の作曲に使う）。
      近さ＝ジャンル・タグ・楽器の言葉の重なり・テンポ・声の性別。データベースに組の名前（例：TIDEWOOL）を書いた曲は最優先。
      select_references.py（参考曲の割り当ての重み）・write_brief.py（Claude に渡す）・suno_design.py（スタイル欄の後ろに足す言葉）が読む

使い方：
  python3 scripts/reference_db.py --show                # 見つかったデータベースの中身と読み方を表示するだけ
  python3 scripts/reference_db.py                       # 取り込む（run_week.py のブリーフ作りの前にも自動で動く）
  python3 scripts/reference_db.py --setup "/Volumes/SP DS72/データベース"   # 場所を .env に書く
  python3 scripts/reference_db.py --artists             # 組ごとの近い参考曲だけ作り直す（SSD が無くても前回の取り込みから）
  python3 scripts/reference_db.py --audio-limit 0       # 「既存楽曲」の音源を全部解析する（1 曲 10〜30 秒）
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_dotenv  # noqa: E402

load_dotenv()
import _archive as archive  # noqa: E402

REF_DIR = OUT / "references"
DIGEST_DIR = OUT / "reference_db"
PREFIX = "db-"
HINTS = ["データベース", "database", "db", "参考", "リファレンス", "reference", "ライブラリ", "library"]
TABLE_EXT = {".xlsx", ".xlsm", ".csv", ".tsv"}
TEXT_EXT = {".md", ".txt"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aif", ".aiff", ".aac"}
SQLITE_EXT = {".db", ".sqlite", ".sqlite3"}
MAX_ROWS = 5000

# 見出し → 欄（上から順に見る。アーティストを曲名より先に見る：「song artist」などのため）
FIELDS = [
    ("for_artist", ["使う組", "向いている組", "合う組", "どの組", "使うアーティスト", "向いているアーティスト", "うちの組", "for artist", "use for"]),
    ("artist", ["アーティスト", "artist", "歌手", "バンド", "band", "歌い手"]),
    ("title", ["曲名", "タイトル", "title", "song", "track", "楽曲名", "曲"]),
    ("bpm", ["bpm", "テンポ", "tempo"]),
    ("key", ["キー", "key", "調"]),
    ("genre", ["ジャンル", "genre"]),
    ("tags", ["タグ", "tag", "ムード", "mood", "雰囲気", "キーワード", "keyword", "空気", "シーン", "scene", "曲調", "カラー"]),
    ("theme", ["テーマ", "theme", "歌詞", "lyric", "内容", "世界観", "物語", "story"]),
    ("vocal", ["ボーカル", "vocal", "声", "歌声", "voice"]),
    ("instruments", ["楽器", "instrument", "編成", "音色", "サウンド", "sound"]),
    ("structure", ["構成", "structure", "展開"]),
    ("language", ["言語", "language", "lang"]),
    ("year", ["年", "year", "発売", "リリース", "release"]),
    ("memo", ["メモ", "memo", "note", "特徴", "ポイント", "参考にしたい", "良い", "好き", "感想", "コメント", "comment", "理由"]),
    ("skip", ["url", "リンク", "link", "id", "番号", "no.", "spotify", "youtube", "apple"]),
]


def norm(s: object) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip().casefold()


# 歌詞の本文は渡さない（借りるのはテーマだけ。docs/04_borrowing_rules.md）。作曲者などの実名も渡さない
NOT_USED = ["歌詞", "lyrics", "lyric", "歌詞全文", "本文"]
CREDITS = ["作曲", "作詞", "編曲", "composer", "lyricist", "writer", "producer", "プロデューサー"]
LABEL = {"artist": "アーティスト（実名は制作の指示に入れない）", "title": "曲名", "bpm": "BPM", "key": "キー", "genre": "ジャンル",
         "tags": "タグ・雰囲気", "theme": "テーマ", "vocal": "ボーカル", "instruments": "楽器", "structure": "構成",
         "language": "言語", "year": "年", "memo": "メモ（参考にしたい点）", "other": "その他（メモとして渡す）",
         "skip": "使わない（URL・番号・作者名・歌詞の本文）",
         "for_artist": "使う組（この組の作曲で最優先にする）"}
# 部分一致で見る順（キーワード→キー、曲調→曲名 の取り違えを防ぐ）
ORDER = ["skip", "for_artist", "artist", "tags", "theme", "memo", "title", "bpm", "key", "genre", "vocal", "instruments", "structure", "language", "year"]


def field_of(header: str) -> str | None:
    """見出しから欄を決める。None は「その他」"""
    h = norm(header)
    if not h:
        return None
    if h in ("db", "no", "#") or h in {norm(w) for w in NOT_USED}:
        return "skip"
    if any(norm(w) in h for w in CREDITS):
        return "skip"
    words = dict(FIELDS)
    for f in ORDER:                                    # 1. 見出しがそのまま同じ
        if h in {norm(w) for w in words[f]}:
            return f
    for f in ORDER:                                    # 2. 見出しに含まれている
        if any(norm(w) in h for w in words[f] if len(norm(w)) >= 2 or f in ("title", "key", "year")):
            return f
    return None


# ---------------------------------------------------------------------------
# 「既存楽曲」の音源を解析して、参考曲の解析シートにする（オーナーの指示 2026-10-25。決定 76）
AUDIO_DIR_HINTS = ["既存楽曲"]
AUDIO_TAKE = {".wav", ".mp3", ".m4a"}
AUDIO_CACHE = DIGEST_DIR / "audio"          # 解析した結果（実名つき。Mac の中だけ）
AUDIO_INDEX = DIGEST_DIR / "audio_index.json"


def audio_dirs() -> list[Path]:
    """「既存楽曲」のフォルダ（.env の REFERENCE_AUDIO か、SSD の一番上・Postdog Records の中から 3 段まで）"""
    env = os.environ.get("REFERENCE_AUDIO", "").strip()
    if env:
        return [p for p in (Path(x.strip()).expanduser() for x in env.split(";") if x.strip()) if p.is_dir()]
    base, _ = archive.root()
    if base is None:
        return []
    found: list[Path] = []
    for top in (base.parent, base):
        for c in _walk(top, 3):
            if c.is_dir() and any(norm(h) in norm(c.name) for h in AUDIO_DIR_HINTS) and not any(c.is_relative_to(f) for f in found):
                found.append(c)
    return found


def _tags(f: Path) -> dict:
    """ファイルに書かれた曲の情報（曲名・アーティスト・ジャンル・年）。ffprobe で読む"""
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(f)],
                           capture_output=True, text=True, timeout=30)
        t = {k.lower(): v for k, v in (json.loads(r.stdout or "{}").get("format", {}).get("tags") or {}).items()}
    except Exception:  # noqa: BLE001
        return {}
    return {"title": t.get("title", ""), "artist": t.get("artist") or t.get("album_artist", ""), "genre": t.get("genre", ""),
            "year": (re.match(r"\d{4}", t.get("date", "") or "") or [None])[0]}


def _who(f: Path, top: Path) -> tuple[str, str]:
    """曲名とアーティスト：タグ → ファイル名「アーティスト - 曲名」→ フォルダ名（既存楽曲/<アーティスト>/…）"""
    t = _tags(f)
    title, artist = t.get("title", "").strip(), t.get("artist", "").strip()
    stem = re.sub(r"^\d{1,3}[\s._-]+", "", f.stem).strip()   # 先頭のトラック番号
    if not title or not artist:
        m = re.match(r"(.+?)\s+[-–—]\s+(.+)", stem)
        if m:
            artist, title = artist or m.group(1).strip(), title or m.group(2).strip()
    if not artist:
        rel = f.parent.relative_to(top).parts if f.parent != top else ()
        artist = rel[0] if rel else "?"
    return title or stem, artist


def _measure(analyze_track, f: Path) -> dict:
    """解析する。そのまま読めない音源（M4A など）は、ffmpeg で一時的に WAV にしてから読む（元のファイルは変えない）"""
    try:
        return analyze_track.measure(f)
    except Exception:  # noqa: BLE001
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("ffmpeg"):
            raise
        with tempfile.TemporaryDirectory() as d:
            w = Path(d) / "tmp.wav"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(f), "-ac", "1", "-ar", "22050", str(w)], check=True)
            return analyze_track.measure(w)


NEW_AUDIO: list[str] = []   # この回に新しく解析した曲（「アーティスト「曲名」」。通知に使う）


def analyze_audio(limit: int = 20, quiet: bool = False) -> list[tuple[Path, dict]]:
    """「既存楽曲」の音源を解析する（新しい曲だけ。1 回に limit 曲まで。0 なら全部）。解析済みの全曲の (ファイル, シート) を返す。
    新しく解析した曲は NEW_AUDIO に入る。書き込みの途中（最後に変わってから 1 分以内）のファイルは次の回に回す（決定 78）"""
    import time as _time
    dirs = audio_dirs()
    if not dirs:
        if not quiet:
            print("－ 「既存楽曲」のフォルダが見つかりません（SSD をつなぐか、.env の REFERENCE_AUDIO で場所を指定してください）")
        return []
    import hashlib as _h
    AUDIO_CACHE.mkdir(parents=True, exist_ok=True)
    index = json.loads(AUDIO_INDEX.read_text(encoding="utf-8")) if AUDIO_INDEX.exists() else {}
    files = [(top, f) for top in dirs for f in files_in(top) if f.suffix.lower() in AUDIO_TAKE]
    todo, out, seen = [], [], set()
    busy = 0
    for top, f in files:
        st = f.stat()
        rid = "aud-" + _h.sha1(f"{f.relative_to(top)}|{st.st_size}".encode()).hexdigest()[:12]
        seen.add(rid)
        if _time.time() - st.st_mtime < 60 and not (AUDIO_CACHE / f"{rid}.json").exists():
            busy += 1   # コピーの途中かもしれない：次の回に解析する
            continue
        cache = AUDIO_CACHE / f"{rid}.json"
        if cache.exists() and index.get(rid, {}).get("sig") == f"{st.st_size}|{int(st.st_mtime)}":
            out.append((f, json.loads(cache.read_text(encoding="utf-8"))))
        else:
            todo.append((top, f, rid, cache, f"{st.st_size}|{int(st.st_mtime)}"))
    if quiet and not todo:   # 自動の見張り：新しい曲が無ければ何も言わない
        return out
    print(f"▶ 既存楽曲：{len(files)} 曲（解析済み {len(out)}・まだ {len(todo)}" + (f"・コピー中 {busy}" if busy else "") + "）"
          + "".join(f"\n   ・{d}" for d in dirs))
    if todo:
        try:
            import analyze_track
        except Exception as e:  # noqa: BLE001
            print(f"   － 解析の部品を読めませんでした（{e}）。ターミナルで  python3 -m pip install -r requirements.txt  を 1 回実行してください")
            todo = []
    run = todo if limit <= 0 else todo[:limit]
    for i, (top, f, rid, cache, sig) in enumerate(run, 1):
        title, artist = _who(f, top)
        print(f"【進み具合】{i}/{len(run)} 曲目：{artist}「{title}」を解析しています（{f.name}）", flush=True)
        try:
            m = _measure(analyze_track, f)
        except Exception as e:  # noqa: BLE001  1 曲読めなくても次へ
            print(f"   ✕ 解析できませんでした（{type(e).__name__}: {str(e)[:120]}）")
            index[rid] = {"sig": sig, "file": str(f), "error": str(e)[:200]}
            continue
        t = _tags(f)
        args = argparse.Namespace(title=title, artist=artist, language="en", source="manual", acquired_from=str(f.name),
                                  tags=[x.strip() for x in re.split(r"[;/,]", t.get("genre", "")) if x.strip()],
                                  year=int(t["year"]) if t.get("year") else None, web=[])
        sheet = analyze_track.skeleton(args, m, None)
        sheet["id"] = PREFIX + rid
        sheet["reference"]["source"] = "database"
        sheet["reference"]["from"] = "既存楽曲（音源の解析）"
        sheet["analyzed_by"] = "auto（既存楽曲の音源）"
        cache.write_text(json.dumps(sheet, ensure_ascii=False, indent=1), encoding="utf-8")
        index[rid] = {"sig": sig, "file": str(f), "at": datetime.now().isoformat(timespec="seconds")}
        out.append((f, sheet))
        NEW_AUDIO.append(f"{artist}「{title}」")
        print(f"   ○ {m['bpm']} BPM・{m['key']}・{m['duration_sec']} 秒・セクション {len(m['sections'])}")
    for rid in [k for k in index if k not in seen]:   # 既存楽曲から消えた曲は取り下げる
        (AUDIO_CACHE / f"{rid}.json").unlink(missing_ok=True)
        index.pop(rid)
    AUDIO_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    left = len(todo) - len(run)
    if left > 0:
        print(f"   まだ解析していない曲が {left} 曲あります（次の回に続けます。全部いま解析するときは --audio-limit 0）")
    return out


# ---------------------------------------------------------------------------
# 場所
def candidates() -> tuple[list[Path], str]:
    env = os.environ.get("REFERENCE_DB", "").strip()
    if env:
        p = Path(env).expanduser()
        return ([p], f".env の REFERENCE_DB（{p}）") if p.exists() else ([], f"REFERENCE_DB の場所が見つかりません（{p}。SSD をつないでください）")
    base, how = archive.root()
    if base is None:
        return [], how
    found: list[Path] = []
    own = {norm(x) for x in (archive.BOOK_DIR, archive.ARTIST_DIR, archive.KIT_DIR, archive.OLD_DIR)}
    for top in (base.parent, base):
        for c in _walk(top, 2):
            if norm(c.name) in own or any(c.is_relative_to(f) for f in found) or c == base:
                continue
            words = re.split(r"[\s_\-・.()（）]+", norm(c.stem if c.is_file() else c.name))
            hit = any(norm(h) in norm(c.name) for h in HINTS if h != "db") or "db" in words
            if hit or (c.is_file() and c.suffix.lower() in SQLITE_EXT):
                found.append(c)
    return found, f"SSD（{base.parent}）"


def _walk(top: Path, depth: int):
    try:
        kids = sorted(top.iterdir())
    except OSError:
        return
    for c in kids:
        if c.name.startswith((".", "$", "~$")) or c.name in ("System Volume Information",):
            continue
        yield c
        if c.is_dir() and depth > 1:
            yield from _walk(c, depth - 1)


def files_in(p: Path) -> list[Path]:
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*") if f.is_file() and not f.name.startswith((".", "~$")))


# ---------------------------------------------------------------------------
# 読み込み
def read_table_rows(f: Path) -> list[tuple[str, list[dict]]]:
    """(表の名前, 行の一覧)。行は {見出し: 値}"""
    ext = f.suffix.lower()
    if ext in (".csv", ".tsv"):
        for enc in ("utf-8-sig", "cp932"):
            try:
                text = f.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return []
        rows = list(csv.DictReader(text.splitlines(), delimiter="\t" if ext == ".tsv" else ","))
        return [(f.name, rows[:MAX_ROWS])]
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(f, read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header = None
            rows = []
            for r in it:
                if header is None:
                    if sum(1 for v in r if v not in (None, "")) >= 2:
                        header = [str(v).strip() if v is not None else "" for v in r]
                    continue
                if all(v in (None, "") for v in r):
                    continue
                rows.append({h: v for h, v in zip(header, r) if h})
                if len(rows) >= MAX_ROWS:
                    break
            if header:
                out.append((f"{f.name}／{ws.title}", rows))
        return out
    if ext in SQLITE_EXT:
        out = []
        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        try:
            for (t,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
                cur = con.execute(f'select * from "{t}" limit {MAX_ROWS}')
                cols = [d[0] for d in cur.description]
                out.append((f"{f.name}／{t}", [dict(zip(cols, r)) for r in cur.fetchall()]))
        finally:
            con.close()
        return out
    if ext == ".json":
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), None)
        if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
            return [(f.name, data[:MAX_ROWS])]
    return []


def split_words(v: object) -> list[str]:
    return [w.strip() for w in re.split(r"[,、，/／|・;；\n]+", str(v or "")) if w.strip()]


def row_to_sheet(row: dict, src: str) -> dict | None:
    """表の 1 行を、解析シートの形（分かる欄だけ）にする"""
    got: dict[str, list[str]] = {}
    other: dict[str, str] = {}
    for h, v in row.items():
        if v in (None, "") or str(v).strip() == "":
            continue
        f = field_of(h)
        if f == "skip":
            continue
        if f is None:
            other[str(h)] = str(v).strip()[:300]
        else:
            got.setdefault(f, []).append(str(v).strip())
    title = (got.get("title") or [""])[0]
    artist = (got.get("artist") or [""])[0]
    if not title and not artist:
        return None
    j = lambda k: " / ".join(got.get(k, []))[:600]
    tags = [w for k in ("genre", "tags") for v in got.get(k, []) for w in split_words(v)]
    bpm = None
    m = re.search(r"\d{2,3}(\.\d+)?", j("bpm"))
    if m:
        bpm = round(float(m.group()))
    vtext = norm(j("vocal"))
    sex = ("female" if re.search(r"女|female|woman", vtext) else "male" if re.search(r"男|male|man", vtext)
           else "mixed" if re.search(r"混|duet|デュエット|mixed", vtext) else None)
    year = None
    m = re.search(r"(19|20)\d{2}", j("year"))
    if m:
        year = int(m.group())
    sheet = {
        "id": PREFIX + hashlib.sha1(f"{norm(title)}|{norm(artist)}".encode()).hexdigest()[:10],
        "reference": {"title": title or "（曲名なし）", "artist_name": artist or "?", "release_year": year,
                      "language": j("language") or None, "source": "database", "tags": tags},
        "_from": src,
    }
    slots: set[str] = set()
    if got.get("theme"):
        sheet["lyrics"] = {"theme": j("theme")[:300]}
        sheet["worldview"] = {"notes": j("theme")[:300]}
        slots |= {"lyrics", "worldview"}
    if bpm or got.get("genre"):
        sheet["groove"] = {"bpm": bpm, "notes": " / ".join(x for x in (j("genre"), j("tags")) if x)}
        slots.add("groove")
    if got.get("key"):
        sheet["harmony"] = {"key": j("key")}
        slots.add("harmony")
    if got.get("vocal"):
        sheet["vocal"] = {"sex": sex, "notes": j("vocal")}
        slots |= {"vocal_main", "vocal_sub1", "vocal_sub2"}
    if got.get("instruments"):
        sheet["instruments"] = {"notes": j("instruments")}
        slots.add("instruments")
    if got.get("structure"):
        sheet["structure"] = {"notes": j("structure")}
        slots.add("structure")
    memo = j("memo")
    if memo or other:
        sheet["performance"] = {"notes": memo, "その他": other} if other else {"notes": memo}
        slots.add("performance")
        if memo:   # 「サビ前の溜め」のような、フレーズの作り方のメモとしても使う
            sheet["phrases"] = {"notes": memo}
            slots.add("phrase")
        if "worldview" not in sheet:
            sheet["worldview"] = {"notes": memo or json.dumps(other, ensure_ascii=False)[:600]}
            slots.add("worldview")
    if bpm:
        sheet.setdefault("groove", {})["bpm"] = bpm
    if got.get("for_artist"):
        sheet["for_artists"] = j("for_artist")[:200]   # assigned_to が読む（組の名前）
    sheet["usable_slots"] = sorted(slots) if slots else ["worldview"]
    sheet["_memo"] = memo
    return sheet


def scan(paths: list[Path]) -> dict:
    res = {"sheets": [], "tables": [], "texts": [], "audio": 0, "skipped": [], "analysis": []}
    for p in paths:
        for f in files_in(p):
            ext = f.suffix.lower()
            try:
                if ext == ".json":
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "reference" in data:
                        data.setdefault("id", PREFIX + f.stem)
                        if not str(data["id"]).startswith(PREFIX):
                            data["id"] = PREFIX + str(data["id"])
                        data.setdefault("reference", {}).setdefault("source", "database")
                        res["analysis"].append((f, data))
                        continue
                if ext in TABLE_EXT | SQLITE_EXT | {".json"}:
                    tabs = read_table_rows(f)
                    if not tabs:
                        res["skipped"].append((f, "表として読めない"))
                    for name, rows in tabs:
                        heads = list(rows[0].keys()) if rows else []
                        sheets = [s for s in (row_to_sheet(r, name) for r in rows) if s]
                        res["tables"].append({"name": name, "rows": len(rows), "songs": len(sheets),
                                              "map": {str(h): LABEL.get(field_of(h) or "other", field_of(h)) for h in heads}})
                        res["sheets"] += sheets
                elif ext in TEXT_EXT:
                    res["texts"].append((f, f.read_text(encoding="utf-8", errors="ignore")[:4000]))
                elif ext in AUDIO_EXT:
                    res["audio"] += 1
                elif ext == ".numbers":
                    res["skipped"].append((f, "Numbers の形式は読めません。Numbers で「書き出す → Excel」してください"))
                else:
                    res["skipped"].append((f, "読まない種類"))
            except Exception as e:  # noqa: BLE001  1 つ読めなくても残りは続ける
                res["skipped"].append((f, f"読めない（{type(e).__name__}: {e}）"))
    return res


# ---------------------------------------------------------------------------
# 傾向の要約（実名は入れない）
def real_names(res: dict) -> list[str]:
    all_sheets = res["sheets"] + [d for _, d in res["analysis"]]
    return sorted({str(s["reference"].get(k, "")) for s in all_sheets for k in ("artist_name", "title")} - {"", "?", "（曲名なし）"},
                  key=len, reverse=True)


def scrubber(names: list[str]):
    pats = [re.compile(re.escape(n), re.I) for n in names if len(n) >= 2]

    def scrub(v):
        if isinstance(v, str):
            for p in pats:
                v = p.sub("（実名）", v)
            return v
        if isinstance(v, list):
            return [scrub(x) for x in v]
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in v.items()}
        return v
    return scrub


def scrub_sheet(sheet: dict, scrub) -> dict:
    """曲名・アーティスト名の欄以外の文章から、データベースに出てくる実名を伏せる（Claude に渡る部分）"""
    return {k: (v if k in ("id", "reference", "_from", "usable_slots", "weight") else scrub(v)) for k, v in sheet.items()}


def digest(res: dict) -> dict:
    all_sheets = res["sheets"] + [d for _, d in res["analysis"]]
    names = real_names(res)
    scrub = scrubber(names)
    uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))

    tags = Counter(norm(t) for s in all_sheets for t in s["reference"].get("tags", []) if t)
    bpms = sorted(b for s in all_sheets if isinstance(b := (s.get("groove") or {}).get("bpm"), (int, float)))
    vocal = Counter((s.get("vocal") or {}).get("sex") or "不明" for s in all_sheets if s.get("vocal"))
    themes = uniq(scrub(s["lyrics"]["theme"]) for s in all_sheets if isinstance(s.get("lyrics"), dict) and s["lyrics"].get("theme"))
    memos = uniq(scrub(s.get("_memo") or "") for s in res["sheets"])
    notes = uniq(scrub(t)[:1500] for _, t in res["texts"])
    return {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "songs": len(all_sheets),
        "top_tags": [t for t, _ in tags.most_common(30) if t not in {norm(n) for n in names}],
        "bpm": {"min": bpms[0], "median": bpms[len(bpms) // 2], "max": bpms[-1]} if bpms else None,
        "vocal": dict(vocal),
        "themes": themes[:25],
        "owner_notes": (memos[:25] + notes[:5]),
        "real_names": names,   # write_brief.py が「出力に入れてはいけない語」として使う（Claude には渡さない）
    }


def write_outputs(res: dict, where: str, paths: list[Path]) -> tuple[int, dict]:
    REF_DIR.mkdir(parents=True, exist_ok=True)
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    keep = set()
    scrub = scrubber(real_names(res))
    for s in res["sheets"] + [d for _, d in res["analysis"]]:
        s = scrub_sheet({k: v for k, v in s.items() if k != "_memo"}, scrub)
        p = REF_DIR / f"{s['id']}.json"
        body = json.dumps(s, ensure_ascii=False, indent=1)
        if not p.exists() or p.read_text(encoding="utf-8") != body:
            p.write_text(body, encoding="utf-8")
        keep.add(p.name)
    for old in REF_DIR.glob(f"{PREFIX}*.json"):   # データベースから消えた曲は取り下げる
        if old.name not in keep:
            old.unlink()
    d = digest(res)
    (DIGEST_DIR / "digest.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# 参考データベースの傾向（{d['updated']}・{d['songs']} 曲）", "",
          f"- よく出るタグ・ジャンル：{'、'.join(d['top_tags'][:20]) or '—'}",
          f"- BPM：{d['bpm']['min']}〜{d['bpm']['max']}（真ん中 {d['bpm']['median']}）" if d["bpm"] else "- BPM：—",
          f"- ボーカル：{'、'.join(f'{k} {v}' for k, v in d['vocal'].items()) or '—'}", "", "## テーマ", ""]
    md += [f"- {t}" for t in d["themes"]] or ["- —"]
    md += ["", "## オーナーのメモ", ""] + ([f"- {t}" for t in d["owner_notes"]] or ["- —"])
    (DIGEST_DIR / "digest.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (DIGEST_DIR / "summary.md").write_text(summary(res, where, paths), encoding="utf-8")
    return len(keep), d


def summary(res: dict, where: str, paths: list[Path]) -> str:
    out = [f"# 参考データベースの読み取り結果（{datetime.now():%Y-%m-%d %H:%M}）", "", f"- 探した場所：{where}"]
    out += [f"- 見つかったもの：`{p}`" for p in paths]
    out += ["", f"- 解析シート（そのまま使う）：{len(res['analysis'])} 件",
            f"- 表から読んだ曲：{len(res['sheets'])} 曲",
            f"- 文章（制作メモ）：{len(res['texts'])} 件", f"- 音源：{res['audio']} 件（analyze_track.py で解析シートにできます）", ""]
    cover = Counter(sl for sh in res["sheets"] for sl in sh.get("usable_slots", []))
    slot_ja = {"lyrics": "歌詞（テーマ）", "worldview": "世界観", "instruments": "楽器", "performance": "演奏・歌い方", "structure": "構成",
               "harmony": "和声（キー）", "groove": "リズム（BPM・ジャンル）", "phrase": "フレーズ",
               "vocal_main": "ボーカル"}
    out += ["## 枠ごとに使える曲の数（1 曲は 1 つの枠にしか使わないので、どの枠も 2 曲以上あると安心）", "",
            "| 枠 | 曲数 | 増やすなら |", "|---|---|---|"]
    need = {"lyrics": "テーマ", "worldview": "テーマかメモ", "instruments": "楽器", "performance": "メモ", "structure": "構成",
            "harmony": "キー", "groove": "BPM かジャンル", "phrase": "メモ", "vocal_main": "ボーカル"}
    out += [f"| {ja} | {cover.get(k, 0)} | {'「' + need[k] + '」の列に書き足す' if cover.get(k, 0) < 2 else '—'} |" for k, ja in slot_ja.items()]
    out += [""]
    for t in res["tables"]:
        out += [f"## 表：{t['name']}（{t['rows']} 行 → {t['songs']} 曲）", "", "| 見出し | 読み方 |", "|---|---|"]
        out += [f"| {h} | {m} |" for h, m in t["map"].items()] + [""]
    if res["skipped"]:
        out += ["## 読まなかったもの", ""] + [f"- `{f.name}`：{why}" for f, why in res["skipped"][:40]] + [""]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 組ごとの近い参考曲（既存の組がこれからの作曲で使う。select_references.py・write_brief.py・suno_design.py が読む）
ARTIST_DIR = DIGEST_DIR / "artists"
# データベースの日本語の言葉 → 設定書の英語の言葉（近さを測るためだけ。Suno には英語のタグしか入れない）
JA_EN = {
    "ポップ": "pop", "ロック": "rock", "フォーク": "folk", "アコースティック": "acoustic", "アコギ": "acoustic guitar",
    "シティポップ": "city pop", "シティ・ポップ": "city pop", "ハウス": "house", "ディープハウス": "deep house", "ダンス": "dance",
    "エレクトロ": "electro", "エレクトロニカ": "electronica", "シンセ": "synth", "シンセポップ": "synth-pop", "シンセウェイヴ": "synthwave",
    "シンセウェーブ": "synthwave", "ローファイ": "lo-fi", "インディー": "indie", "インディ": "indie", "ファンク": "funk", "ソウル": "soul",
    "ジャズ": "jazz", "ディスコ": "disco", "アンビエント": "ambient", "ダウンテンポ": "downtempo", "チル": "chill", "バラード": "ballad",
    "80年代": "80s", "80's": "80s", "90年代": "90s", "レトロ": "retro", "夜": "night", "朝": "morning", "ドライブ": "drive",
    "雨": "rain", "海": "ocean", "夏": "summer", "冬": "winter", "ギター": "guitar", "ベース": "bass", "ピアノ": "piano",
    "ドラム": "drums", "ブラシ": "brushed", "ストリングス": "strings", "チェロ": "cello", "オルガン": "organ", "サックス": "saxophone",
    "ハーモニー": "harmony", "ハモり": "harmony", "ハモリ": "harmony", "デュエット": "duet", "男女": "male-female",
    "ウィスパー": "whisper", "ささやき": "whisper", "ファルセット": "falsetto", "裏声": "falsetto", "クルーナー": "crooner",
    "ボーカルチョップ": "vocal chops", "フィールド録音": "field recording", "環境音": "field recording", "明るい": "bright",
    "切ない": "bittersweet", "温かい": "warm", "あたたかい": "warm", "爽やか": "fresh", "メロウ": "mellow", "グルーヴ": "groove",
}
STOP = {"and", "with", "the", "of", "a", "in", "on", "for", "to", "style", "music", "song", "vocals", "vocal", "sound", "feel"}
WIDE = {"pop", "rock", "dance", "indie", "night", "warm", "bright", "drive", "guitar", "bass", "drums", "synth"}  # 広すぎる言葉は半分に数える


def words_of(*texts: object) -> set[str]:
    """文章 → 比べるための言葉の集まり（英語は単語と 2 語のつながり、日本語は上の対応表で英語に）"""
    out: set[str] = set()
    for t in texts:
        s = norm(t if isinstance(t, str) else json.dumps(t, ensure_ascii=False) if t else "")
        for ja, en in JA_EN.items():
            if norm(ja) in s:
                out |= set(en.split()) | {en}
        toks = [w for w in re.findall(r"[a-z0-9][a-z0-9'&+\-]*", s) if w not in STOP and len(w) >= 2]
        out |= set(toks)
        out |= {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    return out


def artist_words(artist: dict) -> set[str]:
    snd = artist.get("sound") or {}
    return words_of(" , ".join(snd.get("genres") or []), " , ".join(snd.get("dna_tags") or []))


def _sheet_text(s: dict) -> list:
    r = s.get("reference") or {}
    return [r.get("tags"), (s.get("groove") or {}).get("notes"), (s.get("instruments") or {}).get("notes"),
            (s.get("vocal") or {}).get("notes"), (s.get("performance") or {}), (s.get("worldview") or {}).get("notes"),
            (s.get("lyrics") or {}).get("theme")]


def _bpm_fit(bpm, lo, hi) -> float | None:
    if not isinstance(bpm, (int, float)) or not lo or not hi:
        return None
    best = min(abs(b - min(max(b, lo), hi)) for b in (bpm, bpm / 2, bpm * 2))   # 倍・半分のノリも同じテンポ感とみる
    return 1.0 if best <= 5 else 0.5 if best <= 15 else 0.0


def _vocal_fit(sex, artist: dict) -> float | None:
    want = (artist.get("vocal") or {}).get("sex")
    if not sex or not want:
        return None
    if want in ("none", "mixed") or sex == "mixed":
        return 1.0
    return 1.0 if sex == want else 0.0


def assigned_to(s: dict, artist: dict) -> bool:
    """オーナーがデータベースに組の名前を書いた曲（「使う組」の欄・メモなど）"""
    names = {norm(artist.get("name")), norm(artist.get("slug")), norm(str(artist.get("slug", "")).replace("_", " "))} - {""}
    blob = norm(json.dumps({k: v for k, v in s.items() if k != "id"}, ensure_ascii=False))
    return any(len(n) >= 4 and n in blob for n in names)


def affinity(s: dict, artist: dict, aw: set[str] | None = None) -> tuple[float, list[str]]:
    """参考曲と組の近さ（0〜2。1 を超えるのはオーナーが組を書いた曲）と、近いと見た理由"""
    aw = artist_words(artist) if aw is None else aw
    common = aw & words_of(*_sheet_text(s))
    singles = {w for w in common if " " not in w}
    hit = sum(0.5 if w in WIDE else 1.0 for w in singles) + sum(1.0 for w in common if " " in w)
    tag = min(1.0, hit / 3)
    snd = artist.get("sound") or {}
    bpm = _bpm_fit((s.get("groove") or {}).get("bpm"), snd.get("bpm_min"), snd.get("bpm_max"))
    voc = _vocal_fit((s.get("vocal") or {}).get("sex"), artist)
    parts = [(tag, 0.6)] + [(v, w) for v, w in ((bpm, 0.25), (voc, 0.15)) if v is not None]
    score = sum(v * w for v, w in parts) / sum(w for _, w in parts)
    if voc == 0.0:
        score *= 0.5            # 声の性別が逆の曲は、声以外の参考としてだけ少し残す
    why = []
    if singles:
        why.append("言葉：" + "・".join(sorted(singles)[:6]))
    if bpm:
        why.append("テンポが近い")
    if assigned_to(s, artist):
        score += 1.0
        why.insert(0, "オーナーがこの組に指定")
    if tag < 0.34 and not assigned_to(s, artist):
        score = min(score, 0.3)  # 言葉が 1 つも本当に重ならない曲は、テンポと声が合っても近いとみない
    return round(score, 3), why


def _style_hints(close: list[tuple[float, dict]], artist: dict, names: set[str]) -> list[str]:
    """近い参考曲に 2 曲以上出てくる英語のタグで、設定書にまだ無く、禁止にも当たらないもの（Suno のスタイル欄の後ろに足す候補）"""
    snd = artist.get("sound") or {}
    have = norm(" , ".join((snd.get("genres") or []) + (snd.get("dna_tags") or [])))
    bad = norm(" , ".join(snd.get("forbidden") or []))
    cnt: Counter = Counter()
    for sc, s in close:
        for t in dict.fromkeys(norm(x) for x in (s.get("reference") or {}).get("tags", [])):
            if re.fullmatch(r"[a-z0-9][a-z0-9 '&+\-]{1,38}", t) and t not in names and t not in have and t not in bad:
                cnt[t] += 2 if sc > 1 else 1
    return [t for t, c in cnt.most_common() if c >= 2][:4]


def build_artist_digests(quiet: bool = False) -> list[str]:
    """取り込んだ参考曲（out/references/db-*.json。実名以外の文章は伏せ済み）から、組ごとの近い参考曲と傾向を作る。
    SSD が無いときも前回の取り込みから作り直す（設定書が変わっても追いつく）"""
    sheets = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(REF_DIR.glob(f"{PREFIX}*.json"))] if REF_DIR.exists() else []
    dg = json.loads((DIGEST_DIR / "digest.json").read_text(encoding="utf-8")) if (DIGEST_DIR / "digest.json").exists() else {}
    real = list(dg.get("real_names") or [])
    names, scrub = {norm(n) for n in real}, scrubber(real)
    if not sheets:
        return []
    from _common import all_artists
    ARTIST_DIR.mkdir(parents=True, exist_ok=True)
    md = [f"# 参考データベース：組ごとの近い参考曲（{datetime.now():%Y-%m-%d %H:%M}・データベース {len(sheets)} 曲）", "",
          "近さ＝ジャンル・タグ・楽器の言葉の重なり（6 割）・テンポ（2.5 割）・声の性別（1.5 割）。"
          "データベースの「使う組」の欄やメモに組の名前を書いた曲は、いちばん優先します。", ""]
    lines = []
    for a in all_artists():
        aw = artist_words(a)
        scored = sorted(((affinity(s, a, aw), s) for s in sheets), key=lambda x: -x[0][0])
        close = [(sc, why, s) for (sc, why), s in scored if sc >= 0.4][:15]
        uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))
        pick = [(sc, s) for sc, _, s in close if sc >= 0.6] or [(sc, s) for sc, _, s in close]   # 傾向は特に近い曲から
        tags = Counter(norm(t) for _, s in pick for t in (s.get("reference") or {}).get("tags", []) if norm(t) not in names)
        bpms = sorted(b for _, s in pick if isinstance(b := (s.get("groove") or {}).get("bpm"), (int, float)))
        d = {
            "slug": a["slug"], "name": a.get("name"), "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "close_count": len(close), "owner_assigned": sum(1 for sc, _, _ in close if sc > 1),
            "close": {s["id"]: sc for sc, _, s in close},   # select_references.py が選ぶ重みに使う（id だけ。実名は入れない）
            "top_tags": [t for t, _ in tags.most_common(15)],
            "bpm": {"min": bpms[0], "median": bpms[len(bpms) // 2], "max": bpms[-1]} if bpms else None,
            "themes": scrub(uniq((s.get("lyrics") or {}).get("theme") for _, s in pick))[:8],
            "instruments": scrub(uniq((s.get("instruments") or {}).get("notes") for _, s in pick))[:8],
            "vocal_notes": scrub(uniq((s.get("vocal") or {}).get("notes") for _, s in pick))[:6],
            "owner_notes": scrub(uniq((s.get("performance") or {}).get("notes") for _, s in pick))[:10],
            "style_hints": _style_hints(pick, a, names),
        }
        (ARTIST_DIR / f"{a['slug']}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        md += [f"## {a.get('name')}（{a['slug']}）：近い参考曲 {len(close)} 曲"
               + (f"（うちオーナーの指定 {d['owner_assigned']} 曲）" if d["owner_assigned"] else ""), ""]
        md += [f"- {s['reference'].get('title')}／{s['reference'].get('artist_name')}　近さ {sc:.2f}　{'・'.join(why)}" for sc, why, s in close] or ["- —（近い曲がありません。データベースに足すと次の制作から効きます）"]
        if d["style_hints"]:
            md += ["", f"Suno のスタイル欄の後ろに足す言葉：{'、'.join(d['style_hints'])}"]
        md.append("")
        lines.append(f"{a.get('name')} {len(close)} 曲")
    (DIGEST_DIR / "artists.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    if not quiet:
        print("   組ごとの近い参考曲：" + "・".join(lines) + "（out/reference_db/artists.md）")
    return lines


def artist_digest(slug: str) -> dict:
    """組ごとの近い参考曲の傾向（無ければ空）"""
    p = ARTIST_DIR / f"{slug}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def setup(path: str) -> None:
    p = Path(path).expanduser()
    if not p.exists():
        sys.exit(f"[エラー] {p} が見つかりません。SSD がつながっているか確かめてください")
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [l for l in lines if not l.startswith("REFERENCE_DB=")] + [f"REFERENCE_DB={p}"]
    env.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    print(f"▶ 参考データベースの場所を .env に書きました：{p}")


def main() -> None:
    ap = argparse.ArgumentParser(description="SSD のデータベースを読み、楽曲制作の参考にする")
    ap.add_argument("--show", action="store_true", help="見つかったデータベースの中身と読み方を表示するだけ（取り込まない）")
    ap.add_argument("--setup", metavar="PATH", help="データベースの場所を .env に書く")
    ap.add_argument("--quiet", action="store_true", help="SSD が無いときは何も言わない（自動実行用）")
    ap.add_argument("--artists", action="store_true", help="組ごとの近い参考曲だけ作り直す")
    ap.add_argument("--audio-limit", type=int, default=20, help="「既存楽曲」の音源を 1 回に何曲まで解析するか（0＝全部）")
    ap.add_argument("--watch", action="store_true",
                    help="自動の見張り用：「既存楽曲」に新しく入った曲だけ解析し、あったときだけ参考曲に登録して Mac に知らせる（決定 78）")
    args = ap.parse_args()
    if args.watch:
        args.quiet, args.audio_limit = True, 0
    if args.artists:
        if not build_artist_digests():
            print("－ まだ参考曲を取り込んでいません。SSD をつないで python3 scripts/reference_db.py を実行してください")
        return
    if args.setup:
        setup(args.setup)
        return
    paths, where = candidates()
    audio = [] if args.show else analyze_audio(args.audio_limit, quiet=args.quiet)
    if args.watch and not NEW_AUDIO:
        return   # 新しい曲が無い：登録し直さない（見張りは 30 分ごとにも動くので、ふだんは何もしない）
    if not paths and audio:   # データベースのフォルダは無いが、既存楽曲の解析はある
        res = {"sheets": [], "tables": [], "texts": [], "audio": 0, "skipped": [], "analysis": audio}
        n, d = write_outputs(res, "SSD の既存楽曲", [])
        print(f"   取り込み：参考曲 {n} 曲（既存楽曲の解析。out/references/{PREFIX}aud-*.json）")
        build_artist_digests()
        _tell_new()
        return
    if not paths:
        cached = len(list(REF_DIR.glob(f"{PREFIX}*.json"))) if REF_DIR.exists() else 0
        if not args.quiet or cached == 0:
            print(f"－ 参考データベースは読みませんでした：{where}")
            if cached:
                print(f"   前回取り込んだ {cached} 曲と傾向（out/reference_db/digest.md）をそのまま使います")
            elif "SSD" not in where:
                print("   SSD の一番上か Postdog Records の中に「データベース」という名前のフォルダを置くか、"
                      "--setup で場所を指定してください")
        build_artist_digests(quiet=args.quiet)
        return
    print(f"▶ 参考データベース：{where}")
    for p in paths:
        print(f"   ・{p}")
    res = scan(paths)
    res["analysis"] += audio   # 既存楽曲の音源の解析も、参考曲として一緒に取り込む（決定 76）
    if args.show:
        print(summary(res, where, paths))
        return
    n, d = write_outputs(res, where, paths)
    print(f"   取り込み：参考曲 {n} 曲（out/references/{PREFIX}*.json）・傾向の要約（out/reference_db/digest.md）")
    if d["top_tags"]:
        print(f"   よく出るタグ：{'、'.join(d['top_tags'][:10])}")
    if res["skipped"]:
        print(f"   読まなかったもの {len(res['skipped'])} 件（out/reference_db/summary.md）")
    build_artist_digests()
    _tell_new()


def _tell_new() -> None:
    """新しく解析して登録した曲を Mac に知らせる"""
    if NEW_AUDIO:
        from _common import notify
        print(f"【結果】既存楽曲の新しい曲 {len(NEW_AUDIO)} 曲を解析して、参考曲に登録しました：" + "・".join(NEW_AUDIO[:10]))
        notify("参考曲に登録しました", f"既存楽曲の新しい曲 {len(NEW_AUDIO)} 曲を解析しました：" + "・".join(NEW_AUDIO[:3]))


if __name__ == "__main__":
    main()

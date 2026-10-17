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
  ・音源（.mp3・.wav など）… 数だけ数える（analyze_track.py で解析シートにできる）
  ・Numbers（.numbers）は読めないので、Numbers で「書き出す → Excel」してから置く

出すもの（Mac の中。SSD が無いときも、前回取り込んだものを使い続ける）：
  ・out/references/db-<番号>.json … 参考曲の解析シート（select_references.py・write_brief.py が読む）
  ・out/reference_db/digest.json・digest.md … データベース全体の傾向（タグ・ジャンル・BPM・テーマ・メモ）。
      write_brief.py が Claude に渡す。実在のアーティスト名・曲名は入れない
  ・out/reference_db/summary.md … 何が見つかり、どう読んだかの一覧（--show でも表示）

使い方：
  python3 scripts/reference_db.py --show                # 見つかったデータベースの中身と読み方を表示するだけ
  python3 scripts/reference_db.py                       # 取り込む（run_week.py のブリーフ作りの前にも自動で動く）
  python3 scripts/reference_db.py --setup "/Volumes/SP DS72/データベース"   # 場所を .env に書く
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
         "skip": "使わない（URL・番号・作者名・歌詞の本文）"}
# 部分一致で見る順（キーワード→キー、曲調→曲名 の取り違えを防ぐ）
ORDER = ["skip", "artist", "tags", "theme", "memo", "title", "bpm", "key", "genre", "vocal", "instruments", "structure", "language", "year"]


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
    args = ap.parse_args()
    if args.setup:
        setup(args.setup)
        return
    paths, where = candidates()
    if not paths:
        cached = len(list(REF_DIR.glob(f"{PREFIX}*.json"))) if REF_DIR.exists() else 0
        if not args.quiet or cached == 0:
            print(f"－ 参考データベースは読みませんでした：{where}")
            if cached:
                print(f"   前回取り込んだ {cached} 曲と傾向（out/reference_db/digest.md）をそのまま使います")
            elif "SSD" not in where:
                print("   SSD の一番上か Postdog Records の中に「データベース」という名前のフォルダを置くか、"
                      "--setup で場所を指定してください")
        return
    print(f"▶ 参考データベース：{where}")
    for p in paths:
        print(f"   ・{p}")
    res = scan(paths)
    if args.show:
        print(summary(res, where, paths))
        return
    n, d = write_outputs(res, where, paths)
    print(f"   取り込み：参考曲 {n} 曲（out/references/{PREFIX}*.json）・傾向の要約（out/reference_db/digest.md）")
    if d["top_tags"]:
        print(f"   よく出るタグ：{'、'.join(d['top_tags'][:10])}")
    if res["skipped"]:
        print(f"   読まなかったもの {len(res['skipped'])} 件（out/reference_db/summary.md）")


if __name__ == "__main__":
    main()

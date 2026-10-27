#!/usr/bin/env python3
"""
楽曲管理シート（SSD の Postdog Records/01_管理表/楽曲管理.xlsx）を作る・書き直す

1 曲 1 行で、曲名・アーティスト名・登録までのフェーズ・歌詞・Suno の指示・保管場所・配信の記録をまとめて見られる。
  ・機械が埋める欄：曲 ID・アーティスト・曲名・曲名の別案・フェーズ・次にやること・制作週・BPM・ジャンル・言語・
                    Suno の指示・歌詞・選んだテイク・保管場所・更新日
  ・オーナーが書く欄：配信予定日・ISRC・UPC・配信 URL・メモ（書き直しても消さない）
  ・フェーズは、機械が分かる段階とオーナーが書いた段階の「先に進んでいるほう」を残す
    （例：DistroKid に登録したら、オーナーが「7 DistroKid 登録済み」に変える）

元になる情報：
  ・デビュー曲 … debut_kit/<日付>/kit.json（曲 ID は PDR-001 から）
  ・毎週の曲   … out/briefs・out/takes・out/masters・out/visuals・out/distrokid（試験用の週 2000-xx-xx は除く）

使い方：
  python3 scripts/song_book.py                 # SSD の楽曲管理.xlsx を書き直す（SSD が無ければ飛ばす）
  python3 scripts/song_book.py --out 楽曲管理.xlsx   # 好きな場所に書き出す（試すとき）
archive_sync.py（毎週の仕上げの最後・毎日 3:30）からも自動で呼ばれる。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_artist, load_dotenv, read_json  # noqa: E402

load_dotenv()
import _archive as archive  # noqa: E402
import _songs  # noqa: E402  曲の一覧と公開の進み具合（publish.py）

BOOK_NAME = "楽曲管理.xlsx"
SHEET = "楽曲"
PHASES = ["1 企画", "2 Suno で作曲", "3 テイク選び", "4 音量調整", "5 ジャケット", "6 登録の準備", "7 DistroKid 登録済み", "8 配信中"]
NEXT = {
    "1 企画": "Claude がブリーフを仕上げる（自動）",
    "2 Suno で作曲": "オーナー：ダッシュボードの「Suno に入力する」で欄が埋まる → 確かめて Create → いいねした 1 本を SSD の「楽曲情報」へダウンロード",
    "3 テイク選び": "自動（いいねしてダウンロードした 1 本を取り込む）",
    "4 音量調整": "自動（-14 LUFS / -1 dBTP）",
    "5 ジャケット": "自動で候補 → 最初の 5 回はオーナーが選ぶ",
    "6 登録の準備": "ダッシュボードの「DistroKid に入力する」で下書きが入る → オーナーが登録ボタンを押す → 案内の「登録した」",
    "7 DistroKid 登録済み": "自動（毎日 Spotify を確かめ、配信が始まったら ISRC・UPC・配信 URL を入れて 8 にする）",
    "8 配信中": "—",
}
PHASE_COLOR = {"1": "EDEDED", "2": "FCE4D6", "3": "FFF2CC", "4": "FFF2CC", "5": "FFF2CC", "6": "DDEBF7", "7": "E2EFDA", "8": "C6E0B4"}
COLUMNS = [  # (見出し, 幅, 書く人)
    ("曲 ID", 18, "機械"), ("アーティスト", 18, "機械"), ("曲名", 22, "機械"), ("曲名の別案", 26, "機械"),
    ("フェーズ", 18, "両方"), ("次にやること", 34, "機械"), ("制作週", 12, "機械"), ("配信予定日", 12, "オーナー"),
    ("BPM", 6, "機械"), ("ジャンル", 18, "機械"), ("言語", 10, "機械"), ("Suno の指示（スタイル）", 48, "機械"),
    ("歌詞", 60, "機械"), ("選んだテイク", 16, "機械"), ("保管場所（SSD）", 36, "機械"),
    ("ISRC", 16, "オーナー"), ("UPC", 16, "オーナー"), ("配信 URL", 30, "オーナー"), ("メモ", 30, "オーナー"), ("更新日", 11, "機械"),
]
OWNER = [h for h, _, who in COLUMNS if who == "オーナー"]
AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".aif", ".aiff"}


def _join(v) -> str:
    return " / ".join(map(str, v)) if isinstance(v, (list, tuple)) else ("" if v is None else str(v))


STATUS_PHASE = {"": PHASES[1], "imported": PHASES[3], "waiting_ffmpeg": PHASES[3], "waiting_cover": PHASES[4],
                "ready": PHASES[5], "registered": PHASES[6], "live": PHASES[7]}


def release_info(song: dict) -> dict:
    """publish.py の記録（取り込み・登録・配信）から、シートに入れる値"""
    st = _songs.state(song)
    out = {}
    if st.get("take"):
        out["選んだテイク"] = st["take"]
    if st.get("release_date") or st.get("released"):
        out["配信予定日"] = st.get("released") or st["release_date"]
    for k, h in (("isrc", "ISRC"), ("upc", "UPC"), ("url", "配信 URL")):
        if st.get(k):
            out[h] = st[k]
    if (OUT / "masters" / song["key"] / "master.wav").exists():
        out["保管場所（SSD）"] = f"{archive.ARTIST_DIR}/{song['artist']}/{archive.SUBS['music']}/{_songs.folder_name(song)}/"
    return out


def debut_rows() -> list[dict]:
    rows = []
    for song in _songs.debut_songs():
        st = _songs.state(song)
        rows.append({
            "曲 ID": song["id"], "アーティスト": song["artist"], "曲名": song["title"],
            "曲名の別案": _join(song["alts"]), "フェーズ": STATUS_PHASE.get(st.get("status", ""), PHASES[1]),
            "制作週": song["week"], "BPM": next((x.get("bpm", "") for x in read_json(song["kit"] / "kit.json").get("songs", [])
                                              if x.get("slug") == song["slug"]), ""),
            "ジャンル": _join(song["genre"]), "言語": song["language"],
            "Suno の指示（スタイル）": song["style"], "歌詞": song["lyrics"], **release_info(song),
        })
    return rows


def weekly_rows() -> list[dict]:
    rows = []
    for p in sorted((OUT / "briefs").glob("*.json")):
        if p.name.startswith("2000-") or p.name.endswith((".prompt.json",)) or "_" not in p.stem:
            continue
        b = read_json(p)
        if "artist_slug" not in b or "week_start" not in b:
            continue
        a = load_artist(b["artist_slug"], b.get("label_slug"), missing_ok=True)
        if a is None:
            continue
        week = b["week_start"]
        takes = OUT / "takes" / p.stem
        sel = read_json(takes / "selection.json") if (takes / "selection.json").exists() else {}
        chosen = (sel.get("decision") or {}).get("selected") or ""
        cover = OUT / "visuals" / b["artist_slug"] / "cover" / week / "cover_final.jpg"
        meta_p = OUT / "distrokid" / week / f"{b['artist_slug']}.metadata.json"
        meta = read_json(meta_p) if meta_p.exists() else {}
        has_audio = takes.is_dir() and any(f.suffix.lower() in AUDIO for f in takes.iterdir())
        if not b.get("suno_style_prompt"):
            phase = PHASES[0]
        elif not has_audio:
            phase = PHASES[1]
        elif not chosen:
            phase = PHASES[2]
        elif not sel.get("master", {}).get("ok"):
            phase = PHASES[3]
        elif not cover.exists():
            phase = PHASES[4]
        else:
            phase = {"uploaded": PHASES[6], "live": PHASES[7]}.get(meta.get("status"), PHASES[5])
        lyr = p.with_suffix(".lyrics_final.txt")
        lyrics = lyr.read_text(encoding="utf-8") if lyr.exists() else _join(b.get("suno_lyrics"))
        name = a.get("name") or b["artist_slug"]
        titles = b.get("title_candidates") or []
        song = _songs.by_key(p.stem)
        st = _songs.state(song) if song else {}
        if st.get("status") in ("registered", "live"):
            phase = STATUS_PHASE[st["status"]]
        stored = (f"{archive.ARTIST_DIR}/{name}/{archive.SUBS['music']}/{_songs.folder_name(song)}/"
                  if song and (OUT / "masters" / p.stem / "master.wav").exists() else "")
        rows.append({
            "曲 ID": p.stem, "アーティスト": name, "曲名": meta.get("title") or (titles[0] if titles else ""),
            "曲名の別案": _join(titles[1:]), "フェーズ": phase, "制作週": week,
            "配信予定日": str(b.get("release_at") or "")[:10],
            "BPM": meta.get("bpm", ""), "ジャンル": _join(meta.get("genre") or [meta.get("primary_genre"), meta.get("secondary_genre")] if meta else ""),
            "言語": meta.get("language", ""), "Suno の指示（スタイル）": b.get("suno_style_prompt", ""), "歌詞": lyrics,
            "選んだテイク": chosen, "保管場所（SSD）": stored, **(release_info(song) if song else {}),
        })
    return rows


def read_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True)[SHEET]
    head = [c.value for c in ws[1]]
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        d = {h: ("" if v is None else v) for h, v in zip(head, r) if h}
        if d.get("曲 ID"):
            out[str(d["曲 ID"])] = d
    return out


def merge(rows: list[dict], old: dict[str, dict]) -> list[dict]:
    today = date.today().isoformat()
    seen = set()
    for r in rows:
        o = old.get(r["曲 ID"], {})
        for h in OWNER:   # オーナーの欄は前の値を残す（機械に値が無いときだけ機械の値）
            r[h] = o.get(h) or r.get(h, "")
        if o.get("フェーズ") in PHASES and PHASES.index(o["フェーズ"]) > PHASES.index(r["フェーズ"]):
            r["フェーズ"] = o["フェーズ"]
        r["次にやること"] = NEXT[r["フェーズ"]]
        changed = any(str(o.get(h, "")) != str(r.get(h, "")) for h, _, _ in COLUMNS if h != "更新日")
        r["更新日"] = today if changed or not o else o.get("更新日", today)
        seen.add(r["曲 ID"])
    for k, o in old.items():   # 元の情報が無くなった行（手で足した曲など）も消さずに残す
        if k not in seen:
            rows.append(o)
    return rows


def write(path: Path, rows: list[dict]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append([h for h, _, _ in COLUMNS])
    thin = Side(style="thin", color="D0D0D0")
    for i, (h, w, who) in enumerate(COLUMNS, 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2F3B4C" if who != "オーナー" else "7A5C2E")
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[c.column_letter].width = w
    for r in rows:
        ws.append([r.get(h, "") for h, _, _ in COLUMNS])
        n = ws.max_row
        for c in ws[n]:
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
        ph = str(r.get("フェーズ", ""))[:1]
        ws.cell(row=n, column=5).fill = PatternFill("solid", fgColor=PHASE_COLOR.get(ph, "FFFFFF"))
        ws.row_dimensions[n].height = 90
    dv = DataValidation(type="list", formula1='"' + ",".join(PHASES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"E2:E{max(ws.max_row, 2) + 200}")
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{ws.cell(row=1, column=len(COLUMNS)).column_letter}{max(ws.max_row, 2)}"
    # 説明のタブ
    g = wb.create_sheet("フェーズの説明")
    g.append(["フェーズ", "中身", "次にやること"])
    what = ["ブリーフ（作曲の指示）を作っている", "Suno の指示文と歌詞ができた。Suno で作る番", "テイクが届いた。1 本選ぶ番",
            "選んだテイクの音量を整える", "ジャケットを作る・選ぶ", "音源とジャケットがそろった。DistroKid に登録する番",
            "DistroKid に登録した（配信予約）", "ストアで配信が始まった"]
    for ph, w in zip(PHASES, what):
        g.append([ph, w, NEXT[ph]])
    g.append([])
    g.append(["色の見出し", "紺＝機械が埋める欄（書き直すと上書きされる）／茶＝オーナーが書く欄（書き直しても消えない）"])
    g.append(["フェーズの欄", "両方が書く。機械が分かる段階と、オーナーが書いた段階の「先に進んでいるほう」が残る"])
    for col, w in zip("ABC", (20, 50, 60)):
        g.column_dimensions[col].width = w
    for row in g.iter_rows():
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    for c in g[1]:
        c.font = Font(bold=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def target_path() -> tuple[Path | None, str]:
    base, why = archive.root()
    if base is None:
        return None, why
    return archive.child(base, archive.BOOK_DIR) / BOOK_NAME, str(base)


def update(out: Path | None = None, quiet: bool = False) -> Path | None:
    path, why = (out, "") if out else target_path()
    if path is None:
        print(f"   － 楽曲管理シートは書き直しませんでした：{why}")
        return None
    rows = merge(debut_rows() + weekly_rows(), read_existing(path))
    write(path, rows)
    counts = {}
    for r in rows:
        counts[r.get("フェーズ", "")] = counts.get(r.get("フェーズ", ""), 0) + 1
    summary = "・".join(f"{k} {v} 曲" for k, v in sorted(counts.items()) if k)
    print(f"   ○ 楽曲管理シート：{archive.label(path.parent) if not out else path.parent}/{path.name}（{len(rows)} 曲：{summary}）")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="楽曲管理シート（曲名・アーティスト・フェーズ・歌詞など）を作る・書き直す")
    ap.add_argument("--out", type=Path, help="書き出す場所（既定は SSD の 01_管理表/楽曲管理.xlsx）")
    a = ap.parse_args()
    update(a.out)


if __name__ == "__main__":
    main()

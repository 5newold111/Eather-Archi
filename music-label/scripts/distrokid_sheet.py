#!/usr/bin/env python3
"""
DistroKid 登録シートを作る（その週の全曲ぶん）。

DistroKid には公開 API が無く、画面の自動操作は規約上のリスクが高いので、登録だけは人が行う。
そのかわり「上から順にコピーして貼るだけ」のシートと、出す前の確認リストを自動で作る。

  入力：out/briefs/<週の月曜>_*.json（ブリーフ）
        out/takes/<ブリーフ名>/selection.json（選んだテイク）
        out/masters/<ブリーフ名>/master.wav（音量を整えた音源）
        out/visuals/<組>/cover/<週>/cover_final.jpg（3000×3000 のジャケット）
  出力：out/distrokid/<週の月曜>/sheet.md        … 人が見ながら登録するシート
        out/distrokid/<週の月曜>/sheet.csv       … 表計算ソフトで開ける一覧
        out/distrokid/<週の月曜>/<組>.metadata.json … Supabase の releases に入れる元データ

使い方
  python scripts/distrokid_sheet.py --week 2026-10-05
  python scripts/distrokid_sheet.py --week 2026-10-05 --title light="Window, East"   # タイトルを決める
  python scripts/distrokid_sheet.py --week 2026-10-05 --label focus                    # 子レーベルだけ
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_artist, load_label, read_json, step, write_json  # noqa: E402

ET, JST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Tokyo")
LANG_NAME = {"en": "English", "ja": "Japanese", "es": "Spanish", "ko": "Korean", "pt": "Portuguese",
             "fr": "French", "none": "Instrumental（歌なし）"}


def rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def find_cover(slug: str, week: str) -> Path | None:
    base = OUT / "visuals" / slug / "cover"
    for p in (base / week / "cover_final.jpg", base / week / "cover_final.png", base / "cover_final.jpg"):
        if p.exists():
            return p
    return None


def build_row(brief_path: Path, titles: dict[str, str]) -> dict:
    b = read_json(brief_path)
    slug, label_slug = b["artist_slug"], b.get("label_slug")
    artist = load_artist(slug, label_slug)
    label = load_label(label_slug)
    feat = load_artist(b["featured_artist_slug"]) if b.get("featured_artist_slug") else None

    title = titles.get(slug) or b.get("title") or ((b.get("remix_of") or {}).get("title_rule"))
    title_status = "決定" if title else "仮（候補 1 番）"
    if not title:
        cands = b.get("title_candidates") or []
        title = cands[0] if cands else None
    problems = []
    if not title:
        problems.append("タイトルが未定（write_brief.py を先に実行）")
        title, title_status = "（未定）", "未定"
    if re.search(r"\b(feat|ft)\.?\s", title, re.I):
        problems.append("タイトルに feat. が入っている（Featured Artist 欄で登録する）")

    rel_utc = datetime.fromisoformat(b["release_at"].replace("Z", "+00:00"))
    rel_et, rel_jst = rel_utc.astimezone(ET), rel_utc.astimezone(JST)

    stem = brief_path.stem
    sel_path = OUT / "takes" / stem / "selection.json"
    sel = read_json(sel_path) if sel_path.exists() else {}
    master = Path(sel.get("master", {}).get("path", OUT / "masters" / stem / "master.wav"))
    if not master.exists():
        problems.append("音源（master.wav）が未作成：select_takes.py → master_track.py")
    elif sel.get("master", {}).get("ok") is False:
        problems.append("音源の音量が目標から外れている（master.report.json を確認）")
    if sel and not sel.get("decision", {}).get("selected"):
        problems.append("テイクが未選択")
    if sel.get("decision", {}).get("spot_check"):
        problems.append("抜き取り確認の対象：登録前に 1 回聴く")

    cover = find_cover(slug, b["week_start"])
    if not cover:
        problems.append("ジャケット（3000×3000）が未作成：generate_visuals.py --kind cover → finalize_cover.py")

    lyr = artist.get("lyrics") or {}
    lang = "none" if lyr.get("mode") == "instrumental" else lyr.get("primary_language", "en")
    lyrics_file = brief_path.with_suffix(".lyrics_final.txt")
    genres = (artist.get("sound") or {}).get("genres") or []
    if artist.get("name_status") != "checked":
        problems.append("アーティスト名の重複確認が済んでいない（check_names.py）")

    return {
        "label": label.get("name"), "label_slug": label.get("slug"),
        "distrokid_account": (label.get("accounts") or {}).get("distrokid_email") or "本体アカウント",
        "artist": artist["name"], "artist_slug": slug,
        "featured_artist": feat["name"] if feat else "",
        "featured_spotify": ((feat.get("distribution") or {}).get("spotify_uri") or "未登録（相手の最初の曲が配信された後に Spotify のアーティストリンクを設定書へ）") if feat else "",
        "title": title, "title_status": title_status,
        "release_date_et": rel_et.strftime("%Y-%m-%d"), "release_time_et": rel_et.strftime("%H:%M"),
        "release_jst": rel_jst.strftime("%Y-%m-%d %H:%M"), "release_at_utc": b["release_at"],
        "language": LANG_NAME.get(lang, lang), "instrumental": lang == "none",
        "primary_genre": genres[0] if genres else "", "secondary_genre": genres[1] if len(genres) > 1 else "",
        "explicit": "No（Clean）", "ai_disclosure": "Yes（全曲 AI 使用を申告）",
        "songwriter": "（オーナーの本名：登録時に入力。シートには書かない）",
        "audio": rel(master), "cover": rel(cover) if cover else "",
        "lyrics_file": rel(lyrics_file) if lyrics_file.exists() else "",
        "isrc": "（空欄：DistroKid が自動で付ける）",
        "spotify_artist": (artist.get("distribution") or {}).get("spotify_uri") or "初回は「新しいアーティスト」",
        "remix_of": (f"{b['remix_of']['artist_name']} — {b['remix_of']['title']}" if b.get("remix_of") else ""),
        "problems": problems, "brief": rel(brief_path), "week_start": b["week_start"],
    }


def sheet_md(week: str, rows: list[dict]) -> str:
    out = [f"# DistroKid 登録シート（制作週 {week}）", "",
           "上から順に、DistroKid の Upload 画面の欄へコピーして貼ります。1 曲 約 5 分。",
           "⚠ のある曲は、問題を直してから登録してください。", "",
           "| # | アーティスト | Featured | タイトル | 配信日（ET） | 状態 |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        state = "⚠ " + str(len(r["problems"])) + " 件" if r["problems"] else "○ 登録できます"
        out.append(f"| {i} | {r['artist']} | {r['featured_artist'] or '—'} | {r['title'] if r['title_status'] != '未定' else ''}（{r['title_status']}） | "
                   f"{r['release_date_et']} {r['release_time_et']} | {state} |")
    for i, r in enumerate(rows, 1):
        out += ["", f"## {i}. {r['artist']} — {r['title']}", ""]
        if r["problems"]:
            out += ["**直すこと**", ""] + [f"- ⚠ {p}" for p in r["problems"]] + [""]
        out += [
            f"- ログインするアカウント：`{r['distrokid_account']}`（{r['label']}）",
            f"- Artist / Band name：`{r['artist']}`（Spotify：{r['spotify_artist']}）",
            (f"- Featured artist：`{r['featured_artist']}`（相手の Spotify：{r['featured_spotify']}。同名の別人に紐づかないよう必ず指定）"
             if r["featured_artist"] else "- Featured artist：なし"),
            f"- Song title：`{r['title']}`（feat. はタイトルに入れない）",
            *([f"- リミックス：元曲は {r['remix_of']}。画面に Remixer 欄があれば `{r['artist']}` を入れ、元曲のアーティストを"
               "メインアーティストに追加できる画面なら追加する（できなければこの名義だけで登録）"] if r.get("remix_of") else []),
            f"- Release date：`{r['release_date_et']}`（米国東部 {r['release_time_et']} ＝ 日本 {r['release_jst']}）",
            "  - 時刻を指定できる画面ならこの時刻、できなければ日付だけ（各国の 0 時に公開）",
            f"- Language：`{r['language']}`" + ("・Instrumental にチェック" if r["instrumental"] else ""),
            f"- Primary genre：`{r['primary_genre']}` / Secondary：`{r['secondary_genre']}`",
            f"- Explicit lyrics：{r['explicit']}",
            f"- AI の申告：{r['ai_disclosure']}。アップロード画面の AI の質問で、歌詞・ボーカル・演奏・作曲のうち使ったものをすべて選ぶ",
            f"- Songwriter：{r['songwriter']}",
            f"- 音源：`{r['audio']}`",
            f"- ジャケット：`{r['cover'] or '未作成'}`",
            f"- 歌詞（Lyrics を提出する場合）：`{r['lyrics_file'] or '—'}`",
            f"- ISRC：{r['isrc']}",
            "",
            "**出す前の確認**",
            "",
            "- [ ] 名前の綴りが設定書と同じ（大文字・記号・スペース）",
            "- [ ] タイトルに feat. / ft. が入っていない",
            "- [ ] ジャケットの文字が曲名・アーティスト名と一致（他の文字・URL・SNS 名なし）",
            "- [ ] 配信日が上の日付と同じ（本体は 2 週間後の水曜、子レーベルは設定書の曜日）",
            "- [ ] 登録後、DistroKid の画面で ISRC と UPC を控え、`" + f"out/distrokid/{week}/{r['artist_slug']}.metadata.json" + "` の isrc / upc に書く",
        ]
    out += ["", "---", "登録が終わったら：`python scripts/supabase_sync.py releases --week " + week + "`（鍵を入れた後）"]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="DistroKid 登録シートを作る")
    ap.add_argument("--week", required=True, help="制作週の月曜（ブリーフ名の先頭の日付）")
    ap.add_argument("--label", help="このレーベルだけ（drive / sleep / morning / focus / move）")
    ap.add_argument("--title", action="append", default=[], help='タイトルを決める。例 --title light="Window, East"')
    args = ap.parse_args()
    date.fromisoformat(args.week)

    titles = {}
    for t in args.title:
        k, _, v = t.partition("=")
        titles[k.strip()] = v.strip().strip('"')

    briefs = sorted(p for p in (OUT / "briefs").glob(f"{args.week}_*.json") if "." not in p.stem)
    if not briefs:
        sys.exit(f"[エラー] out/briefs/{args.week}_*.json がありません。select_references.py で骨組みを作ってください")

    out_dir = OUT / "distrokid" / args.week
    rows = []
    for bp in briefs:
        step(f"{bp.name}：登録内容を組み立てています")
        r = build_row(bp, titles)
        if args.label and r["label_slug"] != args.label:
            continue
        rows.append(r)
        meta_path = out_dir / f"{r['artist_slug']}.metadata.json"
        old = read_json(meta_path) if meta_path.exists() else {}
        meta = {k: r[k] for k in ("artist_slug", "label_slug", "title", "release_at_utc", "week_start", "audio",
                                  "cover", "lyrics_file", "brief")}
        meta.update(featured_artist_slug=read_json(bp).get("featured_artist_slug"), ai_disclosed=True,
                    lyrics_language=None if r["instrumental"] else (load_artist(r["artist_slug"], r["label_slug"]).get("lyrics") or {}).get("primary_language", "en"),
                    isrc=old.get("isrc"), upc=old.get("upc"), distrokid_release_id=old.get("distrokid_release_id"),
                    status=old.get("status", "planned"))
        write_json(meta_path, meta)
        if titles.get(r["artist_slug"]):
            b = read_json(bp)
            b["title"] = titles[r["artist_slug"]]
            write_json(bp, b)

    rows.sort(key=lambda r: (r["label_slug"] != "drive", r["label_slug"], r["artist"]))
    name = f"sheet_{args.label}" if args.label else "sheet"     # レーベルを絞ったときは別ファイル（全体のシートを上書きしない）
    (out_dir / f"{name}.md").write_text(sheet_md(args.week, rows), encoding="utf-8")
    with (out_dir / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as f:
        cols = [k for k in rows[0] if k != "problems"] + ["problems"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "problems": " / ".join(r["problems"])})
    ng = sum(1 for r in rows if r["problems"])
    step(f"{len(rows)} 曲ぶんのシートを書き出しました: {rel(out_dir / (name + '.md'))}（要対応 {ng} 曲）")


if __name__ == "__main__":
    main()

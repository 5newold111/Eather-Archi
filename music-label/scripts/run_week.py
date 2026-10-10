#!/usr/bin/env python3
"""
1 週間ぶんの処理を、段階ごとに全組まとめて流す。人の作業（Suno・テイクを聴く・DistroKid 登録）の間をつなぐ。

  brief   話題曲の取得 → 参考曲の割り当て（本体＋子レーベル）→ コラボの組み合わせ → Claude がブリーフを書く
  lyrics  Suno が書いた節（<ブリーフ名>.verses.txt）があれば核と合体して最終歌詞に
  takes   Suno のテイク（out/takes/<ブリーフ名>/）があれば計測して選ぶ（Tier A/B/C）
  finish  音量調整 → ジャケット（生成・3000×3000）→ DistroKid 登録シート → データベース → SNS 投稿の準備
  status  組ごとの進み具合を一覧にする（out/weeks/<週>/status.md）
  auto    lyrics → takes → finish → status（ブリーフは作らない。毎時の定期実行向け。変化が無ければほぼ何もしない）
  all     brief → lyrics → takes → finish を順に。準備ができていない組は飛ばす

使い方
  python scripts/run_week.py brief  --week 2026-10-12
  python scripts/run_week.py takes  --week 2026-10-12
  python scripts/run_week.py finish --week 2026-10-12
  python scripts/run_week.py status --week 2026-10-12
  python scripts/run_week.py all    --week next        # 来週の月曜（定期実行で使う）

  --references out/references  参考曲の解析シートのフォルダ（既定）
  --demo                       架空の参考曲で試す
  --overwrite                  Claude が書き足し済みのブリーフも作り直す
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import MAIN_LABEL, OUT, ROOT, load_artist, load_dotenv, monday_of, read_json, step  # noqa: E402

load_dotenv()
PY = sys.executable
S = ROOT / "scripts"
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aif", ".aiff"}


def run(args: list[str], quiet: bool = False) -> int:
    """他のスクリプトを呼ぶ。表示はそのまま流す（何をしているかは各スクリプトが日本語で出す）"""
    r = subprocess.run([PY, *map(str, args)], capture_output=quiet, text=True)
    if quiet and r.returncode != 0:
        print((r.stdout + r.stderr)[-600:])
    return r.returncode


def parse_week(v: str) -> date:
    today = date.today()
    if v in ("this", "今週"):
        return monday_of(today)
    if v in ("next", "来週"):
        return monday_of(today) + timedelta(weeks=1)
    return monday_of(date.fromisoformat(v))


def briefs_of(week: date) -> list[Path]:
    return sorted(p for p in (OUT / "briefs").glob(f"{week}_*.json") if "." not in p.stem)


def labels_with_artists() -> list[str]:
    return [p.stem for p in sorted((ROOT / "templates" / "labels").glob("*.json"))
            if not p.stem.startswith("_") and read_json(p).get("artists")]


def references_ok(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for p in folder.glob("*.json") if not p.name.endswith(".measure.json"))


# ---------------------------------------------------------------------------
def stage_brief(week: date, a) -> None:
    if (S / "fetch_trends.py").exists() and not (OUT / "trends" / f"{week}.json").exists():
        step("話題曲とトレンド言語を調べています（fetch_trends.py）")
        run([S / "fetch_trends.py", "--week", week.isoformat()])
    ref_dir = Path(a.references)
    n = references_ok(ref_dir)
    common = ["--week", week.isoformat()]
    if a.demo:
        common += ["--demo", "--force"]     # 試運転：デビュー前の組も作る
    elif n >= 11:
        common += ["--references", ref_dir]
    else:
        sys.exit(f"[停止] 参考曲の解析シートが {n} 件しかありません（11 件以上必要）: {ref_dir}\n"
                 "        analyze_track.py で参考曲を解析して増やすか、試すだけなら --demo を付けてください")
    g = OUT / "growth"
    if (g / "weights.json").exists():
        common += ["--weights", g / "weights.json"]
    if (g / "hints.json").exists():
        common += ["--hints", g / "hints.json"]
    if a.overwrite:
        common.append("--overwrite")
    step("本体レーベルの参考曲を割り当てています")
    run([S / "select_references.py", "--all", *common])
    for lab in labels_with_artists():
        step(f"子レーベル {lab} の参考曲を割り当てています")
        run([S / "select_references.py", "--label", lab, *common])
    if (S / "plan_collabs.py").exists():
        step("コラボ（feat. / remix）の組み合わせを決めています")
        run([S / "plan_collabs.py", "--week", week.isoformat()])
    todo = [p for p in briefs_of(week) if a.overwrite or not read_json(p).get("suno_style_prompt")]
    if todo:
        step(f"Claude がブリーフを書いています（{len(todo)} 曲）")
        extra = [] if a.demo else ["--references", ref_dir]
        run([S / "write_brief.py", *todo, *extra])
    print_next_lyrics(week)


def print_next_lyrics(week: date) -> None:
    need = []
    for p in briefs_of(week):
        b = read_json(p)
        if b.get("lyrics_mode") in ("core_fixed", "topic_only") and not p.with_suffix(".lyrics_final.txt").exists():
            need.append(p)
    if need:
        print("\n■ 人の作業：Suno の Write Lyrics で節を書かせ、次の名前で保存してください")
        for p in need:
            print(f"   {p.with_suffix('.verses.txt').relative_to(ROOT)}   ← 指示文は {p.name} の suno_lyric_prompt")
        print("   保存したら  python scripts/run_week.py lyrics --week " + week.isoformat())


def stage_lyrics(week: date, a) -> None:
    for p in briefs_of(week):
        b = read_json(p)
        if b.get("lyrics_mode") not in ("core_fixed", "topic_only"):
            continue
        final, verses = p.with_suffix(".lyrics_final.txt"), p.with_suffix(".verses.txt")
        if final.exists():
            continue
        if verses.exists():
            step(f"{p.stem}：Suno の節と核を合体しています")
            run([S / "merge_lyrics.py", p, "--verses", verses])
    print_next_lyrics(week)


def stage_takes(week: date, a) -> None:
    waiting = []
    for p in briefs_of(week):
        folder = OUT / "takes" / p.stem
        has_audio = folder.exists() and any(f.suffix.lower() in AUDIO_EXT for f in folder.iterdir())
        if not has_audio:
            waiting.append(p.stem)
            continue
        sel = folder / "selection.json"
        if sel.exists() and read_json(sel).get("decision", {}).get("selected"):
            continue
        newest = max(f.stat().st_mtime for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXT)
        if sel.exists() and sel.stat().st_mtime > newest:
            continue          # 計測済みで新しいテイクも無い（人の選択待ち）
        step(f"{p.stem}：テイクを計測しています")
        run([S / "select_takes.py", p], quiet=False)
    if waiting:
        print("\n■ 人の作業：Suno の画面で聴いて選んだ 1 本だけをダウンロードし、次のフォルダに take_01.mp3 の名前で置いてください")
        print("   （Suno は月のダウンロード数に上限があるので、選ばないテイクは落とさない。機械で比べたいときだけ複数置く）")
        for w in waiting:
            print(f"   out/takes/{w}/")


def stage_finish(week: date, a) -> None:
    if not briefs_of(week):
        print("  この週のブリーフがありません")
        return
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    for p in briefs_of(week):
        b = read_json(p)
        sel_path = OUT / "takes" / p.stem / "selection.json"
        sel = read_json(sel_path) if sel_path.exists() else {}
        if not sel.get("decision", {}).get("selected"):
            continue
        if not sel.get("master", {}).get("ok"):
            step(f"{p.stem}：音量を整えています")
            run([S / "master_track.py", p])
        artist = load_artist(b["artist_slug"], b.get("label_slug"))
        cover_dir = OUT / "visuals" / b["artist_slug"] / "cover" / b["week_start"]
        if not (cover_dir / "candidates.json").exists() and has_openai:
            step(f"{p.stem}：ジャケットの候補を作っています")
            cmd = [S / "generate_visuals.py", "--artist", b["artist_slug"], "--kind", "cover", "--brief", p]
            if b.get("label_slug") and b["label_slug"] != MAIN_LABEL:
                cmd += ["--label", b["label_slug"]]
            run(cmd, quiet=True)
        if (cover_dir / "selection_cover.json").exists() and not (cover_dir / "cover_final.jpg").exists():
            step(f"{p.stem}：ジャケットを 3000×3000 に仕上げています")
            run([S / "finalize_cover.py", p])
        _ = artist
    step("DistroKid 登録シートを作っています")
    run([S / "distrokid_sheet.py", "--week", week.isoformat()])
    has_db = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))
    step("データベースへの登録内容を作っています" + ("（直接登録します）" if has_db else "（鍵が無いので SQL を書き出します）"))
    run([S / "supabase_sync.py", "week", "--week", week.isoformat()] + (["--apply"] if has_db else []))
    step("SNS 投稿の準備（縦動画と説明文）をしています")
    run([S / "post_social.py", "plan", "--week", week.isoformat()])


# ---------------------------------------------------------------------------
def status_rows(week: date) -> list[dict]:
    rows = []
    for p in briefs_of(week):
        b = read_json(p)
        a = load_artist(b["artist_slug"], b.get("label_slug"), missing_ok=True)
        if a is None:   # 保管庫へ移した組の古いブリーフは表に出さない
            continue
        mode = b.get("lyrics_mode") or (a.get("lyrics") or {}).get("mode", "core_fixed")
        sel_path = OUT / "takes" / p.stem / "selection.json"
        sel = read_json(sel_path) if sel_path.exists() else {}
        dec = sel.get("decision", {})
        cover_dir = OUT / "visuals" / b["artist_slug"] / "cover" / b["week_start"]
        meta = OUT / "distrokid" / week.isoformat() / f"{b['artist_slug']}.metadata.json"
        m = read_json(meta) if meta.exists() else {}
        rows.append({
            "組": a["name"], "レーベル": b.get("label_slug"),
            "客演": b.get("featured_artist_slug") or ("remix" if b.get("remix_of") else ""),
            "ブリーフ": "○" if b.get("suno_style_prompt") else "骨組み",
            "歌詞": "不要" if mode == "instrumental" or mode == "full" else ("○" if p.with_suffix(".lyrics_final.txt").exists() else "Suno 待ち"),
            "テイク": ("○ " + dec["selected"]) if dec.get("selected") else (("候補 " + ",".join(dec.get("shortlist", []))) if dec.get("shortlist") else ("計測済み" if sel else "未")),
            "音量": "○" if sel.get("master", {}).get("ok") else "未",
            "ジャケット": "○" if (cover_dir / "cover_final.jpg").exists() else ("候補あり" if (cover_dir / "candidates.json").exists() else "未"),
            "登録": {"planned": "未登録", "uploaded": "登録済み", "live": "配信中"}.get(m.get("status"), m.get("status") or "未"),
            "SNS": "○" if (OUT / "social" / week.isoformat() / b["artist_slug"] / "plan.json").exists() else "未",
        })
    return rows


SUNO_CAP = {MAIN_LABEL: 60}      # Premier。子レーベルは既定で Pro の 20（設定書の suno_download_cap で変えられる）


def downloads_this_month() -> dict[str, tuple[int, int]]:
    """今月 Suno からダウンロードしたテイクの数（out/takes に置いた音源の数で数える）と上限"""
    from datetime import datetime
    now = datetime.now()
    used: dict[str, int] = {}
    for folder in (OUT / "takes").glob("*_*"):
        if folder.name.startswith("2000-") or not folder.is_dir():
            continue   # 試運転は数えない
        bp = OUT / "briefs" / f"{folder.name}.json"
        if not bp.exists():
            continue
        label = read_json(bp).get("label_slug") or MAIN_LABEL
        for f in folder.iterdir():
            t = datetime.fromtimestamp(f.stat().st_mtime)
            if f.suffix.lower() in AUDIO_EXT and (t.year, t.month) == (now.year, now.month):
                used[label] = used.get(label, 0) + 1
    out = {}
    for label, n in used.items():
        cap = SUNO_CAP.get(label) or int((read_json(ROOT / "templates" / "labels" / f"{label}.json").get("suno_download_cap") or 20)
                                         if (ROOT / "templates" / "labels" / f"{label}.json").exists() else 20)
        out[label] = (n, cap)
    return out


def stage_status(week: date, a) -> None:
    rows = status_rows(week)
    if not rows:
        print(f"  {week} のブリーフはまだありません（run_week.py brief --week {week}）")
        return
    if not briefs_of(week):
        return
    cols = list(rows[0])
    lines = [f"# 制作週 {week} の進み具合", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    dl = downloads_this_month()
    if dl:
        lines += ["", "**今月の Suno のダウンロード**（上限は Premier 60・Pro 20。超えると追加購入）", ""]
        for label, (n, cap) in sorted(dl.items()):
            warn = "　⚠ 残りわずか" if n >= cap * 0.8 else ""
            lines.append(f"- {label}：{n} / {cap}{warn}")
    path = OUT / "weeks" / week.isoformat() / "status.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    step(f"書き出しました: {path.relative_to(ROOT)}")
    try:
        from dashboard import build
        step(f"管理画面も更新しました: {build().relative_to(ROOT)}")
    except Exception as e:  # noqa: BLE001
        print(f"   （管理画面の更新に失敗：{e}）")


STAGES = {"brief": stage_brief, "lyrics": stage_lyrics, "takes": stage_takes, "finish": stage_finish, "status": stage_status}


def main() -> None:
    ap = argparse.ArgumentParser(description="1 週間ぶんの処理を段階ごとにまとめて流す")
    ap.add_argument("stage", choices=[*STAGES, "all", "auto"])
    ap.add_argument("--week", required=True, help="制作週の月曜（YYYY-MM-DD）か this / next")
    ap.add_argument("--references", default=str(OUT / "references"))
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    week = parse_week(a.week)
    print(f"=== 制作週 {week}：{a.stage} ===")
    if a.stage in ("all", "auto"):
        names = ("brief", "lyrics", "takes", "finish", "status") if a.stage == "all" else ("lyrics", "takes", "finish", "status")
        for name in names:
            print(f"\n----- {name} -----")
            try:
                STAGES[name](week, a)
            except SystemExit as e:   # 準備ができていない段階は飛ばして次へ
                print(f"  {e}")
        return
    STAGES[a.stage](week, a)


if __name__ == "__main__":
    main()

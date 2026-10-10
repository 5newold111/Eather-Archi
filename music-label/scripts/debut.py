#!/usr/bin/env python3
"""
デビューの段取りを 1 回で流す（本体 5 組の同時デビュー / 子レーベルの立ち上げ / 月 1 組の新人）。

  1. デビュー週を設定書に書く（debut_week ＝ デビュー曲が配信される週の月曜。制作はその 2 週間前から）
  2. 名前の重複確認（照合先に届かなければ前回の確認結果を使い、その旨を残す）
  3. ロゴとアーティスト写真の候補を作る（OpenAI の鍵・残高が無ければ指示文だけ）
  4. データベースに登録（鍵が無ければ SQL を書き出す）
  5. 準備状況の一覧を書き出す（out/debut/<デビュー週>/readiness.md）

使い方
  デビュー週は「毎月 1 日のある週」（月曜始まり）。--month で月を指定すると、その週を自動で計算する
  python scripts/debut.py --month 2026-12                               # 本体 5 組を 12 月デビュー（11/30 の週）
  python scripts/debut.py --next                                        # 2 週間の仕込みが取れる、いちばん近いデビュー週
  python scripts/debut.py --label focus --month 2027-02                 # 子レーベルの 3 組
  python scripts/debut.py --artist focus_newcomer --label focus --month 2027-03   # 新人 1 組
  python scripts/debut.py --launch-week 2026-11-30                      # 週を直接（1 日のある週でなければ次のデビュー週に合わせる）
  python scripts/debut.py --status                                      # 全組の準備状況だけ見る

  --skip-visuals  ロゴ・写真の生成を飛ばす / --skip-names  名前確認を飛ばす（再実行のとき）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (LEAD_WEEKS, MAIN_LABEL, OUT, ROOT, all_artists, as_date, debut_week_of_month,  # noqa: E402
                     is_debut_week, load_artist, load_dotenv, load_label, next_debut_week, release_at_utc,
                     release_schedule, save_artist, step, write_json)

load_dotenv()


def targets(args) -> list[dict]:
    if args.artist:
        return [load_artist(args.artist, args.label)]
    if args.label and args.label != MAIN_LABEL:
        return [load_artist(a["slug"], args.label) for a in load_label(args.label).get("artists", [])]
    return [a for a in all_artists() if a["label_slug"] == MAIN_LABEL]


def check_name_now(artist: dict) -> tuple[str, str]:
    """名前の照合。戻り値：(結果, 説明)"""
    import check_names  # 同じフォルダのスクリプト
    r = check_names.check_name(artist["name"])
    v = r["verdict"]
    if v == "clear":
        artist["name_status"] = "checked"
        msg = "照合先に完全一致なし。商標と SNS ハンドルは report の手動確認リンクで最終確認"
    elif v == "collision":
        artist["name_status"] = "draft"
        msg = f"完全一致あり（{r['exact'][0]}）。name_candidates の次の候補に変えて再実行"
    elif v == "near":
        msg = f"似た名前あり（{', '.join(r['near'][:2])}）。混同しないか人が判断"
    else:
        msg = "照合先に届かず未確認。前回の確認結果（name_status）をそのまま使う"
    nc = dict(artist.get("name_check") or {})          # 以前の確認メモ（手で調べた結果など）は残す
    nc.setdefault("auto_checks", []).append({"verdict": v, "date": date.today().isoformat(),
                                             "exact": r["exact"][:3], "near": r["near"][:3]})
    nc["auto_checks"] = nc["auto_checks"][-5:]
    if v != "unverified":
        nc.update(verdict=v, date=date.today().isoformat())
    artist["name_check"] = nc
    return v, msg


def visuals_state(artist: dict) -> dict:
    d = OUT / "visuals" / artist["slug"] / "debut"
    return {k: (d / f"selection_{k}.json").exists() for k in ("logo", "photo")} | {
        "candidates": (d / "candidates.json").exists()}


def readiness(artist: dict) -> list[tuple[str, bool | None, str]]:
    v = visuals_state(artist)
    vocal = artist.get("vocal") or {}
    instrumental = (artist.get("lyrics") or {}).get("mode") == "instrumental" or vocal.get("sex") == "none"
    debut = as_date(artist.get("debut_week"))
    rows = [
        ("デビュー週", debut is not None, str(debut) if debut else "未設定（LAUNCH_WEEK のまま）"),
        ("名前の確認", artist.get("name_status") in ("checked", "registered"),
         f"{artist.get('name_status')}（{(artist.get('name_check') or {}).get('verdict', '—')}）"),
        ("ロゴ", v["logo"], "選択済み" if v["logo"] else ("候補あり：review.md を見て選ぶ" if v["candidates"] else "未作成")),
        ("アーティスト写真", v["photo"], "選択済み" if v["photo"] else ("候補あり" if v["candidates"] else "未作成")),
        ("Suno の Persona", True if instrumental else bool(vocal.get("suno_persona_id")),
         "歌なしのため不要" if instrumental else (vocal.get("suno_persona_id") or "最初の 1 曲で作って vocal.suno_persona_id に書く")),
        ("英語の bio", bool((artist.get("distribution") or {}).get("bio_en")), "distribution.bio_en"),
    ]
    return rows


def write_readiness(path: Path, arts: list[dict]) -> int:
    lines = ["# デビューの準備状況", "", f"作成日 {date.today()}", ""]
    ng = 0
    for a in arts:
        label = load_label(a["label_slug"])
        debut = as_date(a.get("debut_week"))
        lines.append(f"## {a['name']}（{a['slug']} / {label.get('name')}）")
        if debut:
            prod = debut - timedelta(weeks=LEAD_WEEKS)
            wd, hr = release_schedule(a, label)
            rel = release_at_utc(prod, wd, hr)
            lines.append(f"- 制作開始：{prod} の週（ブリーフ生成）／デビュー曲の配信：{rel:%Y-%m-%d %H:%M} UTC")
        lines.append("")
        for name, ok, detail in readiness(a):
            mark = "○" if ok else ("－" if ok is None else "✕")
            ng += 0 if ok else 1
            lines.append(f"- {mark} {name}：{detail}")
        lines.append("")
    lines += ["## デビュー週までに人がすること", "",
              "- ロゴと写真を選ぶ（最初の 5 回はオーナーが選ぶ。`generate_visuals.py --artist <組> --kind debut --choose logo:<番号> --reason \"…\"`）",
              "- 商標と SNS ハンドルを `out/names/report.md` のリンクで確認",
              "- Spotify for Artists / Apple Music for Artists のプロフィールは、最初の配信が出た後に申請（写真・bio を貼る）",
              ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return ng


def main() -> None:
    ap = argparse.ArgumentParser(description="デビューの段取りをまとめて行う")
    ap.add_argument("--launch-week", help="デビュー曲が配信される週の月曜（YYYY-MM-DD）")
    ap.add_argument("--month", help="デビューする月（YYYY-MM）。その月の 1 日のある週になる")
    ap.add_argument("--next", action="store_true", help="2 週間の仕込みが取れる、いちばん近いデビュー週")
    ap.add_argument("--label", help="子レーベル（focus など）。省略で本体")
    ap.add_argument("--artist", help="1 組だけ（月 1 組の新人など）")
    ap.add_argument("--status", action="store_true", help="準備状況だけ表示")
    ap.add_argument("--skip-names", action="store_true")
    ap.add_argument("--skip-visuals", action="store_true")
    args = ap.parse_args()

    if args.status:
        arts = all_artists()
        ng = write_readiness(OUT / "debut" / "status.md", arts)
        for a in arts:
            ok = sum(1 for _, o, _ in readiness(a) if o)
            print(f"  {a['name']:<16} {a['label_slug']:<8} {ok}/6 項目 OK  デビュー週 {a.get('debut_week')}")
        step(f"一覧を書き出しました: out/debut/status.md（未完了 {ng} 項目）")
        return

    this_monday = date.today() - timedelta(days=date.today().weekday())
    if args.month:
        y, m = map(int, args.month.split("-"))
        week = debut_week_of_month(y, m)
    elif args.next:
        week = next_debut_week(this_monday + timedelta(weeks=LEAD_WEEKS + 1))   # 来週から制作を始められる週
    elif args.launch_week:
        week = date.fromisoformat(args.launch_week)
        week = week - timedelta(days=week.weekday())
        if not is_debut_week(week):
            nxt = next_debut_week(week)
            print(f"   （{week} の週には 1 日が無いので、次のデビュー週 {nxt} に合わせました）")
            week = nxt
    else:
        sys.exit("[エラー] --month 2026-12 / --next / --launch-week のどれかを指定してください（デビューは毎月 1 日のある週）")
    first_production = week - timedelta(weeks=LEAD_WEEKS)
    if first_production < this_monday:
        print(f"   ⚠ 制作開始週 {first_production} がもう過ぎています。2 週間の仕込みが取れないので、デビュー週を遅らせることを勧めます")

    arts = targets(args)
    step(f"{len(arts)} 組のデビューを準備します（デビュー週 {week}、制作開始 {first_production} の週）")
    stopped = []
    for a in arts:
        step(f"{a['name']}：デビュー週を設定書に書いています")
        a["debut_week"] = week.isoformat()
        if not args.skip_names:
            step(f"{a['name']}：名前の重複を確認しています（Apple Music・MusicBrainz・Deezer など）")
            try:
                v, msg = check_name_now(a)
            except Exception as e:  # noqa: BLE001
                v, msg = "unverified", f"確認中にエラー（{e}）。前回の結果を使う"
            print(f"     {v}：{msg}")
            if v == "collision":
                stopped.append(a["name"])
        save_artist(a)
        if a["name"] in stopped or args.skip_visuals:
            continue
        if a.get("name_status") not in ("checked", "registered"):
            print("     名前が確認済みでないので、ロゴと写真は作りません")
            continue
        if (OUT / "visuals" / a["slug"] / "debut" / "candidates.json").exists():
            print("     ロゴと写真の候補は作成済み（作り直すときは generate_visuals.py を直接実行）")
            continue
        step(f"{a['name']}：ロゴとアーティスト写真の候補を作っています")
        cmd = [sys.executable, str(ROOT / "scripts" / "generate_visuals.py"), "--artist", a["slug"], "--kind", "debut"]
        if a["label_slug"] != MAIN_LABEL:
            cmd += ["--label", a["label_slug"]]
        r = subprocess.run(cmd, capture_output=True, text=True)
        tail = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-2:]
        print("     " + " / ".join(t.strip() for t in tail))

    step("データベースへの登録内容を書き出しています")
    has_db_key = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))
    subprocess.run([sys.executable, str(ROOT / "scripts" / "supabase_sync.py"), "seed"] + (["--apply"] if has_db_key else []),
                   check=False)

    path = OUT / "debut" / week.isoformat() / "readiness.md"
    ng = write_readiness(path, [load_artist(a["slug"], a["label_slug"]) for a in arts])
    step(f"準備状況を書き出しました: {path.relative_to(ROOT)}（未完了 {ng} 項目）")
    if stopped:
        print(f"   ✕ 名前がぶつかった組：{', '.join(stopped)}。名前を変えて  debut.py --artist <組> --launch-week {week}  を再実行")
    write_json(OUT / "debut" / week.isoformat() / "targets.json", [a["slug"] for a in arts])


if __name__ == "__main__":
    main()

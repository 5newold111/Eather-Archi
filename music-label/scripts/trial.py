#!/usr/bin/env python3
"""
1 組だけ、本物の鍵で 1 週間分を通しで動かして確かめる（試運転）。終わったら --cleanup で跡を消せる。

  使う週は「2000-01-03」の試験用の週（本番の週と混ざらない）。参考曲は架空のもの（--demo）を使う
  1. 骨組み（参考曲の割り当て）
  2. Claude がブリーフを書く                         … ANTHROPIC_API_KEY
  3. テイク：Suno の本物のテイクがあればそれ（--takes）、無ければ合成音で代用 → 計測して選ぶ
  4. 音量を -14 LUFS / -1 dBTP に整える
  5. ジャケット候補を作り、Claude が見て採点 → 3000×3000 に仕上げる   … OPENAI_API_KEY（＋ ANTHROPIC）
     （試運転の選択は「最初の 5 回の判断」に数えない）
  6. DistroKid 登録シート
  7. Supabase に登録し、音源とジャケットを非公開の保管庫に上げる → 読み戻して確認   … SUPABASE_*
  8. SNS：YouTube に**非公開**で上げる（公開予約もしない）。Instagram は公開になってしまうので試さない。
     TikTok は --with-tiktok のときだけ（審査前は自分だけに公開）
  9. 結果の一覧 → out/trial/report.md

使い方
  python3 scripts/trial.py --artist light
  python3 scripts/trial.py --artist light --takes ~/Downloads/suno_light      # Suno のテイク（1 本でも可）
  python3 scripts/trial.py --artist focus_beats --label focus
  python3 scripts/trial.py --cleanup        # 試運転のデータを消す（データベース・保管庫・手元のファイル）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import MAIN_LABEL, OUT, ROOT, load_artist, load_dotenv, read_json, write_json  # noqa: E402

load_dotenv()
WEEK = "2000-01-03"            # 試運転専用の週
RELEASE_WEEK = "2000-01-17"    # その 2 週間後（配信週）
PY = sys.executable
S = ROOT / "scripts"
AUDIO = {".mp3", ".wav", ".m4a", ".flac", ".aif", ".aiff"}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, step: str, state: str, detail: str) -> None:
        self.rows.append((step, state, detail))
        mark = {"ok": "○", "ng": "✕", "skip": "－"}[state]
        print(f"\n{mark} {step}：{detail}\n")


def run(args: list, quiet: bool = False) -> tuple[int, str]:
    r = subprocess.run([PY, *map(str, args)], capture_output=True, text=True, cwd=ROOT)
    out = r.stdout + r.stderr
    if not quiet:
        print(out.rstrip())
    return r.returncode, out


def has(*names: str) -> bool:
    import os
    return all(os.environ.get(n) for n in names)


def trial(a) -> None:
    rep = Report()
    art = load_artist(a.artist, a.label)
    label = art["label_slug"]
    stem = f"{WEEK}_{art['slug']}"
    brief = OUT / "briefs" / f"{stem}.json"
    print(f"=== 試運転：{art['name']}（{label}）／試験用の週 {WEEK} ===")

    # 1. 骨組み
    cmd = [S / "select_references.py", "--demo", "--week", WEEK, "--force", "--overwrite"]
    cmd += ["--label", label] if label != MAIN_LABEL else ["--artist", art["slug"]]
    code, _ = run(cmd, quiet=True)
    if label != MAIN_LABEL:   # 子レーベルは全組ぶん作られるので、対象以外は消す
        for p in (OUT / "briefs").glob(f"{WEEK}_*.json"):
            if p.stem != stem:
                p.unlink()
    rep.add("1. 骨組み", "ok" if brief.exists() else "ng", str(brief.relative_to(ROOT)) if brief.exists() else "作れませんでした")
    if not brief.exists():
        return finish(rep)

    # 2. Claude のブリーフ
    if has("ANTHROPIC_API_KEY"):
        code, out = run([S / "write_brief.py", brief])
        b = read_json(brief)
        ok = bool(b.get("suno_style_prompt"))
        rep.add("2. Claude のブリーフ", "ok" if ok else "ng",
                f"タイトル候補：{' / '.join(b.get('title_candidates', [])[:3])}" if ok else out.strip().splitlines()[-1][:120])
    else:
        rep.add("2. Claude のブリーフ", "skip", "ANTHROPIC_API_KEY が無い")
    b = read_json(brief)

    # 3. テイク
    takes = OUT / "takes" / stem
    if takes.exists():
        shutil.rmtree(takes)
    takes.mkdir(parents=True)
    src = Path(a.takes).expanduser() if a.takes else None
    if src and src.exists():
        files = [f for f in sorted(src.iterdir()) if f.suffix.lower() in AUDIO]
        for i, f in enumerate(files, 1):
            shutil.copy(f, takes / f"take_{i:02d}{f.suffix.lower()}")
        what = f"Suno のテイク {len(files)} 本"
    else:
        import select_takes
        select_takes.make_demo(takes)
        what = "合成音のテイク 6 本（Suno のテイクは --takes で指定）"
    run([S / "select_takes.py", brief, "--tier", "C"], quiet=True)
    sel = read_json(takes / "selection.json")
    if not sel["decision"].get("selected"):
        best = max(sel["takes"], key=lambda t: t["score"])
        run([S / "select_takes.py", brief, "--choose", str(best["take_no"]), "--note", "試運転（条件外でも最高点を採用）"], quiet=True)
        sel = read_json(takes / "selection.json")
    chosen = next(t for t in sel["takes"] if t["take"] == sel["decision"]["selected"])
    rep.add("3. テイクの計測", "ok", f"{what} → {chosen['take']}（{chosen['score']} 点、{'条件を満たす' if chosen['passed'] else '条件外：' + '、'.join(chosen['hard_fail'])}）")

    # 4. 音量
    code, out = run([S / "master_track.py", brief], quiet=True)
    m = read_json(takes / "selection.json").get("master", {})
    rep.add("4. 音量調整", "ok" if m.get("ok") else "ng", f"{m.get('lufs')} LUFS / {m.get('true_peak_db')} dBTP")

    # 5. ジャケット
    cover_dir = OUT / "visuals" / art["slug"] / "cover" / WEEK
    if has("OPENAI_API_KEY") and not a.skip_images:
        cmd = [S / "generate_visuals.py", "--artist", art["slug"], "--kind", "cover", "--brief", brief]
        if label != MAIN_LABEL:
            cmd += ["--label", label]
        code, out = run(cmd)
        cands = read_json(cover_dir / "candidates.json") if (cover_dir / "candidates.json").exists() else []
        made = [c for c in cands if c.get("file")]
        usable = [c for c in made if c.get("compliance_ok") is not False]
        if usable:
            best = max(usable, key=lambda c: c.get("total", 0))
            # 試運転の選択は、オーナーの判断（最初の 5 回）に数えない
            write_json(cover_dir / "selection_cover.json", {"kind": "cover", "chosen": best["no"], "by": "trial"})
            scored = sum(1 for c in made if "scores_note" not in c)
            rep.add("5. ジャケット生成と採点", "ok",
                    f"{len(made)} 枚生成・Claude の採点 {scored} 枚・採用 #{best['no']}（{best.get('total')} 点）" +
                    (f"・除外 {len(made) - len(usable)} 枚" if len(made) > len(usable) else ""))
        else:
            rep.add("5. ジャケット生成と採点", "ng", (out.strip().splitlines() or ["生成できませんでした"])[-1][:150])
    else:
        rep.add("5. ジャケット生成と採点", "skip", "OPENAI_API_KEY が無い（仮の画像で続けます）")
    if not (cover_dir / "selection_cover.json").exists():
        import finalize_cover
        finalize_cover.make_demo(cover_dir)
    code, out = run([S / "finalize_cover.py", brief], quiet=True)
    ok = (cover_dir / "cover_final.jpg").exists()
    rep.add("   ジャケットの仕上げ", "ok" if ok else "ng", "3000×3000 JPEG" if ok else out.strip()[-150:])

    # 6. 登録シート
    title = (b.get("title_candidates") or [f"Trial {art['slug']}"])[0]
    run([S / "distrokid_sheet.py", "--week", WEEK, "--title", f"{art['slug']}={title}"], quiet=True)
    meta = OUT / "distrokid" / WEEK / f"{art['slug']}.metadata.json"
    rep.add("6. DistroKid 登録シート", "ok" if meta.exists() else "ng", f"out/distrokid/{WEEK}/sheet.md（タイトル「{title}」）")

    # 7. データベース
    if has("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        run([S / "supabase_sync.py", "seed", "--apply"], quiet=True)
        code, out = run([S / "supabase_sync.py", "week", "--week", WEEK, "--apply"], quiet=True)
        from _supabase import Client
        cl = Client()
        try:
            rows = cl.select("releases", f"release_week=eq.{RELEASE_WEEK}&select=title,status,master_wav_path,cover_path")
            ok = any(r.get("master_wav_path") and r.get("cover_path") for r in rows)
            rep.add("7. データベースと保管庫", "ok" if ok else "ng",
                    f"releases に {len(rows)} 行（音源・ジャケットの保存先あり）" if ok else (out.strip().splitlines() or ["登録できませんでした"])[-1][:150])
        except RuntimeError as e:
            rep.add("7. データベースと保管庫", "ng", str(e)[:150])
    else:
        rep.add("7. データベースと保管庫", "skip", "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が無い")

    # 8. SNS
    run([S / "post_social.py", "plan", "--week", WEEK], quiet=True)
    plan_path = OUT / "social" / WEEK / art["slug"] / "plan.json"
    if plan_path.exists():
        plan = read_json(plan_path)
        plan["trial"] = True          # 定期実行の投稿には絶対に乗せない
        write_json(plan_path, plan)
        only = "youtube,tiktok" if a.with_tiktok else "youtube"
        if a.skip_youtube:
            rep.add("8. SNS（YouTube 非公開）", "skip", "--skip-youtube")
        else:
            run([S / "post_social.py", "post", "--week", WEEK, "--only", only, "--private", "--trial"])
            plan = read_json(plan_path)
            for p in plan["posts"]:
                if p["platform"] in only.split(","):
                    res = p.get("result") or {}
                    state = "ok" if p["status"] == "done" else ("ng" if res.get("error") else "skip")
                    detail = (f"非公開で投稿：{res.get('url') or res.get('publish_id')}" if state == "ok"
                              else (res.get("error") or "鍵が無いので飛ばしました")[:150])
                    rep.add(f"8. SNS（{p['platform']}）", state, detail)
    else:
        rep.add("8. SNS", "ng", "縦動画と説明文を作れませんでした")
    finish(rep, art)


def finish(rep: Report, art: dict | None = None) -> None:
    mark = {"ok": "○", "ng": "✕", "skip": "－"}
    lines = [f"# 試運転の結果（{date.today()}）", "", f"試験用の週 {WEEK}" + (f"／{art['name']}" if art else ""), "",
             "| 段階 | 結果 | 内容 |", "|---|---|---|"]
    lines += [f"| {s} | {mark[st]} | {d} |" for s, st, d in rep.rows]
    lines += ["", "## 目で確かめるところ", "",
              "- YouTube Studio：非公開の動画が 1 本あるか（タイトル・説明文・『合成コンテンツ』の表示）",
              "- Supabase の Table Editor：releases の release_week が 2000-01-17 の行",
              "- Supabase の Storage：masters / covers / generations に試運転のファイル",
              f"- 手元：out/masters/{WEEK}_*/master.wav、out/visuals/*/cover/{WEEK}/cover_final.jpg、out/social/{WEEK}/*/clip_vertical.mp4",
              "", "## 片付け", "", "`python3 scripts/trial.py --cleanup`（YouTube の非公開動画だけは YouTube Studio で手で削除）", ""]
    p = OUT / "trial" / "report.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    ng = sum(1 for _, st, _ in rep.rows if st == "ng")
    print(f"=== 試運転おわり：✕ {ng} 件 → {p.relative_to(ROOT)} ===")


def cleanup() -> None:
    print(f"=== 試運転のデータを消します（試験用の週 {WEEK}）===")
    if has("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        from _supabase import Client
        cl = Client()
        try:
            rels = cl.select("releases", f"release_week=eq.{RELEASE_WEEK}&select=id")
            ids = ",".join(r["id"] for r in rels)
            if ids:
                assets = cl.select("visual_assets", f"release_id=in.({ids})&select=id")
                aids = ",".join(x["id"] for x in assets)
                if aids:
                    cl.delete("visual_decisions", f"asset_id=in.({aids})")
                    cl.delete("visual_assets", f"id=in.({aids})")
                cl.delete("releases", f"id=in.({ids})")
            briefs = cl.delete("briefs", f"week_start=eq.{WEEK}")
            print(f"   データベース：リリース {len(rels)} 件・ブリーフ {len(briefs)} 件を消しました（テイクの記録も一緒に消えます）")
            man_path = OUT / "supabase" / "uploaded.json"
            man = read_json(man_path) if man_path.exists() else {}
            gone = [k for k in man if WEEK in k or RELEASE_WEEK in k]
            for bucket in ("masters", "covers", "generations", "social"):
                paths = [k.split("/", 1)[1] for k in gone if k.startswith(bucket + "/")]
                cl.remove(bucket, paths)
            for k in gone:
                man.pop(k, None)
            write_json(man_path, man)
            print(f"   保管庫：ファイル {len(gone)} 件を消しました")
        except RuntimeError as e:
            print(f"   ✕ データベースの片付けに失敗：{str(e)[:200]}")
    targets = [*(OUT / "briefs").glob(f"{WEEK}_*"), *(OUT / "takes").glob(f"{WEEK}_*"), *(OUT / "masters").glob(f"{WEEK}_*"),
               *(OUT / "visuals").glob(f"*/cover/{WEEK}"), OUT / "distrokid" / WEEK, OUT / "social" / WEEK,
               OUT / "supabase" / f"week_{WEEK}.sql", OUT / "collabs" / f"{WEEK}.json"]
    n = 0
    for t in targets:
        if t.is_dir():
            shutil.rmtree(t)
            n += 1
        elif t.exists():
            t.unlink()
            n += 1
    print(f"   手元：{n} 件を消しました")
    rep = OUT / "trial" / "report.md"
    if rep.exists() and "youtube.com/shorts" in rep.read_text(encoding="utf-8"):
        print("   YouTube の非公開の試験動画は、YouTube Studio → コンテンツ から手で削除してください（この仕組みには削除の権限を持たせていません）")


def main() -> None:
    ap = argparse.ArgumentParser(description="1 組の試運転（本物の鍵で 1 週間分）")
    ap.add_argument("--artist", help="組（light など）")
    ap.add_argument("--label", help="子レーベルの組のとき")
    ap.add_argument("--takes", help="Suno のテイクを置いたフォルダ（無ければ合成音）")
    ap.add_argument("--skip-images", action="store_true", help="画像の生成を飛ばす（費用をかけずに試す）")
    ap.add_argument("--skip-youtube", action="store_true")
    ap.add_argument("--with-tiktok", action="store_true", help="TikTok も試す（審査前は自分だけに公開）")
    ap.add_argument("--cleanup", action="store_true")
    a = ap.parse_args()
    if a.cleanup:
        cleanup()
    elif a.artist:
        trial(a)
    else:
        ap.error("--artist か --cleanup を指定してください")


if __name__ == "__main__":
    main()

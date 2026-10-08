#!/usr/bin/env python3
"""
定期実行の設定（Mac の launchd を使う。PC を起動していれば、決まった時刻に自動で動く）。

  用語
    launchd   … Mac に最初から入っている「決まった時刻にプログラムを動かす」仕組み
    plist     … launchd に渡す設定ファイル（~/Library/LaunchAgents/ に置く）
    スリープ中に時刻が過ぎた仕事は、起きたときに 1 回だけ実行される

  仕事の一覧（時刻は Mac の時計＝日本時間）
    post     15 分ごと      配信時刻を過ぎた SNS 投稿を出す（YouTube は公開予約なので前もって上げる）
    trends   月 23:30       今週の話題曲・トレンド言語を調べる
    brief    火 06:00       今週の全組のブリーフを作る（参考曲の割り当て → コラボ → Claude）
    auto     火〜木 8〜23 時の毎時   歌詞の合体・テイクの計測・音量・ジャケット・登録シート・DB・SNS 準備（変化が無ければ何もしない）
    growth   木 10:00       成績を集めて成長分析（重み・ヒント・方針転換の提案）→ 転換の適用
    backup   毎日 03:30     音源・ジャケット・記録を Cloudflare R2 に予備保管
    monthly  毎月 1 日 09:00  月 1 組の追加と週の曲数の確認、Instagram の鍵の延長、四半期の EP 計画（1・4・7・10 月）

使い方
  python scripts/schedule.py list                 # 仕事の一覧と次の実行時刻
  python scripts/schedule.py write                # plist を out/schedule/ に書き出すだけ（中身を確認できる）
  python scripts/schedule.py install              # Mac に登録（~/Library/LaunchAgents/ に置いて有効化）
  python scripts/schedule.py uninstall            # 登録を外す
  python scripts/schedule.py run post             # 1 つの仕事を今すぐ動かす（ログは out/logs/<仕事>.log）
  python scripts/schedule.py cron                 # Mac 以外（Linux など）向けの crontab の行を表示
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, step  # noqa: E402

PY = sys.executable
S = ROOT / "scripts"
PREFIX = "jp.etherarchi.label"
PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"   # ffmpeg（Homebrew）を見つけられるように

# 仕事の中身：(説明, 実行するコマンドの並び)
JOBS: dict[str, tuple[str, list[list[str]]]] = {
    "post":    ("SNS 投稿（配信時刻を過ぎたもの）と管理画面の更新", [["post_social.py", "post", "--week", "all"], ["dashboard.py"]]),
    "trends":  ("今週の話題曲・トレンド言語", [["fetch_trends.py", "--week", "this"]]),
    "brief":   ("アーティスト台帳の読み込み → 今週のブリーフ（全組）",
                [["artist_book.py", "pull", "--if-exists"], ["run_week.py", "brief", "--week", "this"]]),
    "auto":    ("歌詞・テイク・仕上げ・登録シート・DB・SNS 準備", [["run_week.py", "auto", "--week", "this"]]),
    "growth":  ("成績の回収と成長分析・方針転換", [["collect_metrics.py", "--analyze"], ["apply_pivot.py", "--from-growth"]]),
    "backup":  ("R2 への予備保管", [["backup_r2.py"]]),
    "monthly": ("新しい組と来月の近況の提案（台帳へ）・曲数の確認・Instagram の鍵の延長・四半期の EP 計画",
                [["artist_book.py", "pull", "--if-exists"], ["expand_label.py", "check", "--apply"],
                 ["artist_book.py", "propose-update", "--all", "--if-exists"],
                 ["post_social.py", "refresh-ig"], ["plan_quarterly.py", "--if-quarter-start"]]),
}


def calendar(job: str) -> dict:
    """launchd の起動条件（Weekday：0=日 1=月 2=火 …）"""
    if job == "post":
        return {"StartInterval": 900}
    if job == "trends":
        return {"StartCalendarInterval": [{"Weekday": 1, "Hour": 23, "Minute": 30}]}
    if job == "brief":
        return {"StartCalendarInterval": [{"Weekday": 2, "Hour": 6, "Minute": 0}]}
    if job == "auto":
        return {"StartCalendarInterval": [{"Weekday": wd, "Hour": h, "Minute": 5} for wd in (2, 3, 4) for h in range(8, 24)]}
    if job == "growth":
        return {"StartCalendarInterval": [{"Weekday": 4, "Hour": 10, "Minute": 0}]}
    if job == "backup":
        return {"StartCalendarInterval": [{"Hour": 3, "Minute": 30}]}
    if job == "monthly":
        return {"StartCalendarInterval": [{"Day": 1, "Hour": 9, "Minute": 0}]}
    raise KeyError(job)


CRON = {"post": "*/15 * * * *", "trends": "30 23 * * 1", "brief": "0 6 * * 2", "auto": "5 8-23 * * 2-4",
        "growth": "0 10 * * 4", "backup": "30 3 * * *", "monthly": "0 9 1 * *"}


def plist_for(job: str) -> dict:
    logs = OUT / "logs"
    return {
        "Label": f"{PREFIX}.{job}",
        "ProgramArguments": [PY, str(S / "schedule.py"), "run", job],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": PATH_ENV, "PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(logs / f"{job}.launchd.log"),
        "StandardErrorPath": str(logs / f"{job}.launchd.log"),
        "RunAtLoad": False,
        **calendar(job),
    }


def run_job(job: str) -> int:
    """仕事を 1 つ実行してログに残す。まだ無いスクリプトは飛ばす"""
    desc, cmds = JOBS[job]
    log = OUT / "logs" / f"{job}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    lock = OUT / "logs" / f"{job}.lock"
    if lock.exists() and (datetime.now().timestamp() - lock.stat().st_mtime) < 3 * 3600:
        print(f"   {job} は前回の実行がまだ終わっていないので飛ばします")
        return 0
    lock.write_text(str(os.getpid()))
    code = 0
    try:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M} {job}：{desc} =====\n")
            for c in cmds:
                script = S / c[0]
                if not script.exists():
                    f.write(f"（{c[0]} がまだ無いので飛ばしました）\n")
                    continue
                f.flush()
                r = subprocess.run([PY, str(script), *c[1:]], stdout=f, stderr=subprocess.STDOUT, cwd=ROOT,
                                   env={**os.environ, "PATH": os.environ.get("PATH", "") + ":" + PATH_ENV})
                code = code or r.returncode
                f.write(f"→ 終了コード {r.returncode}\n")
        trim(log)
    finally:
        lock.unlink(missing_ok=True)
    return code


def trim(log: Path, keep: int = 4000) -> None:
    """ログは新しい 4,000 行だけ残す"""
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > keep:
        log.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="定期実行の設定（Mac の launchd）")
    ap.add_argument("command", choices=["list", "write", "install", "uninstall", "run", "cron"])
    ap.add_argument("job", nargs="?", help="run のときの仕事の名前")
    ap.add_argument("--only", help="install / write をこの仕事だけに（カンマ区切り）")
    a = ap.parse_args()
    jobs = a.only.split(",") if a.only else list(JOBS)

    if a.command == "list":
        for j in JOBS:
            print(f"  {j:<8} {CRON[j]:<16} {JOBS[j][0]}")
        print("  （左から 分 時 日 月 曜日。曜日は 1=月 … 0=日）")
        return
    if a.command == "run":
        if a.job not in JOBS:
            sys.exit(f"[エラー] 仕事の名前: {', '.join(JOBS)}")
        step(f"{a.job}：{JOBS[a.job][0]} を実行しています（ログ out/logs/{a.job}.log）")
        sys.exit(run_job(a.job))
    if a.command == "cron":
        print("# crontab -e で貼り付ける行（Mac 以外向け）")
        for j in jobs:
            print(f"{CRON[j]} cd {ROOT} && {PY} scripts/schedule.py run {j}")
        return

    out = OUT / "schedule"
    out.mkdir(parents=True, exist_ok=True)
    (OUT / "logs").mkdir(parents=True, exist_ok=True)
    files = []
    for j in jobs:
        p = out / f"{PREFIX}.{j}.plist"
        with p.open("wb") as f:
            plistlib.dump(plist_for(j), f)
        files.append(p)
    if a.command == "write":
        step(f"{len(files)} 個の設定を書き出しました: {out.relative_to(ROOT)}/（install で Mac に登録）")
        return

    if sys.platform != "darwin":
        sys.exit("[停止] install / uninstall は Mac 専用です。Mac 以外は  schedule.py cron  の行を crontab に貼ってください")
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    for p in files:
        dst = agents / p.name
        label = p.stem
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)   # 登録済みなら一度外す
        if a.command == "uninstall":
            dst.unlink(missing_ok=True)
            step(f"外しました: {label}")
            continue
        shutil.copy(p, dst)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dst)], capture_output=True, text=True)
        step(f"登録しました: {label}" + (f"（警告: {r.stderr.strip()}）" if r.returncode else ""))
    if a.command == "install":
        print("   Mac がスリープ中に時刻が過ぎた仕事は、起きたときに 1 回だけ動きます")
        print("   ログ: out/logs/<仕事>.log  ／  外すとき: python scripts/schedule.py uninstall")


if __name__ == "__main__":
    main()

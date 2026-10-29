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
    backup   毎日 03:30     情報・写真・ジャケット・楽曲を外付け SSD（DS72）に保管・楽曲管理シートを書き直し、SSD の中身ごと Cloudflare R2 にも予備保管
    monthly  毎月 1 日 09:00  月 1 組の追加と週の曲数の確認、Instagram の鍵の延長、四半期の EP 計画（1・4・7・10 月）、
                            デビューから 6 か月ごとのアーティスト写真の刷新（refresh_photos.py）
    intake   SSD の「楽曲情報」か「SUNO楽曲」に曲が入ったとき＋10 分ごと  いいねしてダウンロードした曲を取り込み、音量・ジャケット・DistroKid の下書き・公開セットまで進める（publish.py scan）
    release  毎日 10:15     DistroKid に登録した曲が Spotify に出たかを確かめ、ISRC・UPC・配信 URL を楽曲管理シートに記録（publish.py track）
    ssd      SSD をつないだとき  SSD が無い間に「ダウンロード/SUNO楽曲」へ入った曲を SSD の「楽曲情報」へ移し、ローカル保存の中身を SSD に写し、
                            配信が始まった曲はローカル保存から片付けて、Mac に知らせる（archive_sync.py --on-mount → publish.py scan）
    refs     既存楽曲に曲が入ったとき・SSD をつないだとき・30 分ごと  SSD の「既存楽曲」に新しく入った WAV・MP3・M4A だけを解析して、
                            参考曲の解析シートに登録し、Mac に知らせる（reference_db.py --watch。決定 78）
    retry    毎日 12:05     止まった週 1・月 1 の仕事（trends・brief・auto・growth・monthly）の、失敗した手順だけをやり直す（決定 113）
    update   10 分ごと      push された更新を取り込んで反映する（git pull → 定期実行の登録し直し → ダッシュボードも新しい版に。
                            動いている仕事があれば待つ。self_update.py。決定 108）
    webapp   いつも          ダッシュボードのウェブアプリ（http://localhost:8765）。Mac の起動中は動かしたままにする（webapp.py）

使い方
  python scripts/schedule.py list                 # 仕事の一覧と次の実行時刻
  python scripts/schedule.py write                # plist を out/schedule/ に書き出すだけ（中身を確認できる）
  python scripts/schedule.py install              # Mac に登録（~/Library/LaunchAgents/ に置いて有効化）
  python scripts/schedule.py uninstall            # 登録を外す
  python scripts/schedule.py status               # 登録の状態（○ 登録済み／✕ 未登録）
  python scripts/schedule.py run post             # 1 つの仕事を今すぐ動かす（ログは out/logs/<仕事>.log）
  python scripts/schedule.py ack brief            # 直したエラーの表示を消す（ダッシュボードの「仕組みの詰まり」から消える）
  python scripts/schedule.py cron                 # Mac 以外（Linux など）向けの crontab の行を表示
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time
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
    "post":    ("SNS 投稿（配信時刻を過ぎたもの）と管理画面の更新",
                [["post_social.py", "daily", "--if-on"],   # 毎日の投稿の案（3 日先まで。作ってあれば何もしない。決定 94）
                 ["post_social.py", "post", "--week", "all"], ["dashboard.py"]]),
    "trends":  ("今週の話題曲・トレンド言語", [["fetch_trends.py", "--week", "this"]]),
    "brief":   ("アーティスト管理表の読み込み → 今週のブリーフ（全組）",
                [["artist_book.py", "pull", "--if-exists"], ["suno_quota.py", "plan", "--quiet"], ["run_week.py", "brief", "--week", "this"],
                 ["daily.py", "make", "--if-active"], ["suno_quota.py", "plan", "--quiet"]]),   # Suno のダウンロード枠に合わせる（決定 105）   # 毎日配信の 21 日先までの曲（決定 94）
    "auto":    ("歌詞・テイク・仕上げ・登録シート・DB・SNS 準備", [["run_week.py", "auto", "--week", "this"]]),
    "growth":  ("成績の回収と成長分析・方針転換", [["collect_metrics.py", "--analyze"], ["apply_pivot.py", "--from-growth"],
                                          ["explore.py", "report"]]),   # 方向性の伸びを学んで、次の曲のくじに返す（決定 96）
    "backup":  ("外付け SSD への保管と R2 への予備保管", [["archive_sync.py"], ["backup_r2.py"]]),
    "monthly": ("新しい組と来月の近況の提案（管理表へ）・曲数の確認・Instagram の鍵の延長・四半期の EP 計画",
                [["artist_book.py", "pull", "--if-exists"], ["expand_label.py", "check", "--apply"],
                 ["artist_book.py", "propose-update", "--all", "--if-exists"],
                 ["post_social.py", "refresh-ig"], ["plan_quarterly.py", "--if-quarter-start"], ["refresh_photos.py", "--due"]]),
    "intake":  ("Suno の曲の取り込み → 音量・ジャケット・DistroKid の下書き・公開セット", [["publish.py", "scan", "--quiet"], ["suno_quota.py", "plan", "--quiet"]]),
    "release": ("配信の確認と ISRC・UPC・配信 URL の記録", [["publish.py", "track", "--quiet"]]),
    "ssd":     ("SSD をつないだとき：楽曲のデータを SSD へ移す・保管・取り込み", [["archive_sync.py", "--on-mount"], ["publish.py", "scan", "--quiet"]]),
    "refs":    ("既存楽曲に入った曲の解析 → 参考曲に登録", [["reference_db.py", "--watch"]]),
    "update":  ("push された更新を取り込んで反映（決定 108）", [["self_update.py"]]),
    "retry":   ("止まった週 1・月 1 の仕事の、失敗した手順だけを 1 日 1 回やり直す（決定 113）", [["schedule.py", "retry-failed"]]),
}
RETRY_JOBS = {"trends": "話題曲の調査", "brief": "ブリーフ", "auto": "毎週の制作", "growth": "成長分析", "monthly": "月 1 の仕事"}
RETRY_AFTER_HOURS = 20   # 失敗してからこの時間はやり直さない（その仕事の決まった時刻の実行・自分の毎時の実行に任せる）


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
    if job == "intake":   # フォルダに何か入ったらすぐ。念のため 10 分ごとにも見る
        sys.path.insert(0, str(S))
        import publish
        return {"WatchPaths": [str(p) for p in publish.inboxes()], "StartInterval": 600}
    if job == "release":
        return {"StartCalendarInterval": [{"Hour": 10, "Minute": 15}]}
    if job == "ssd":   # ドライブがつながるたびに動く（SSD でなければすぐ終わる）
        return {"StartOnMount": True}
    if job == "refs":   # 既存楽曲のフォルダに何か入ったらすぐ・SSD をつないだとき・念のため 30 分ごと（決定 78）
        sys.path.insert(0, str(S))
        import reference_db
        dirs = [str(d) for d in reference_db.audio_dirs()]
        return {**({"WatchPaths": dirs} if dirs else {}), "StartOnMount": True, "StartInterval": 1800}
    if job == "retry":   # 毎日 1 回（決定 113）
        return {"StartCalendarInterval": [{"Hour": 12, "Minute": 5}]}
    if job == "update":   # 10 分ごとに GitHub の更新を確かめる（決定 108）
        return {"StartInterval": 600}
    raise KeyError(job)


CRON = {"post": "*/15 * * * *", "trends": "30 23 * * 1", "brief": "0 6 * * 2", "auto": "5 8-23 * * 2-4",
        "growth": "0 10 * * 4", "backup": "30 3 * * *", "monthly": "0 9 1 * *", "intake": "*/10 * * * *",
        "release": "15 10 * * *", "ssd": "*/30 * * * *", "refs": "*/30 * * * *", "update": "*/10 * * * *", "retry": "5 12 * * *"}


# 動かしたままにする仕事（決まった時刻に動く仕事とは別）
SERVICES = {"webapp": ("ダッシュボードのウェブアプリ（http://localhost:8765）", ["webapp.py"])}


def plist_for(job: str) -> dict:
    logs = OUT / "logs"
    if job in SERVICES:
        return {"Label": f"{PREFIX}.{job}", "ProgramArguments": [PY, str(S / SERVICES[job][1][0]), *SERVICES[job][1][1:]],
                "WorkingDirectory": str(ROOT), "EnvironmentVariables": {"PATH": PATH_ENV, "PYTHONUNBUFFERED": "1"},
                "StandardOutPath": str(logs / f"{job}.launchd.log"), "StandardErrorPath": str(logs / f"{job}.launchd.log"),
                "RunAtLoad": True, "KeepAlive": True}
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


def loaded(uid: int, label: str) -> bool:
    """launchd に登録されているか"""
    return subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True).returncode == 0


def status() -> None:
    """登録の状態（○ 登録済み・動いている／✕ 未登録）"""
    uid = os.getuid()
    for j in [*JOBS, *SERVICES]:
        label = f"{PREFIX}.{j}"
        r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True, text=True)
        if r.returncode:
            print(f"  ✕ {j:<8} 未登録（python3 scripts/schedule.py install）")
            continue
        state = next((l.split("=", 1)[1].strip() for l in r.stdout.splitlines() if l.strip().startswith("state =")), "?")
        code = next((l.split("=", 1)[1].strip() for l in r.stdout.splitlines() if "last exit code" in l), "")
        print(f"  ○ {j:<8} 登録済み（{'動いている' if state == 'running' else '待機中'}" + (f"・前回の終了コード {code}" if code and code != "0" and code != "(never exited)" else "") + "）")


def run_job(job: str, only: list[int] | None = None, title: str = "") -> int:
    """仕事を 1 つ実行してログに残す。まだ無いスクリプトは飛ばす。only：この番号の手順だけ（やり直し。決定 113）"""
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
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M} {job}：{title or desc} =====\n")
            for i, c in enumerate(cmds):
                if only is not None and i not in only:
                    continue
                script = S / c[0]
                if not script.exists():
                    f.write(f"（{c[0]} がまだ無いので飛ばしました）\n")
                    continue
                f.write(f"▷ 手順 {i}：{' '.join(c)}\n")   # どの手順の結果か（失敗した手順だけやり直すため。決定 113）
                f.flush()
                r = subprocess.run([PY, str(script), *c[1:]], stdout=f, stderr=subprocess.STDOUT, cwd=ROOT,
                                   env={**os.environ, "PATH": os.environ.get("PATH", "") + ":" + PATH_ENV})
                code = code or r.returncode
                f.write(f"→ 終了コード {r.returncode}\n")
        trim(log)
    finally:
        lock.unlink(missing_ok=True)
    return code


def failed_steps(job: str) -> tuple[list[int], datetime | None]:
    """最後の実行で失敗した手順の番号と、その実行の時刻（失敗が無ければ空）"""
    log = OUT / "logs" / f"{job}.log"
    if not log.exists():
        return [], None
    last = log.read_text(encoding="utf-8", errors="replace").split("\n===== ")[-1]
    import re
    m = re.match(r"=* ?(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", last)
    when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M") if m else None
    marks = re.findall(r"▷ 手順 (\d+)：.*?→ 終了コード (\d+)", last, re.S)
    if marks:
        return [int(i) for i, c in marks if c != "0"], when
    codes = re.findall(r"→ 終了コード (\d+)", last)   # 目印の無い古い記録：あるスクリプトの順に結果を当てはめる
    run_idx = [i for i, c in enumerate(JOBS[job][1]) if (S / c[0]).exists()]
    return [i for i, c in zip(run_idx, codes) if c != "0"], when


def retry_failed() -> int:
    """止まった週 1・月 1 の仕事の、失敗した手順だけをやり直す（毎日 1 回。決定 113）"""
    from _common import notify
    fixed, still, waiting = [], [], []
    for job, ja in RETRY_JOBS.items():
        steps, when = failed_steps(job)
        if not steps:
            continue
        if when and (datetime.now() - when).total_seconds() < RETRY_AFTER_HOURS * 3600:
            print(f"   {ja}：失敗してからまだ {RETRY_AFTER_HOURS} 時間たっていないので、今日はやり直しません")
            waiting.append(ja)
            continue
        names = "・".join(JOBS[job][1][i][0] for i in steps)
        print(f"▶ {ja}（{job}）の失敗した手順をやり直します：{names}", flush=True)
        code = run_job(job, only=steps, title=f"止まっていた手順のやり直し（{names}）")
        (fixed if code == 0 else still).append(ja)
        print(f"   {'○ 直りました' if code == 0 else '✕ まだ失敗しています（明日もう一度）'}")
    if fixed:
        notify("止まっていた自動の仕事が直りました", "・".join(fixed))
    if still:
        notify("自動の仕事がまだ止まっています", "・".join(still) + "（明日もう一度やり直します。ダッシュボードの「仕組みの詰まり」を見てください）")
    print(f"【結果】やり直し：直った {len(fixed)}・まだ止まっている {len(still)}・明日やり直す {len(waiting)}" if fixed or still or waiting
          else "【結果】止まっている仕事はありません")
    return 0


def ack_job(job: str) -> int:
    """定期実行のエラーの表示を消す：ログの最後に「確認済み」の区切りを足す（ログそのものは消さない。決定 89）"""
    log = OUT / "logs" / f"{job}.log"
    if not log.exists():
        print(f"【結果】{job} のログがありません")
        return 0
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M} {job}：エラーの表示を消しました（オーナーが直した。前の記録は上に残っています） =====\n→ 終了コード 0\n")
    print(f"【結果】定期実行「{job}」のエラーの表示を消しました。次の定期実行はいつもどおり動きます")
    return 0


def trim(log: Path, keep: int = 4000) -> None:
    """ログは新しい 4,000 行だけ残す"""
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > keep:
        log.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="定期実行の設定（Mac の launchd）")
    ap.add_argument("command", choices=["list", "write", "install", "uninstall", "run", "ack", "cron", "status", "retry-failed"])
    ap.add_argument("job", nargs="?", help="run のときの仕事の名前")
    ap.add_argument("--only", help="install / write をこの仕事だけに（カンマ区切り）")
    a = ap.parse_args()
    jobs = a.only.split(",") if a.only else [*JOBS, *SERVICES]

    if a.command == "retry-failed":   # 止まった仕事の、失敗した手順だけをやり直す（決定 113）
        sys.exit(retry_failed())
    if a.command == "status":
        if sys.platform != "darwin":
            sys.exit("[停止] status は Mac 専用です")
        status()
        return
    if a.command == "list":
        for j in JOBS:
            print(f"  {j:<8} {CRON[j]:<16} {JOBS[j][0]}")
        for j, (desc, _) in SERVICES.items():
            print(f"  {j:<8} {'いつも':<14} {desc}")
        print("  （左から 分 時 日 月 曜日。曜日は 1=月 … 0=日）")
        return
    if a.command == "run":
        if a.job not in JOBS:
            sys.exit(f"[エラー] 仕事の名前: {', '.join(JOBS)}")
        step(f"{a.job}：{JOBS[a.job][0]} を実行しています（ログ out/logs/{a.job}.log）")
        sys.exit(run_job(a.job))
    if a.command == "ack":   # エラーの表示を消す（決定 89）
        if a.job not in JOBS:
            sys.exit(f"[エラー] 仕事の名前: {', '.join(JOBS)}")
        sys.exit(ack_job(a.job))
    if a.command == "cron":
        print("# crontab -e で貼り付ける行（Mac 以外向け）")
        for j in jobs:
            if j in SERVICES:
                print(f"@reboot cd {ROOT} && {PY} scripts/{SERVICES[j][1][0]}")
                continue
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
    ng = []
    for p in files:
        dst = agents / p.name
        label = p.stem
        if loaded(uid, label):   # 登録済みなら一度外し、外し終わるまで待つ（すぐ入れ直すと失敗することがある）
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
            for _ in range(50):
                if not loaded(uid, label):
                    break
                time.sleep(0.2)
        if a.command == "uninstall":
            dst.unlink(missing_ok=True)
            step(f"外しました: {label}")
            continue
        shutil.copy(p, dst)
        err = ""
        for attempt in range(5):   # うまくいかなければ、少し待ってやり直す
            r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dst)], capture_output=True, text=True)
            if r.returncode == 0 or loaded(uid, label):
                break
            err = r.stderr.strip()
            time.sleep(1 + attempt)
        if loaded(uid, label):
            step(f"登録しました: {label}")
        else:
            ng.append(label)
            print(f"   ✕ 登録できませんでした: {label}（{err or '理由不明'}）")
    if a.command == "install" and ng:
        print(f"   ✕ {len(ng)} 個の仕事が登録できませんでした。もう一度  python3 scripts/schedule.py install  を実行し、"
              "それでも ✕ なら Mac を再起動してから実行してください")
    if a.command == "install":
        if "webapp" in jobs:
            print("   ダッシュボード：ブラウザで http://localhost:8765 を開く（ブックマークしておくと便利）")
        print("   Mac がスリープ中に時刻が過ぎた仕事は、起きたときに 1 回だけ動きます")
        print("   ログ: out/logs/<仕事>.log  ／  外すとき: python scripts/schedule.py uninstall")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
push された更新を、Mac に自動で反映する（オーナーの指示 2026-10-27「自動で反映させて」。決定 108）

  いままで Gon さんが手でしていたこと（git pull → schedule.py install → ダッシュボードを ⌘R）を、定期実行（10 分ごと）でシステムがする。
    1 GitHub に新しい更新があるか確かめる（git fetch。無ければ何もしない）
    2 動いている仕事があれば待つ（定期実行の仕事・ダッシュボードから動かした仕事・ブラウザの入力。次の 10 分後にもう一度）
    3 取り込む（git pull --ff-only。Mac の中の変更とぶつかるときは取り込まずに知らせる。履歴を書き換える取り込みはしない）
    4 定期実行を登録し直す（schedule.py install。この「更新」の仕事だけは自分自身なので外す）→ ダッシュボードも新しい版で立ち上がる
    5 開いているダッシュボードは、版が変わったのを見て自動で読み直す（入力中・ポップアップを開いている間は待つ）
    6 Mac に知らせる（何が変わったか）。部品（requirements.txt）が増えたときは、入れ方を知らせる（勝手には入れない）
  記録：out/update/last.json・out/logs/update.log

使い方：
  python3 scripts/self_update.py            # 確かめて、あれば反映（定期実行が使う）
  python3 scripts/self_update.py --check    # 確かめるだけ
  python3 scripts/self_update.py --force    # 動いている仕事があっても反映する（ダッシュボードの「今すぐ更新」は使わない）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, notify, write_json  # noqa: E402

LAST = OUT / "update" / "last.json"
PY = sys.executable
SELF_JOB = "update"


def git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def busy() -> list[str]:
    """いま動いている仕事（あれば反映を待つ）"""
    out = []
    for lock in (OUT / "logs").glob("*.lock") if (OUT / "logs").exists() else []:
        if lock.stem == SELF_JOB:
            continue
        if (datetime.now().timestamp() - lock.stat().st_mtime) < 3 * 3600:
            out.append(f"定期実行「{lock.stem}」")
    try:   # ダッシュボードから動かした仕事（ブラウザの入力など）
        import os
        port = os.environ.get("WEBAPP_PORT", "8765")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/busy", timeout=3) as r:
            d = json.loads(r.read() or b"{}")
        out += [f"ダッシュボードの「{x}」" for x in d.get("running", [])]
    except Exception:  # noqa: BLE001  ダッシュボードが止まっている・古い版：数えない
        pass
    return out


def check() -> dict:
    """{branch, local, remote, behind, ahead, error}"""
    br = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not br or br == "HEAD":
        return {"error": "ブランチが決まっていない状態（detached HEAD）なので、自動では取り込みません"}
    f = git("fetch", "--quiet", "origin", br)
    if f.returncode:
        return {"branch": br, "error": f"GitHub から確かめられませんでした（{(f.stderr or f.stdout).strip()[:200]}）"}
    local = git("rev-parse", "HEAD").stdout.strip()
    remote = git("rev-parse", f"origin/{br}").stdout.strip()
    counts = git("rev-list", "--left-right", "--count", f"HEAD...origin/{br}").stdout.split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
    return {"branch": br, "local": local, "remote": remote, "ahead": ahead, "behind": behind}


def apply(force: bool = False) -> int:
    st = check()
    if st.get("error"):
        print(f"－ {st['error']}")
        return 0 if "detached" in st["error"] else 1
    if not st["behind"]:
        print("【結果】新しい更新はありません")
        return 0
    if st["ahead"]:
        msg = "Mac の中に、GitHub に無い変更（コミット）があるので、自動では取り込みません。Claude に見せてください"
        print(f"✕ {msg}")
        _notify_once("ahead", "システムの更新を止めています", msg)
        return 1
    waiting = [] if force else busy()
    if waiting:
        print(f"…… 動いている仕事があるので、反映は次の回にします（{'・'.join(waiting)}）")
        return 0
    old = st["local"]
    log = git("log", "--format=%s", f"{old}..{st['remote']}").stdout.strip().splitlines()
    print(f"▶ 更新を取り込みます（{len(log)} 件）：" + " ／ ".join(log[:5]), flush=True)
    r = git("pull", "--ff-only", "--quiet", "origin", st["branch"], timeout=300)
    if r.returncode:
        msg = "Mac の中で書き換わったファイルと更新がぶつかるため、取り込めませんでした：" + (r.stderr or r.stdout).strip()[:300]
        print(f"✕ {msg}")
        _notify_once("conflict", "システムの更新を取り込めませんでした", msg[:180])
        return 1
    new = git("rev-parse", "HEAD").stdout.strip()
    files = git("diff", "--name-only", old, new).stdout.split()
    print(f"   ○ 取り込みました：{old[:7]} → {new[:7]}（ファイル {len(files)} 個）")
    # 定期実行を登録し直す（この仕事は外す：登録し直すと、動いている自分が止められるため）。ダッシュボードも新しい版で立ち上がる
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    import schedule
    importlib.reload(schedule)
    jobs = [j for j in [*schedule.JOBS, *schedule.SERVICES] if j != SELF_JOB]
    ins = subprocess.run([PY, str(ROOT / "scripts" / "schedule.py"), "install", "--only", ",".join(jobs)],
                         cwd=ROOT, capture_output=True, text=True, timeout=600)
    ok = ins.returncode == 0 and "✕" not in ins.stdout
    print("   ○ 定期実行とダッシュボードを新しい版にしました" if ok else f"   ✕ 定期実行の登録で問題がありました：{ins.stdout[-300:]}{ins.stderr[-200:]}")
    reqs = any(f.endswith("requirements.txt") for f in files)
    write_json(LAST, {"at": datetime.now().isoformat(timespec="seconds"), "from": old, "to": new, "changes": log[:20],
                      "files": len(files), "install_ok": ok, "requirements_changed": reqs})
    body = (log[0] if log else "更新") + (f" ほか {len(log) - 1} 件" if len(log) > 1 else "")
    if reqs:
        body += "。部品の追加があります：ターミナルで python3 -m pip install --user -r requirements.txt"
    notify("システムを更新しました" if ok else "更新の反映で問題がありました", body[:200])
    print(("【結果】更新を反映しました" if ok else "【結果】更新は取り込みましたが、定期実行の登録で問題がありました（ターミナルで python3 scripts/schedule.py install）")
          + ("（部品の追加があります：python3 -m pip install --user -r requirements.txt）" if reqs else ""))
    return 0 if ok else 1


def _notify_once(kind: str, title: str, msg: str) -> None:
    """同じ知らせを何度も出さない（10 分ごとに動くので）"""
    p = OUT / "update" / f"notified_{kind}.txt"
    key = git("rev-parse", "FETCH_HEAD").stdout.strip()
    if p.exists() and p.read_text(encoding="utf-8").strip() == key:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(key, encoding="utf-8")
    notify(title, msg[:180])


def main() -> None:
    ap = argparse.ArgumentParser(description="push された更新を Mac に自動で反映する")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.check:
        st = check()
        print(f"【結果】{st.get('error') or ('新しい更新が ' + str(st['behind']) + ' 件あります' if st['behind'] else '新しい更新はありません')}")
        return
    sys.exit(apply(a.force))


if __name__ == "__main__":
    main()

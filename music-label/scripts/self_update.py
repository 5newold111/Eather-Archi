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
  あわせて、Mac の中でシステムが書き直したファイルのうち、送ってよいもの（アーティストの設定書 templates/artists/*.json）は、
  確かめてから自動で GitHub に送る（決定 109）：
    ・JSON として壊れていない（書いている途中ではない）
    ・足した行に、本名（.env の OWNER_LEGAL_NAME）・Mac のフォルダの場所（/Users/・Mac の利用者名）・鍵らしい文字が無い
    ・引っかかったら送らずに知らせる（何が引っかかったかの種類だけ。中身は出さない）
    ・送る記録は「[auto] 」で始まる。Mac にだけある記録が全部これなら、取り込みと送り直しを自動で続ける（それ以外の記録があれば止めて知らせる）
  記録：out/update/last.json・out/update/sent.json・out/logs/update.log

使い方：
  python3 scripts/self_update.py            # 確かめて、あれば反映（定期実行が使う）
  python3 scripts/self_update.py --check    # 確かめるだけ
  python3 scripts/self_update.py --force    # 動いている仕事があっても反映する（ダッシュボードの「今すぐ更新」は使わない）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_dotenv, notify, write_json  # noqa: E402

load_dotenv()

LAST = OUT / "update" / "last.json"
SENT = OUT / "update" / "sent.json"
AUTO = "[auto] "
SEND_OK = ("music-label/templates/artists/",)   # 自動で送ってよい場所（決定 109）
SECRET = re.compile(r"sk-ant-|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|-----BEGIN|xox[bpa]-[A-Za-z0-9-]{10,}")
PY = sys.executable
SELF_JOB = "update"


def git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def gtop(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """リポジトリのいちばん上で動かす git（ファイルの場所を music-label/… の形で扱うため）"""
    top = git("rev-parse", "--show-toplevel").stdout.strip() or str(ROOT)
    return subprocess.run(["git", *args], cwd=top, capture_output=True, text=True, timeout=timeout)


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


# ---------------------------------------------------------------------------
# Mac で書き直したファイルを送る（決定 109）
def _names() -> list[str]:
    """足した行に入っていてはいけない言葉：本名（.env）と Mac の利用者名（どちらもここには書かない）"""
    out = []
    full = os.environ.get("OWNER_LEGAL_NAME", "").strip()
    if full:
        out += [full, full.replace(" ", "").replace("\u3000", "")]
        out += [t for t in re.split(r"[\s\u3000]+", full) if len(t) >= 2]
    home = Path.home().name
    if len(home) >= 3 and home.lower() not in ("user", "admin", "root", "mac"):
        out.append(home)
    return [x for x in dict.fromkeys(out) if x]


def to_send() -> list[str]:
    """送ってよい場所で、書き直されたファイル"""
    r = gtop("status", "--porcelain", "--untracked-files=all", "--", *SEND_OK)
    return [ln[3:].strip().strip('"') for ln in r.stdout.splitlines() if ln.strip()]


def safe_to_send(files: list[str]) -> tuple[bool, str]:
    """送ってよいか（理由は種類だけ。引っかかった中身は出さない）"""
    top = Path(git("rev-parse", "--show-toplevel").stdout.strip())
    for f in files:
        p = top / f
        if p.exists() and p.suffix == ".json":
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False, f"{Path(f).name} が書きかけ（JSON として読めない）"
    diff = gtop("diff", "-U0", "--", *files).stdout + "".join(
        "".join("+" + ln + "\n" for ln in (top / f).read_text(encoding="utf-8", errors="ignore").splitlines())
        for f in files if f not in gtop("ls-files", "--", f).stdout and (top / f).exists())   # 新しいファイルは全部の行
    added = "\n".join(ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    if "/Users/" in added or "/home/" in added:
        return False, "Mac のフォルダの場所（/Users/…）が入っている"
    low = added.lower()
    if any(n.lower() in low for n in _names()):
        return False, "本名か Mac の利用者名が入っている"
    if SECRET.search(added):
        return False, "鍵（API キーなど）らしい文字が入っている"
    return True, ""


def send_local() -> str:
    """書き直された設定書を確かめて、記録（コミット）する。戻り値：記録した要約／空"""
    files = to_send()
    if not files:
        return ""
    ok, why = safe_to_send(files)
    if not ok:
        print(f"✕ 設定書を送りませんでした：{why}")
        _notify_once("unsafe", "設定書を GitHub に送るのを止めました", f"{why}。Claude に見せてください（中身は送っていません）")
        return ""
    gtop("add", "--", *files)
    names = "・".join(Path(f).stem for f in files[:6]) + (f" ほか {len(files) - 6}" if len(files) > 6 else "")
    msg = f"{AUTO}アーティストの設定書を Mac の最新にした（{names}）"
    r = gtop("commit", "-q", "-m", msg, "--", *files)
    if r.returncode:
        print(f"✕ 設定書を記録できませんでした：{(r.stderr or r.stdout).strip()[:200]}")
        return ""
    print(f"   ○ 設定書を記録しました（{len(files)} 個）：{names}")
    return names


def only_auto(branch: str) -> bool:
    """Mac にだけある記録が、全部システムの自動の記録か"""
    subs = git("log", "--format=%s", f"origin/{branch}..HEAD").stdout.strip().splitlines()
    return bool(subs) and all(x.startswith(AUTO) for x in subs)


def push(branch: str) -> bool:
    r = git("push", "--quiet", "origin", branch, timeout=300)
    if r.returncode:
        print(f"✕ GitHub に送れませんでした（次の回にもう一度）：{(r.stderr or r.stdout).strip()[:200]}")
        _notify_once("push", "設定書を GitHub に送れませんでした", "次の回（10 分後）にもう一度送ります。続くときは Claude に見せてください")
        return False
    return True


def apply(force: bool = False) -> int:
    st = check()
    if st.get("error"):
        print(f"－ {st['error']}")
        return 0 if "detached" in st["error"] else 1
    sent = send_local()   # 設定書が書き直されていれば、先に記録する（決定 109）
    if sent:
        st = check()
    if st["ahead"] and not only_auto(st["branch"]):
        msg = "Mac の中に、GitHub に無い変更（コミット）があるので、自動では取り込みません。Claude に見せてください"
        print(f"✕ {msg}")
        _notify_once("ahead", "システムの更新を止めています", msg)
        return 1
    if not st["behind"]:
        if st["ahead"] and push(st["branch"]):
            _record_sent(st["branch"])
            print("【結果】設定書を GitHub に送りました（新しい更新はありません）")
            return 0
        print("【結果】新しい更新はありません")
        return 0
    waiting = [] if force else busy()
    if waiting:
        print(f"…… 動いている仕事があるので、反映は次の回にします（{'・'.join(waiting)}）")
        return 0
    old = st["local"]
    log = git("log", "--format=%s", f"{old}..{st['remote']}").stdout.strip().splitlines()
    print(f"▶ 更新を取り込みます（{len(log)} 件）：" + " ／ ".join(log[:5]), flush=True)
    # Mac にだけある自動の記録（設定書）は、取り込んだ更新の後ろに付け直す（GitHub に出ていない記録だけなので、共有の履歴は書き換えない）
    r = git("pull", "--rebase" if st["ahead"] else "--ff-only", "--quiet", "origin", st["branch"], timeout=300)
    if r.returncode:
        if st["ahead"]:
            git("rebase", "--abort")
        msg = "Mac の中で書き換わったファイルと更新がぶつかるため、取り込めませんでした：" + (r.stderr or r.stdout).strip()[:300]
        print(f"✕ {msg}")
        _notify_once("conflict", "システムの更新を取り込めませんでした", msg[:180])
        return 1
    if st["ahead"] and push(st["branch"]):
        _record_sent(st["branch"])
        print("   ○ 設定書を GitHub に送りました")
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


def _record_sent(branch: str) -> None:
    write_json(SENT, {"at": datetime.now().isoformat(timespec="seconds"), "branch": branch,
                      "commit": git("rev-parse", "--short", "HEAD").stdout.strip()})


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

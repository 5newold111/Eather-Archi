#!/usr/bin/env python3
"""
診断の記録を Claude に送る（オーナーの指示 2026-10-29。決定 130）

  ブラウザの自動入力（DistroKid・Suno など）がうまく動かないとき、Mac の中の記録を、Claude が見られる場所（GitHub の
  music-label/diagnostics/）に送る。Claude の作業場所からは Gon さんの Mac のブラウザを見られないため。
  ・送るもの：ダッシュボードで動かしたブラウザの自動入力の出力（直近 3 回）・最後のエラー（last_error.txt）・
    覚えた画面の部品（fields.json の DistroKid の分）・システムの版・部品（Playwright）の有無・.env の項目が入っているか（値は送らない）
  ・送らないもの：画面の写真（本名などが写る）・鍵・.env の値
  ・本名・Mac の利用者名・メールアドレス・鍵らしい文字・/Users/… の場所は、伏せ字にしてから送る。送る前に self_update の安全確認も通す

使い方：python3 scripts/send_diag.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_dotenv  # noqa: E402

load_dotenv()
JOBLOGS = OUT / "browser" / "jobs"
DIAG = ROOT / "diagnostics"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
KEYS = re.compile(r"sk-ant-[\w-]+|sk-[\w-]{20,}|ghp_\w{20,}|github_pat_\w+|AKIA[0-9A-Z]{16}|xox[bpa]-[\w-]{10,}|"
                  r"-----BEGIN[\s\S]*?-----END[^-]*-----")


def scrub(text: str) -> str:
    import self_update
    t = re.sub(r"/Users/[^/\s]+", "~", text)
    t = re.sub(r"/home/[^/\s]+", "~", t)
    for n in sorted(self_update._names(), key=len, reverse=True):
        t = re.sub(re.escape(n), "（伏せ字）", t, flags=re.I)
    t = EMAIL.sub("（メール）", t)
    t = KEYS.sub("（鍵）", t)
    t = self_update.SECRET.sub("（鍵）", t)
    return t


def collect() -> str:
    import _common
    parts = [f"# 診断の記録 {datetime.now().isoformat(timespec='seconds')}"]
    try:
        import subprocess
        v = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        v = "?"
    parts.append(f"システムの版：{v}")
    parts.append(f"Playwright の入った Python：{'あり' if _common.python_with('playwright') else 'なし'}")
    owner = os.environ.get("OWNER_LEGAL_NAME", "").strip()
    parts.append("OWNER_LEGAL_NAME：" + ("なし" if not owner else "あり（ローマ字の名 姓）" if re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*( [A-Za-z][A-Za-z.'\-]*)+", owner)
                                        else "あり（ローマ字の「名 姓」になっていない）"))
    for k in ("POSTDOG_CHROME", "POSTDOG_BROWSER_PROFILE", "POSTDOG_BROWSER_EXECUTABLE", "POSTDOG_CHROME_ARGS", "POSTDOG_DISTROKID_URL",
              "POSTDOG_AUTOCONTINUE"):
        parts.append(f"{k}：{'あり' if os.environ.get(k) else 'なし'}")
    logs = sorted(JOBLOGS.glob("*.txt"))[-3:] if JOBLOGS.exists() else []
    if not logs:
        parts.append("\n## ブラウザの自動入力の記録：まだありません（ダッシュボードから自動入力を 1 回動かしてから送ってください）")
    for p in logs:
        parts.append(f"\n## ブラウザの自動入力の記録：{p.name}\n" + p.read_text(encoding="utf-8", errors="ignore")[-30000:])
    err = OUT / "browser" / "last_error.txt"
    if err.exists():
        parts.append(f"\n## 最後のエラー（{datetime.fromtimestamp(err.stat().st_mtime).isoformat(timespec='seconds')}）\n"
                     + err.read_text(encoding="utf-8", errors="ignore")[-8000:])
    learned = OUT / "browser" / "fields.json"
    if learned.exists():
        try:
            d = json.loads(learned.read_text(encoding="utf-8"))
            dk = {k: v for k, v in d.items() if "distrokid" in k.lower()} or d
            parts.append("\n## 覚えた画面の部品（DistroKid）\n" + json.dumps(dk, ensure_ascii=False, indent=1)[:8000])
        except (OSError, ValueError):
            pass
    return scrub("\n".join(parts)) + "\n"


def main() -> None:
    import self_update
    DIAG.mkdir(parents=True, exist_ok=True)
    f = DIAG / f"{datetime.now():%Y%m%d-%H%M%S}_browser.txt"
    f.write_text(collect(), encoding="utf-8")
    for old in sorted(DIAG.glob("*_browser.txt"))[:-5]:   # 古いものは 5 つまで残す
        old.unlink()
    print(f"▶ 診断の記録を作りました（本名・利用者名・メール・鍵は伏せ字）：diagnostics/{f.name}")
    st = self_update.check()
    if st.get("error"):
        print(f"【結果】送れませんでした：{st['error']}")
        sys.exit(1)
    if not self_update.send_local():
        print("【結果】送れませんでした（上の記録を見てください）")
        sys.exit(1)
    st = self_update.check()
    if st["behind"] and self_update.only_auto(st["branch"]):   # GitHub の方が新しければ、自動の記録だけを後ろに付け直す
        r = self_update.git("pull", "--rebase", "--autostash", "--quiet", "origin", st["branch"], timeout=300)
        if r.returncode:
            self_update.git("rebase", "--abort")
            print("【結果】送れませんでした（更新とぶつかりました。10 分後の自動の更新のあとに、もう一度押してください）")
            sys.exit(1)
    if not self_update.push(st["branch"]):
        print("【結果】送れませんでした（GitHub に届きませんでした。少し待ってもう一度）")
        sys.exit(1)
    print("【結果】診断の記録を送りました。Claude に「送った」と伝えてください")


if __name__ == "__main__":
    main()

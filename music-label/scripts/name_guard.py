#!/usr/bin/env python3
"""
GitHub に送る前の「本名・個人の情報」の見張り（決定 180）

  送る予定の変更（git diff --cached）に、次が入っていないかを見る。入っていれば、その行を伏せ字で出して止める（終了コード 1）
    ・オーナーの本名（Mac の music-label/.env の OWNER_LEGAL_NAME。名・姓を別々にも見る）
    ・NAME_GUARD に書いた言葉（カンマ区切り。クラウドの作業場の環境変数などに置く。本名の漢字・ローマ字など）
    ・Mac の利用者の場所（/Users/〈名前〉/…）
    ・メールアドレス（noreply@anthropic.com と example.com などの例は除く）
  この見張り自体には、本名を書かない（書くと、この道具を通して本名が GitHub に残るため）

使い方（リポジトリのどこからでも）
  python3 music-label/scripts/name_guard.py            # 送る予定の変更を見る（git add のあと・git commit の前）
  python3 music-label/scripts/name_guard.py --all      # 送る予定でない変更も見る
スキル：.claude/skills/finish-change/SKILL.md
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAFE_MAIL = re.compile(r"noreply@anthropic\.com|@example\.(com|org|net)|@users\.noreply\.github\.com|^git@", re.I)
USERS = re.compile(r"/Users/(?!<|〈|\.\.\.|…|\{|\$|you\b|USER\b|name\b|me\b|etherarchi\b)[A-Za-z][\w.-]*")
MAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def words() -> list[str]:
    out: list[str] = []
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OWNER_LEGAL_NAME="):
                full = line.split("=", 1)[1].strip().strip('"').strip("'")
                out += [full, *full.split()]
    out += [w.strip() for w in os.environ.get("NAME_GUARD", "").split(",")]
    return sorted({w for w in out if len(w) >= 2}, key=len, reverse=True)


def mask(s: str) -> str:
    return s[:1] + "＊" * max(1, len(s) - 1)


def main() -> int:
    if any(x in ("-h", "--help") for x in sys.argv[1:]):
        print(__doc__)
        return 0
    args = ["git", "diff", "--cached"] if "--all" not in sys.argv else ["git", "diff", "HEAD"]
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        print("[エラー] 変更が読めませんでした：" + r.stderr.strip()[:200])
        return 2
    ws = words()
    hits, file = [], ""
    for line in r.stdout.splitlines():
        if line.startswith("+++ "):
            file = line[6:] if line.startswith("+++ b/") else line[4:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        why = []
        for w in ws:
            if re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", body, re.I):
                why.append(f"本名（{mask(w)}）")
        if USERS.search(body):
            why.append(f"Mac の利用者の場所（{mask(USERS.search(body).group(0))}）")
        for m in MAIL.finditer(body):
            if not SAFE_MAIL.search(m.group(0)):
                why.append(f"メールアドレス（{mask(m.group(0))}）")
        if why:
            hits.append(f"{file}：" + "・".join(why))
    note = "" if ws else "（本名の言葉が分かりません：Mac の .env か、環境変数 NAME_GUARD が無いので、場所とメールアドレスだけ見ました）"
    if hits:
        for h in hits[:30]:
            print("   ✕ " + h)
        print(f"【結果】送ってはいけないものが {len(hits)} 行あります。直してから、もう一度{note}")
        return 1
    print(f"【結果】本名・個人の情報は見つかりませんでした{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

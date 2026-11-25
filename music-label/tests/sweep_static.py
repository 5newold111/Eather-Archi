"""全部を試しに流す点検（3）：プログラムを読むだけで見つかる「前に実際に起きた不具合の形」を探す（決定 180）

使い方（music-label で）
  python3 tests/sweep_static.py [Python の場所 …]     # 省くと いまの Python。複数書くと、それぞれの版で読み込めるかも見る

探すもの（どれも、この仕組みで実際に起きたもの）
  1 表示を消す関数が flush= などを受け取れない形（lambda *a: None）… 定期実行の --quiet で、直す物がある日にだけ落ちた
  2 引数の説明文の「%」1 文字 … Python 3.14 から、その道具が起動しなくなった
  3 「pip install --user」で始まる部品の入れ方 … 専用の置き場（.venv）では断られる（_common.pip_install を使う）
  4 Mac 用の台本（*.sh）で、変数のすぐ後ろに全角の文字 … Mac の古い bash が変数名を読み違えて止まった（${VAR} と書く）
  5 その Python の版で読み込めない（文法）・未定義の名前（pyflakes が入っていれば）
スキル：.claude/skills/system-sweep/SKILL.md
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def scan() -> list[str]:
    out = []
    for f in sorted(SCRIPTS.glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"lambda \*\w+\s*:\s*None", line) and "**" not in line.split("lambda", 1)[1].split(":", 1)[0]:
                out.append(f"1 表示を消す関数が flush= などを受け取れない：{f.name}:{i}（lambda *a, **k: None に）")
            for m in re.finditer(r'help=f?"([^"]*)"', line):
                if re.search(r"%(?!\()", m.group(1).replace("%%", "")):
                    out.append(f"2 引数の説明文の % 1 文字（3.14 から起動しない）：{f.name}:{i}（%% と書く）")
            if re.search(r"pip install --user(?! --break)", line) and "pip_install" not in line and "def pip_install" not in line \
                    and not line.lstrip().startswith("#") and f.name != "_common.py":
                out.append(f"3 pip install --user で始まる入れ方（.venv では断られる）：{f.name}:{i}（_common.pip_install を使う）")
    for f in sorted(ROOT.glob("*.sh")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"\$[A-Za-z_][A-Za-z_0-9]*[^\x00-\x7F]", line):
                out.append(f"4 変数のすぐ後ろに全角の文字（Mac の古い bash で止まる）：{f.name}:{i}（${{VAR}} と書く）")
    return out


def compile_with(py: str) -> list[str]:
    code = ("import compileall, sys; ok = compileall.compile_dir(sys.argv[1], quiet=1, force=True, legacy=False); sys.exit(0 if ok else 1)")
    r = subprocess.run([py, "-W", "error::SyntaxWarning", "-c", code, str(SCRIPTS)], capture_output=True, text=True)
    if r.returncode == 0:
        return []
    return [f"5 Python（{py}）で読み込めない：" + (r.stdout + r.stderr).strip().splitlines()[-1][:200]]


def pyflakes_with(py: str) -> list[str]:
    r = subprocess.run([py, "-m", "pyflakes", *map(str, sorted(SCRIPTS.glob("*.py")))], capture_output=True, text=True)
    if "No module named pyflakes" in r.stderr:
        return []
    keep = ("undefined name", "may be undefined", "referenced before assignment", "unable to detect undefined names")
    return [f"5 未定義の名前など：{x.replace(str(ROOT) + '/', '')}" for x in r.stdout.splitlines() if any(k in x for k in keep[:3])]


def main() -> int:
    pys = sys.argv[1:] or [sys.executable]
    found = scan()
    for py in pys:
        found += compile_with(py) + pyflakes_with(py)
    for x in found:
        print("   ✕ " + x)
    print(f"【結果】読むだけの点検：見つかった所 {len(found)} 件（Python：{'・'.join(pys)}）")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

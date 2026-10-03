#!/usr/bin/env python3
"""
API キーを music-label/.env に安全に書き込む（画面に鍵を表示しない。シェルの種類に依存しない）

使い方：
  python3 scripts/set_key.py                 # OPENAI_API_KEY を設定
  python3 scripts/set_key.py ANTHROPIC_API_KEY
  python3 scripts/set_key.py --check         # 入っているかだけ確認（値は表示しない）
"""
from __future__ import annotations
import getpass, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
PREFIX = {"OPENAI_API_KEY": "sk-", "ANTHROPIC_API_KEY": "sk-ant-"}


def read_env() -> list[str]:
    if ENV.exists():
        return ENV.read_text(encoding="utf-8").splitlines()
    if EXAMPLE.exists():
        return EXAMPLE.read_text(encoding="utf-8").splitlines()
    return []


def status() -> None:
    lines = read_env()
    print(f"=== .env の状態（{ENV}）===")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        val = next((l.split("=", 1)[1].strip() for l in lines if l.startswith(name + "=")), None)
        if val is None:
            print(f"  {name:28} 行がありません")
        elif not val:
            print(f"  {name:28} 空です")
        else:
            ok = val.startswith(PREFIX.get(name, ""))
            print(f"  {name:28} 設定済み（{len(val)} 文字、先頭 {val[:3]}…）" + ("" if ok else f" ← 先頭が {PREFIX[name]} ではありません。鍵を貼り間違えていないか確認"))


def main() -> None:
    if "--check" in sys.argv:
        status(); return
    name = next((a for a in sys.argv[1:] if not a.startswith("-")), "OPENAI_API_KEY")
    print(f"=== {name} を .env に書き込みます ===")
    print("  鍵を貼り付けて Enter を押してください（セキュリティのため画面には何も表示されません）")
    key = getpass.getpass("  鍵: ").strip().strip('"').strip("'")
    if not key:
        sys.exit("  [中止] 何も入力されませんでした")
    want = PREFIX.get(name)
    if want and not key.startswith(want):
        print(f"  [注意] 先頭が {want} ではありません（入力 {len(key)} 文字、先頭 {key[:3]}…）。鍵以外を貼っていませんか？")
        if input("  このまま保存しますか？ (y/N): ").strip().lower() != "y":
            sys.exit("  [中止] 保存しませんでした")
    lines = [l for l in read_env() if not l.startswith(name + "=")]
    # 元の位置（空行の直前）に入れたいが、簡単のため末尾に追加
    lines.append(f"{name}={key}")
    ENV.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    try:
        ENV.chmod(0o600)   # 自分だけ読めるようにする
    except OSError:
        pass
    print(f"  保存しました：{ENV}（{len(key)} 文字）")
    status()


if __name__ == "__main__":
    main()

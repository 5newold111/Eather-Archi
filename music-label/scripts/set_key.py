#!/usr/bin/env python3
"""
API キーを music-label/.env に安全に書き込む（画面に鍵を表示しない。シェルの種類に依存しない）

使い方：
  python3 scripts/set_key.py                 # OPENAI_API_KEY を設定
  python3 scripts/set_key.py ANTHROPIC_API_KEY
  python3 scripts/set_key.py --check         # 入っているかだけ確認（値は表示しない）
  python3 scripts/set_key.py --dedupe        # 鍵が繰り返し貼られていたら 1 回分に直す
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


def repeated_unit(val: str) -> str | None:
    """値が同じ文字列の繰り返し（鍵を 2〜3 回貼ってしまった状態）なら、1 回分を返す"""
    for n in (2, 3, 4, 5):
        if len(val) % n == 0:
            unit = val[: len(val) // n]
            if unit * n == val and len(unit) >= 20:
                return unit
    return None


def dedupe(name: str = "OPENAI_API_KEY") -> None:
    lines = read_env()
    val = next((l.split("=", 1)[1].strip() for l in lines if l.startswith(name + "=")), "")
    unit = repeated_unit(val)
    if not unit:
        print(f"  {name} は繰り返しではありません（{len(val)} 文字）。そのままです")
        return
    lines = [l for l in lines if not l.startswith(name + "=")] + [f"{name}={unit}"]
    ENV.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    print(f"  {name} が同じ鍵の {len(val)//len(unit)} 回繰り返しだったので、1 回分（{len(unit)} 文字）に直しました")


def status() -> None:
    lines = read_env()
    print(f"=== .env の状態（{ENV}）===")
    names = [l.split("=", 1)[0].strip() for l in (EXAMPLE.read_text(encoding="utf-8").splitlines() if EXAMPLE.exists() else [])
             if "=" in l and not l.lstrip().startswith("#")]
    names += [l.split("=", 1)[0] for l in lines if "__" in l.split("=", 1)[0] and "=" in l]   # 組・レーベル別の鍵
    for name in dict.fromkeys(names or ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]):
        val = next((l.split("=", 1)[1].strip() for l in lines if l.startswith(name + "=")), None)
        if val is None:
            print(f"  {name:28} 行がありません")
        elif not val:
            print(f"  {name:28} 空です")
        else:
            ok = val.startswith(PREFIX.get(name, ""))
            if name.split("__")[0].endswith(("_URL", "_HOST", "_BUCKET")):   # 秘密でない設定値だけ表示
                print(f"  {name:28} 設定済み（{val}）"); continue
            rep = repeated_unit(val)
            print(f"  {name:28} 設定済み（{len(val)} 文字、先頭 {val[:3]}…）"
                  + ("" if ok else f" ← 先頭が {PREFIX[name]} ではありません。鍵を貼り間違えていないか確認")
                  + (f" ← 同じ鍵が {len(val)//len(rep)} 回繰り返されています。`python3 scripts/set_key.py --dedupe` で直せます" if rep else "")
                  + (" ← 長すぎます（通常 150〜200 文字）。貼り付け内容を確認" if not rep and len(val) > 260 and name in PREFIX else ""))


def main() -> None:
    if "--check" in sys.argv:
        status(); return
    if "--dedupe" in sys.argv:
        dedupe(); status(); return
    name = next((a for a in sys.argv[1:] if not a.startswith("-")), "OPENAI_API_KEY")
    print(f"=== {name} を .env に書き込みます ===")
    print("  鍵を貼り付けて Enter を押してください（セキュリティのため画面には何も表示されません）")
    key = getpass.getpass("  鍵: ").strip().strip('"').strip("'")
    if not key:
        sys.exit("  [中止] 何も入力されませんでした")
    unit = repeated_unit(key)
    if unit:
        print(f"  [自動修正] 同じ鍵が {len(key)//len(unit)} 回貼り付けられていたので 1 回分にします")
        key = unit
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

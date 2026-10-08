#!/usr/bin/env python3
"""
選ばれたジャケット候補を、配信用の 3000×3000 JPEG に仕上げる。

  画像生成（OpenAI の画像モデル）は 1024×1024 前後 なので、配信ストアの条件（3000×3000 以上）に合わせて拡大する。
  拡大は ffmpeg の lanczos（細部が崩れにくい拡大方法）。拡大後に機械で確認できる条件を確かめる。

  入力：out/visuals/<組>/cover/<週>/selection_cover.json と cover_NN.png
  出力：out/visuals/<組>/cover/<週>/cover_final.jpg と cover_final.check.json

使い方
  python scripts/finalize_cover.py out/briefs/2026-10-05_light.json
  python scripts/finalize_cover.py --input some.png --out cover_final.jpg
  python scripts/finalize_cover.py --demo          # 仮の画像で動作確認

機械で確かめること：正方形 / 3000×3000 / RGB / 10MB 未満 / 真っ黒・真っ白ではない
人が確かめること（シートに残る）：顔が判別できない・第三者の商標なし・文字は曲名とアーティスト名だけ
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, read_json, step, write_json  # noqa: E402

SIZE = 3000
MAX_BYTES = 10 * 1024 * 1024


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,pix_fmt", "-of", "json", str(path)], capture_output=True, text=True)
    s = json.loads(r.stdout or "{}").get("streams", [{}])
    return s[0] if s else {}


def mean_brightness(path: Path) -> float | None:
    """平均の明るさ（0〜255）。ffmpeg で 1×1 に縮めた 1 画素の値で代用する"""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=1:1,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    return float(r.stdout[0]) if r.stdout else None


def finalize(src: Path, dst: Path) -> dict:
    if not shutil.which("ffmpeg"):
        sys.exit("[エラー] ffmpeg が見つかりません。Mac なら  brew install ffmpeg")
    info = probe(src)
    w, h = int(info.get("width", 0)), int(info.get("height", 0))
    step(f"元の画像：{src.name}（{w}×{h}）")
    checks = {}
    checks["元が正方形"] = {"ok": w == h and w > 0, "detail": f"{w}×{h}"}
    vf = f"scale={SIZE}:{SIZE}:flags=lanczos" if w == h else \
        f"crop='min(iw,ih)':'min(iw,ih)',scale={SIZE}:{SIZE}:flags=lanczos"
    step(f"{SIZE}×{SIZE} に拡大して JPEG（高画質）で書き出しています")
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf + ",format=yuvj444p",
                        "-q:v", "2", str(dst)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("[エラー] 書き出しに失敗しました:\n" + r.stderr[-600:])
    step("確認：大きさ・色・ファイル容量・明るさを測っています")
    out = probe(dst)
    ow, oh = int(out.get("width", 0)), int(out.get("height", 0))
    size = dst.stat().st_size
    bright = mean_brightness(dst)
    checks["3000×3000"] = {"ok": ow == SIZE and oh == SIZE, "detail": f"{ow}×{oh}"}
    checks["色（RGB）"] = {"ok": "gray" not in out.get("pix_fmt", "") and "cmyk" not in out.get("pix_fmt", ""),
                         "detail": out.get("pix_fmt", "")}
    checks["容量"] = {"ok": size < MAX_BYTES, "detail": f"{size / 1024 / 1024:.1f} MB（10 MB 未満）"}
    checks["真っ黒・真っ白でない"] = {"ok": bright is not None and 8 < bright < 247, "detail": f"平均の明るさ {bright}"}
    if w and w < 1024:
        checks["拡大率"] = {"ok": False, "detail": f"元が {w}px と小さく、拡大するとぼやける"}
    ok = all(c["ok"] for c in checks.values() if c["ok"] is not None)
    for k, c in checks.items():
        print(f"   {'○' if c['ok'] else '✕'} {k}: {c['detail']}")
    rep = {"source": str(src), "final": str(dst), "checks": checks, "ok": ok,
           "human_checks": ["顔が判別できない", "第三者のロゴ・商標・キャラクターなし",
                            "文字は曲名とアーティスト名だけ（URL・SNS 名・価格なし）", "拡大して粗さが目立たない"]}
    write_json(dst.with_suffix(".check.json"), rep)
    return rep


def make_demo(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / "cover_01.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "gradients=s=1024x1024:c0=0x1d2b3a:c1=0xf2c38b:duration=1", "-frames:v", "1", str(src)], check=True)
    write_json(folder / "selection_cover.json", {"kind": "cover", "chosen": 1, "by": "demo"})
    return folder


def main() -> None:
    ap = argparse.ArgumentParser(description="ジャケットを 3000×3000 の配信用 JPEG に仕上げる")
    ap.add_argument("brief", nargs="?", help="out/briefs/<週>_<組>.json")
    ap.add_argument("--input")
    ap.add_argument("--out")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.input:
        src = Path(args.input)
        dst = Path(args.out) if args.out else src.with_name("cover_final.jpg")
    else:
        if args.demo:
            folder = make_demo(OUT / "visuals" / "demo" / "cover" / "demo")
        else:
            if not args.brief:
                sys.exit("[エラー] ブリーフか --input を指定してください")
            b = read_json(Path(args.brief))
            folder = OUT / "visuals" / b["artist_slug"] / "cover" / b["week_start"]
        sel = folder / "selection_cover.json"
        if not sel.exists():
            sys.exit(f"[案内] {sel} がありません。generate_visuals.py --kind cover で候補を作り、選んでください"
                     "（OpenAI の残高が入るまでは、候補画像を手で置いて --input で仕上げることもできます）")
        no = read_json(sel)["chosen"]
        src = folder / f"cover_{int(no):02d}.png"
        if not src.exists():
            sys.exit(f"[エラー] 選ばれた候補の画像がありません: {src}")
        dst = Path(args.out) if args.out else folder / "cover_final.jpg"
    rep = finalize(src, dst)
    rel = dst.resolve().relative_to(ROOT) if dst.resolve().is_relative_to(ROOT) else dst
    step(("仕上がりました" if rep["ok"] else "仕上げましたが ✕ があります") + f": {rel}")


if __name__ == "__main__":
    main()

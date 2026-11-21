#!/usr/bin/env python3
"""
Blender のルームツアー（その組の部屋をゆっくり巡る縦長の映像）を書き出し、SNS の縦動画の背景にする。

  1. 設定書の visual.blend_file に .blend ファイルの場所を書く（music-label からの相対パスか、絶対パス）
     例："blend_file": "assets/light_room.blend"
  2. このスクリプトが Blender を画面なしで動かし、1080×1920・30 コマ/秒・既定 10 秒の連番画像を書き出す
     （カメラの動きは .blend ファイルの中のアニメーションをそのまま使う。最初と最後が同じ位置だとつなぎ目が目立たない）
  3. 連番画像を 1 本の動画（loop.mp4）にまとめる → out/roomtour/<組>/v<コンセプトの世代>/loop.mp4
  4. 以後、post_social.py plan が縦動画を作るとき、自動でこの映像を背景に使う（無い組は今まで通りジャケットの背景）

  方針転換で concept_version が上がると、新しい世代のフォルダに作り直す（部屋が変わるため）。

使い方
  python3 scripts/render_roomtour.py --artist light
  python3 scripts/render_roomtour.py --artist light --seconds 8 --engine CYCLES --samples 64
  python3 scripts/render_roomtour.py --all              # blend_file がある組を全部
  python3 scripts/render_roomtour.py --artist light --force   # 作り直す

Blender の場所：BLENDER_PATH（.env）→ PATH の blender → /Applications/Blender.app の順に探す。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_artist, load_dotenv, step  # noqa: E402

load_dotenv()
MAC_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
FPS = 30


def find_blender() -> str | None:
    for c in (os.environ.get("BLENDER_PATH"), shutil.which("blender"), MAC_BLENDER):
        if c and Path(c).exists():
            return c
    return None


def blend_path(artist: dict) -> Path | None:
    v = (artist.get("visual") or {}).get("blend_file")
    if not v:
        return None
    p = Path(v).expanduser()
    return p if p.is_absolute() else ROOT / p


def version_of(artist: dict) -> int:
    h = artist.get("concept_history") or {}
    return int(h.get("concept_version", 1)) if isinstance(h, dict) else 1


def setup_expr(frames: int, engine: str | None, samples: int | None) -> str:
    """Blender の中で動かす設定（縦長・コマ数・書き出し形式）"""
    lines = ["import bpy", "s = bpy.context.scene",
             "s.render.resolution_x = 1080", "s.render.resolution_y = 1920", "s.render.resolution_percentage = 100",
             f"s.render.fps = {FPS}", "s.frame_start = 1", f"s.frame_end = {frames}",
             "s.render.image_settings.file_format = 'PNG'"]
    if engine:
        lines.append(f"s.render.engine = '{engine}'")
    if samples and (engine or "").upper() == "CYCLES":
        lines.append(f"s.cycles.samples = {int(samples)}")
    return "; ".join(lines)


def render(artist: dict, seconds: float, engine: str | None, samples: int | None, force: bool) -> Path | None:
    blend = blend_path(artist)
    if not blend:
        print(f"   － {artist['name']}：設定書の visual.blend_file が空です（.blend ファイルの場所を書くと使えます）")
        return None
    if not blend.exists():
        print(f"   ✕ {artist['name']}：.blend ファイルがありません：{blend}")
        return None
    out_dir = OUT / "roomtour" / artist["slug"] / f"v{version_of(artist)}"
    loop = out_dir / "loop.mp4"
    if loop.exists() and not force and loop.stat().st_mtime > blend.stat().st_mtime:
        print(f"   － {artist['name']}：最新のルームツアーがあります（作り直すときは --force）")
        return loop
    blender = find_blender()
    if not blender:
        print("   ✕ Blender が見つかりません。インストールするか、.env の BLENDER_PATH に場所を書いてください")
        return None
    frames = max(1, int(seconds * FPS))
    frames_dir = out_dir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    step(f"{artist['name']}：Blender で {frames} コマを書き出しています（数分〜数十分）")
    cmd = [blender, "-b", str(blend), "--python-expr", setup_expr(frames, engine, samples),
           "-o", str(frames_dir / "frame_####"), "-a"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    pngs = sorted(frames_dir.glob("frame_*.png"))
    if r.returncode != 0 or not pngs:
        print("   ✕ Blender の書き出しに失敗しました:\n" + (r.stdout + r.stderr)[-800:])
        return None
    step(f"{artist['name']}：{len(pngs)} コマを動画にまとめています")
    first = int(pngs[0].stem.split("_")[-1])
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-start_number", str(first),
                        "-i", str(frames_dir / "frame_%04d.png"),
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p",
                        "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-movflags", "+faststart", str(loop)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("   ✕ 動画にまとめられませんでした:\n" + r.stderr[-600:])
        return None
    shutil.rmtree(frames_dir)
    step(f"{artist['name']}：ルームツアーを書き出しました → {loop.relative_to(ROOT)}（次の縦動画から背景に使います）")
    return loop


def main() -> None:
    ap = argparse.ArgumentParser(description="Blender のルームツアーを書き出す")
    ap.add_argument("--artist")
    ap.add_argument("--label")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--engine", help="BLENDER_EEVEE_NEXT / CYCLES など（省略で .blend の設定のまま）")
    ap.add_argument("--samples", type=int, help="CYCLES のサンプル数（少ないほど速い）")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.all:
        arts = [x for x in all_artists() if (x.get("visual") or {}).get("blend_file")]
        if not arts:
            print("   blend_file が書かれている組はまだありません")
    elif a.artist:
        arts = [load_artist(a.artist, a.label)]
    else:
        ap.error("--artist か --all を指定してください")
    for art in arts:
        render(art, a.seconds, a.engine, a.samples, a.force)


if __name__ == "__main__":
    main()

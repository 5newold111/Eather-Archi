#!/usr/bin/env python3
"""
SNS の縦動画を作る部品（決定 95。決定 94 の続き）

  1 ゆったり動かす：背景（Blender の映像・ジャケット）に、ゆっくり寄る・離れる・横に流れる・上がる動きを付ける。
    動き方は投稿ごとに変える。始めは少し速く動き出して、だんだんゆっくりになる（最初の 1 秒で目を止めるため）。
    曲名は最初の 1 秒でふわっと出す
  2 拍に合わせてつなぐ：組のフォルダの Blender の映像を 3〜6 カット、曲のテンポから計算した小節の切れ目（2・4・8 小節ごと。
    1 カット 4.5 秒以上になる長さ）で切り替える。切り替えは 0.4 秒ふわっと重ねる（CapCut のディゾルブと同じ）
  3 CapCut 用の素材セット（新曲の日の投稿）：サビ 30 秒の音・Blender の映像・ジャケット・拍の目印（字幕ファイル .srt）・
    カット割りの一覧・説明文を capcut/ にまとめる。CapCut で作った動画を capcut/完成.mp4 として置くと、自動の動画の代わりに投稿する

ffmpeg の 2 回の書き出し：① 背景だけの 30 秒（動き・切り替え）→ ② ジャケット・曲名・音を重ねる
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

CLIP_SEC = 30
FPS = 30
FADE = 0.4
MOTIONS = ["push_in", "pan_right", "rise", "pull_out", "pan_left"]
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
]


def find_font() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).exists()), None)


def esc_drawtext(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def duration(f: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                           capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


# ---------------------------------------------------------------------------
# 拍
# ---------------------------------------------------------------------------
def beat_grid(audio: Path, start: float, bpm_hint: float | None = None, total: float = CLIP_SEC) -> dict:
    """切り出す 30 秒の拍：{bpm, first_beat, bar_sec, how}。librosa が無ければ曲の BPM（ブリーフ）、それも無ければ 6 秒ごと"""
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(str(audio), sr=22050, offset=start, duration=total, mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        bpm = float(np.atleast_1d(tempo)[0])
        if bpm_hint and bpm_hint > 0:   # 倍・半分に取り違えたら、ブリーフの BPM に寄せる
            bpm = min((bpm, bpm * 2, bpm / 2), key=lambda b: abs(b - bpm_hint))
        if 50 <= bpm <= 200 and len(beats):
            bt = [round(float(b), 3) for b in beats]
            if len(bt) >= 8:   # 1 小節の長さは、テンポの数字より実際の拍の間隔から（ずれがたまらない）
                bpm_b = 60.0 / float(np.median(np.diff(bt)))
                bpm = min((bpm_b, bpm_b * 2, bpm_b / 2), key=lambda b: abs(b - bpm))
            return {"bpm": round(bpm, 1), "first_beat": bt[0], "bar_sec": 240.0 / bpm, "beats": bt, "how": "音から測った拍"}
    except Exception:  # noqa: BLE001  librosa が無い・読めない
        pass
    if bpm_hint and bpm_hint > 0:
        return {"bpm": float(bpm_hint), "first_beat": 0.0, "bar_sec": 240.0 / float(bpm_hint), "how": "ブリーフの BPM（頭の位置は 0 秒とみる）"}
    return {"bpm": None, "first_beat": 0.0, "bar_sec": 6.0, "how": "テンポが分からないので 6 秒ごと"}


def cut_times(grid: dict, total: float = CLIP_SEC, min_seg: float = 4.5) -> tuple[list[float], int]:
    """カットの切り替え位置（秒）と、何小節ごとか"""
    bars = next((b for b in (1, 2, 4, 8) if b * grid["bar_sec"] >= min_seg), 8) if grid.get("bpm") else 1
    beats, per = grid.get("beats") or [], bars * 4
    if len(beats) > per:   # 測った拍の位置そのもので切り替える（4 拍 × 小節ごと）
        out = [b for b in beats[per::per] if 2.0 < b < total - 2.5]
        if out:
            return out, bars
    step = bars * grid["bar_sec"]
    t, out = grid["first_beat"] + step, []
    while t < total - 2.5:
        if t > 2.0:
            out.append(round(t, 3))
        t += step
    return out, bars


# ---------------------------------------------------------------------------
# 背景（動き・切り替え）
# ---------------------------------------------------------------------------
def _motion(kind: str, frames: int, amp: float) -> str:
    """zoompan の式（入力は 2160×3840。出力 1080×1920）。始めは速く、だんだんゆっくり（ease-out）"""
    e = f"(1-(1-min(in/{frames},1))*(1-min(in/{frames},1)))"
    cx, cy = "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
    z_pan = f"{1 + amp * 1.5:.4f}"
    m = {
        "push_in": (f"1+{amp}*{e}", cx, cy),
        "pull_out": (f"{1 + amp}-{amp}*{e}", cx, cy),
        "pan_right": (z_pan, f"(iw-iw/zoom)*{e}", cy),
        "pan_left": (z_pan, f"(iw-iw/zoom)*(1-{e})", cy),
        "rise": (z_pan, cx, f"(ih-ih/zoom)*(1-{e})"),
    }[kind]
    return f"zoompan=z='{m[0]}':x='{m[1]}':y='{m[2]}':d=1:s=1080x1920:fps={FPS}"


def plan_segments(sources: list[Path], cuts: list[float], seed: int, total: float = CLIP_SEC) -> list[dict]:
    """カットごとの（素材, 素材の中の開始位置, 長さ, 動き）。切り替えの真ん中が拍に来るように長さを足す"""
    bounds = [0.0] + cuts + [total]
    segs = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        ln = (b - a) + (FADE / 2 if i > 0 else 0) + (FADE / 2 if i < len(bounds) - 2 else 0)
        src = sources[(seed + i) % len(sources)]
        dur = duration(src) if src.suffix.lower() not in IMAGE_EXT else 0
        room = max(0.0, dur - ln - 0.1)
        off = round(((seed * 7.3 + i * 11.7) % room) if room > 0 else 0.0, 2)
        segs.append({"src": src, "offset": off, "len": round(ln, 3), "motion": MOTIONS[(seed + i) % len(MOTIONS)],
                     "cut_at": a, "loop": dur and dur < ln + off})
    return segs


def render_background(sources: list[Path], dst: Path, cuts: list[float], seed: int, blur: bool = False,
                      total: float = CLIP_SEC) -> list[dict]:
    """背景だけの 30 秒（1080×1920・30fps・音なし）を書き出す。使ったカットの一覧を返す"""
    segs = plan_segments(sources, cuts, seed, total) if cuts else \
        [{"src": sources[seed % len(sources)], "offset": 0.0, "len": total, "motion": MOTIONS[seed % len(MOTIONS)], "cut_at": 0.0,
          "loop": True}]
    if not cuts and segs[0]["src"].suffix.lower() not in IMAGE_EXT:
        d = duration(segs[0]["src"])
        segs[0]["offset"] = round((seed * 7.3) % max(d - total, 0.01), 2) if d > total + 1 else 0.0
    amp = 0.08 if len(segs) == 1 else 0.05     # 1 カットなら 30 秒で 8%、切り替えるなら 1 カットで 5%（ゆったり）
    inputs, chains = [], []
    for i, s in enumerate(segs):
        if s["src"].suffix.lower() in IMAGE_EXT:
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{s['len']:.3f}", "-i", str(s["src"])]
        else:
            inputs += [*(["-stream_loop", "-1"] if s["loop"] else []), "-ss", f"{s['offset']:.2f}", "-t", f"{s['len']:.3f}", "-i", str(s["src"])]
        frames = max(1, int(s["len"] * FPS))
        chains.append(f"[{i}:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,fps={FPS},"
                      + ("gblur=sigma=60,eq=brightness=-0.08," if blur else "")
                      + f"{_motion(s['motion'], frames, amp)},setsar=1,format=yuv420p,trim=duration={s['len']:.3f},setpts=PTS-STARTPTS[s{i}]")
    last = "s0"
    for i in range(1, len(segs)):   # 拍の位置で 0.4 秒ふわっと重ねて切り替える
        off = segs[i]["cut_at"] - FADE / 2
        chains.append(f"[{last}][s{i}]xfade=transition=fade:duration={FADE}:offset={off:.3f}[x{i}]")
        last = f"x{i}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", f"[{last}]",
           "-t", str(total), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-r", str(FPS), "-an", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("背景の書き出しに失敗しました：" + r.stderr[-600:])
    return segs


def make_clip(master: Path, cover: Path, start: float, title: str, artist: str, dst: Path,
              bgs: list[Path] | Path | None = None, bpm_hint: float | None = None, seed: int = 0) -> dict:
    """縦動画（1080×1920・30 秒）。bgs：Blender の映像（1 本でも複数でも）。無ければジャケットをぼかして動かす。
    戻り値：{grid, cuts, bars, segments}（CapCut の素材セットにも使う）"""
    if not shutil.which("ffmpeg"):
        sys.exit("[エラー] ffmpeg が見つかりません（Mac：brew install ffmpeg）")
    bgs = [bgs] if isinstance(bgs, Path) else list(bgs or [])
    grid = beat_grid(master, start, bpm_hint)
    multi = len(bgs) >= 2 or (len(bgs) == 1 and duration(bgs[0]) >= 3 * 6)   # 映像が 2 本以上か、1 本でも長ければ切り替える
    cuts, bars = cut_times(grid) if multi else ([], 0)
    if multi:
        cuts = cuts[:5]   # 3〜6 カット
    bg = dst.with_name(dst.stem + "_bg.mp4")
    segs = render_background(bgs or [cover], bg, cuts, seed, blur=not bgs)
    font = find_font()
    t, a = esc_drawtext(title), esc_drawtext(artist)
    fade_in = "alpha='min(1,max(0,(t-0.15)/0.6))'"   # 曲名は最初の 1 秒でふわっと出す
    if bgs:   # Blender の映像の上に、左下にジャケットと曲名
        vf = "[1:v]scale=360:360[fg];[2:v][fg]overlay=64:H-h-300"
        if font:
            vf += (f",drawtext=fontfile='{font}':text='{t}':fontcolor=white:fontsize=56:x=64:y=h-250:{fade_in}:shadowcolor=black@0.4:shadowx=2:shadowy=2"
                   f",drawtext=fontfile='{font}':text='{a}':fontcolor=white@0.85:fontsize=40:x=64:y=h-180:{fade_in}:shadowcolor=black@0.4:shadowx=2:shadowy=2")
    else:     # ぼかしたジャケット（ゆっくり動く）の上に、中央にジャケットと曲名
        vf = "[1:v]scale=960:960[fg];[2:v][fg]overlay=(W-w)/2:(H-h)/2-80"
        if font:
            vf += (f",drawtext=fontfile='{font}':text='{t}':fontcolor=white:fontsize=64:x=(w-text_w)/2:y=h/2+480:{fade_in}"
                   f",drawtext=fontfile='{font}':text='{a}':fontcolor=white@0.8:fontsize=44:x=(w-text_w)/2:y=h/2+570:{fade_in}")
    vf += ",format=yuv420p[v]"
    af = f"afade=t=in:st=0:d=1,afade=t=out:st={CLIP_SEC - 2}:d=2"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", str(CLIP_SEC), "-i", str(master),
           "-loop", "1", "-framerate", str(FPS), "-t", str(CLIP_SEC), "-i", str(cover), "-i", str(bg),
           "-filter_complex", vf, "-map", "[v]", "-map", "0:a", "-af", af,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    bg.unlink(missing_ok=True)
    if r.returncode != 0:
        sys.exit("[エラー] 動画の書き出しに失敗しました:\n" + r.stderr[-800:])
    return {"grid": grid, "cuts": cuts, "bars": bars, "segments": [{**s, "src": str(s["src"])} for s in segs]}


# ---------------------------------------------------------------------------
# CapCut 用の素材セット
# ---------------------------------------------------------------------------
def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


COPY_LIMIT = 800 * 1024 * 1024   # 映像の写しは合わせて 800 MB まで（超えたら場所の一覧だけ）


def capcut_kit(folder: Path, master: Path, start: float, cover: Path, bgs: list[Path], info: dict, caption: str) -> Path:
    """CapCut で作り込むための素材を folder/capcut/ にまとめる。CapCut で書き出した動画を 完成.mp4 として置くと、それを投稿する"""
    kit = folder / "capcut"
    (kit / "映像").mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", str(CLIP_SEC), "-i", str(master),
                    "-ar", "44100", str(kit / "音_サビ30秒.wav")], capture_output=True)
    shutil.copy2(cover, kit / f"ジャケット{cover.suffix.lower()}")
    total, listed = 0, []
    for v in bgs:
        sz = v.stat().st_size
        if total + sz <= COPY_LIMIT:
            shutil.copy2(v, kit / "映像" / v.name)
            total += sz
        else:
            listed.append(str(v))
    grid = info.get("grid") or {}
    bar = grid.get("bar_sec") or 6.0
    marks, t = [], grid.get("first_beat", 0.0)
    while t < CLIP_SEC:   # 小節ごとの目印（切り替えに使う位置は ▼）
        marks.append(t)
        t += bar
    cuts = set(round(c, 3) for c in info.get("cuts") or [])
    srt = []
    for i, m in enumerate(marks, 1):
        star = round(m, 3) in cuts or any(abs(m - c) < 0.05 for c in cuts)
        srt.append(f"{i}\n{_srt_time(m)} --> {_srt_time(min(m + 0.5, CLIP_SEC))}\n{'▼ 切り替え' if star else '｜'} {i} 小節目\n")
    (kit / "拍の目印.srt").write_text("\n".join(srt), encoding="utf-8")
    lines = [
        "CapCut で作るときの素材（新曲の日の目玉の投稿）", "",
        f"テンポ：{grid.get('bpm') or '不明'} BPM（{grid.get('how', '')}）・1 小節 {bar:.2f} 秒・最初の拍 {grid.get('first_beat', 0):.2f} 秒",
        f"おすすめの切り替え（{info.get('bars') or '—'} 小節ごと）：" + ("、".join(f"{c:.2f} 秒" for c in info.get("cuts") or []) or "なし（1 カット）"), "",
        "手順",
        "  1 CapCut で新しいプロジェクト（9:16）を作り、「音_サビ30秒.wav」を入れる",
        "  2 「拍の目印.srt」を字幕として読み込む（テキスト → 字幕の読み込み）。▼ の位置で映像を切り替えると拍に合う",
        "  3 「映像」フォルダの Blender の映像を並べ、ゆっくり寄る・横に流れるなどの動き（キーフレーム）を付ける",
        "  4 字幕の目印を消してから、1080×1920・30fps で書き出す（最後の CapCut のロゴの場面は消す）",
        "  5 書き出した動画を「完成.mp4」という名前でこのフォルダに置く → 自動の動画の代わりに、この動画を投稿します",
        "",
        "※ CapCut の利用規約（アップロードした素材の扱い）を確かめてから使ってください",
    ]
    if listed:
        lines += ["", "大きいので写していない映像（この場所から読み込む）：", *("  " + x for x in listed)]
    (kit / "カット割り.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (kit / "説明文.txt").write_text(caption + "\n", encoding="utf-8")
    (kit / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return kit


def capcut_final(video: Path) -> Path | None:
    """CapCut で作った動画（capcut/完成.mp4）があれば、その場所"""
    p = video.parent / "capcut" / "完成.mp4"
    return p if p.exists() and p.stat().st_size > 0 else None


if __name__ == "__main__":   # 試し：python3 scripts/social_video.py 音源 ジャケット 出力.mp4 [映像 …]
    m, c, o, *v = sys.argv[1:]
    print(json.dumps(make_clip(Path(m), Path(c), 45.0, "Test", "Artist", Path(o), [Path(x) for x in v]), ensure_ascii=False, default=str)[:800])

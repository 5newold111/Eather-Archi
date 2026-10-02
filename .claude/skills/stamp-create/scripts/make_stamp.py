#!/usr/bin/env python3
"""スタンプ作成：キー画像（原画）から中間カットを補完して、透過の動くGIFを作る。

使い方:
    python3 make_stamp.py config.json

config.json の例は同じフォルダの example_config.json を参照。
処理の流れ:
  1. 各キー画像の背景（白・薄いグレー・描き込まれた市松模様）を外側から消して透過にする
  2. （文字ありの場合）上部の文字を1文字ずつ切り出し、キャラと分ける
  3. sequence の指示どおりにキー画像と中間カットを並べる
  4. 白フチを付け、全フレーム共通のパレットで減色して GIF に保存
  5. ファイルサイズ・透過・フレーム数・長さを検証し、確認用シートを出力
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageSequence
from scipy import ndimage as nd


def log(msg):
    print(f"[スタンプ作成] {msg}", flush=True)


# ---------- 1. 背景除去 ----------
def remove_background(path, mode="auto", light_min=200, gray_tol=25, color_tol=40):
    """外周とつながった背景色の画素を消す。
    mode="white": 白・薄いグレー・描き込まれた市松模様を背景とみなす
    mode="auto" : 四隅の色が白系なら white、色付き（黄色など）ならその色に近い画素を背景とみなす
    輪郭線で囲まれた内側（目の白など）は外とつながらないので残る。"""
    a = np.array(Image.open(path).convert("RGBA")).astype(int)
    rgb = a[..., :3]
    mx, mn = rgb.max(2), rgb.min(2)
    whiteish = (mn > light_min) & (mx - mn < gray_tol)
    corners = np.array([rgb[2, 2], rgb[2, -3], rgb[-3, 2], rgb[-3, -3]])
    corner_alpha = min(a[2, 2, 3], a[2, -3, 3], a[-3, 2, 3], a[-3, -3, 3])
    if mode == "auto" and corner_alpha > 128 and (corners.min() <= light_min or np.ptp(corners, 1).max() >= gray_tol):
        bgc = np.median(corners, 0)
        log(f"  背景色を自動検出: RGB{tuple(int(v) for v in bgc)}")
        light = (np.abs(rgb - bgc).max(2) < color_tol) | (a[..., 3] < 128)
    else:
        light = whiteish | (a[..., 3] < 128)
    lab, _ = nd.label(light)
    border = np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    bg = np.isin(lab, border[border > 0])
    keep = nd.binary_opening(~bg, iterations=2)
    lab2, n = nd.label(keep)
    if n:
        sizes = nd.sum(keep, lab2, range(1, n + 1))
        keep = np.isin(lab2, 1 + np.where(sizes > 300)[0])
    out = a.copy()
    out[..., 3] = np.where(keep, 255, 0)
    # もともと半透明だった画素は白で埋める（縁を白フチに馴染ませる）
    semi = keep & (a[..., 3] < 128)
    out[semi, :3] = 255
    return out.astype(np.uint8)


# ---------- 2. 文字の切り出し ----------
def find_text(fg, cfg):
    """文字色に近い画素の塊のうち、画像上部（max_y_ratio より上）にあるものを文字とする。
    x方向に重なる塊は同じ1文字（例：「う」の点と本体）としてまとめる。"""
    h, w = fg.shape[:2]
    rgb = fg[..., :3].astype(int)
    alpha = fg[..., 3] > 0
    color = np.array(cfg.get("color", [70, 25, 10]))
    tol = cfg.get("color_tol", 70)
    mask = alpha & (np.abs(rgb - color).max(2) < tol)
    lab, n = nd.label(mask)
    max_y = int(h * cfg.get("max_y_ratio", 0.37))
    comps = []
    for k, s in enumerate(nd.find_objects(lab)):
        if s is None:
            continue
        area = (lab[s] == k + 1).sum()
        if s[0].stop <= max_y and area > cfg.get("min_area", 0.0012) * h * w:
            comps.append([s[1].start, s[1].stop, k + 1])
    comps.sort()
    letters = []  # [x0, x1, [label ids]]
    for x0, x1, k in comps:
        if letters and x0 < letters[-1][1] - 5:
            letters[-1][1] = max(letters[-1][1], x1)
            letters[-1][2].append(k)
        else:
            letters.append([x0, x1, [k]])
    out = []
    for x0, x1, ids in letters:
        m = np.isin(lab, ids)
        ys, xs = np.where(m)
        bb = (xs.min() - 6, ys.min() - 6, xs.max() + 7, ys.max() + 7)
        arr = np.zeros_like(fg)
        arr[m] = fg[m]
        out.append((Image.fromarray(arr).crop(bb), bb))
    text_mask = np.isin(lab, [k for _, _, ids in letters for k in ids])
    return out, text_mask


# ---------- 3. 中間カット ----------
def top_of(img):
    a = np.array(img)[..., 3] > 0
    rows = np.where(a.any(1))[0]
    return rows[0] if len(rows) else 0


def tween(keys, i, j, t, squash):
    """キー i → j の途中（t=0〜1）のカット。
    前半は i を j の位置へ向けてずらし、後半は j を i の位置からずらして使う。
    移動の途中は少しだけ縦に潰して「動いている感じ」を出す。"""
    a, b = keys[i], keys[j]
    dy = top_of(b) - top_of(a)
    src, off = (a, t * dy) if t <= 0.5 else (b, -(1 - t) * dy)
    w, h = src.size
    s = 1 - squash * math.sin(math.pi * t)
    sq = src.resize((w, max(1, int(h * s))), Image.LANCZOS)
    cv = Image.new("RGBA", src.size)
    cv.alpha_composite(sq, (0, int(round(off + h * (1 - s)))))
    return cv


def transform(img, step):
    """1枚の絵からでも動きを作るための変形（キー画像が1枚だけのときに使う）。
    dx, dy : 位置のずらし（元画像のピクセル単位。dy がマイナスで上へ）
    rot    : 回転（度。プラスで反時計回り。下端中央を軸に揺れる）
    sx, sy : 横・縦の伸び縮み（1.0 が等倍。下端を基準に伸び縮みする）"""
    dx, dy = step.get("dx", 0), step.get("dy", 0)
    rot, sx, sy = step.get("rot", 0), step.get("sx", 1.0), step.get("sy", 1.0)
    if not (dx or dy or rot or sx != 1 or sy != 1):
        return img
    w, h = img.size
    a = np.array(img)[..., 3] > 0
    ys, xs = np.where(a)
    pivot = ((xs.min() + xs.max()) / 2, ys.max()) if len(xs) else (w / 2, h)
    out = img
    if sx != 1 or sy != 1:
        r = img.resize((max(1, int(w * sx)), max(1, int(h * sy))), Image.LANCZOS)
        out = Image.new("RGBA", (w, h))
        # paste は画面外にはみ出す座標でも自動で切り取ってくれる
        out.paste(r, (int(round(pivot[0] * (1 - sx))), int(round(pivot[1] * (1 - sy)))))
    if rot:
        out = out.rotate(rot, resample=Image.BICUBIC, center=pivot)
    if dx or dy:
        moved = Image.new("RGBA", (w, h))
        moved.paste(out, (int(round(dx)), int(round(dy))))
        out = moved
    return out


def auto_crop(canvases, margin):
    """全フレームの絵が収まる正方形の範囲（＋余白）を求める。動いても端が切れないようにするため。"""
    union = np.zeros(canvases[0].size[::-1], bool)
    for cv in canvases:
        union |= np.array(cv)[..., 3] > 0
    ys, xs = np.where(union)
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    half = max(xs.max() - xs.min(), ys.max() - ys.min()) / 2 * (1 + margin * 2)
    return [int(cx - half), int(cy - half), int(cx + half), int(cy + half)]


# ---------- 4. 仕上げ（白フチ・減色・保存） ----------
def finish(frame, crop, size, outline_px, alpha_cut=110):
    if crop:
        frame = frame.crop(tuple(crop))
    frame = frame.resize((size, size), Image.LANCZOS)
    a = np.array(frame)
    solid = a[..., 3] > alpha_cut
    out = np.zeros_like(a)
    if outline_px > 0:
        ring = nd.binary_dilation(
            solid, structure=nd.generate_binary_structure(2, 2), iterations=outline_px
        )
        out[ring] = (255, 255, 255, 255)
    alf = a[..., 3:4] / 255.0
    mix = a[..., :3] * alf + 255 * (1 - alf)
    out[solid, :3] = mix[solid].astype(np.uint8)
    out[solid, 3] = 255
    return out


def save_gif(frames, durations, path, colors=255):
    strip = np.concatenate([f[..., :3] for f in frames], 1)
    pal = Image.fromarray(strip).quantize(colors, method=Image.MEDIANCUT, dither=Image.NONE)
    palette = pal.getpalette()[: colors * 3]
    palette += [0, 0, 0] * (256 - colors)
    imgs = []
    for f in frames:
        q = np.array(Image.fromarray(f[..., :3]).quantize(palette=pal, dither=Image.NONE))
        q[f[..., 3] == 0] = 255  # 透明色の番号
        im = Image.fromarray(q.astype(np.uint8), "P")
        im.putpalette(palette)
        imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=durations,
                 loop=0, transparency=255, disposal=2, optimize=False)


def verify(path):
    """GIFを読み直して、サイズ・コマ数・長さ・透過を確かめる。"""
    im = Image.open(path)
    has_index = im.info.get("transparency") is not None  # 1コマ目で「透明色」が登録されているか
    durs, clear = [], []
    for i in range(im.n_frames):
        im.seek(i)
        durs.append(im.info.get("duration", 0))
        clear.append(float((np.array(im.convert("RGBA"))[..., 3] == 0).mean()))
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "kb": round(os.path.getsize(path) / 1024, 1),
        "size": im.size,
        "frames": im.n_frames,
        "durations_ms": durs,
        "total_ms": sum(durs),
        "loop": im.info.get("loop"),  # 0 = 無限ループ
        "has_transparency_index": has_index,
        # 全コマに透明な部分があるか（最小・最大の割合）
        "transparent_all_frames": min(clear) > 0,
        "transparent_ratio_min_max": [round(min(clear), 3), round(max(clear), 3)],
    }


def contact_sheet(frames, path, cols=6, bg=(60, 60, 60)):
    s = frames[0].shape[0]
    rows = math.ceil(len(frames) / cols)
    sheet = Image.new("RGB", (s * cols, s * rows), bg)
    for k, f in enumerate(frames):
        im = Image.fromarray(f)
        sheet.paste(im, ((k % cols) * s, (k // cols) * s), im)
    sheet.save(path)


# ---------- メイン ----------
def main(cfg_path):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(cfg_path))
    rel = lambda p: p if os.path.isabs(p) else os.path.join(base, p)

    log("背景を消して透過にしています…")
    fgs = [remove_background(rel(p), cfg.get("background", "auto")) for p in cfg["inputs"]]

    text_cfg = cfg.get("text", {"enabled": False})
    letters, keys = [], []
    if text_cfg.get("enabled"):
        log("文字を1文字ずつ切り出しています…")
        src = text_cfg.get("source", 0)
        letters, _ = find_text(fgs[src], text_cfg)
        log(f"  見つかった文字数: {len(letters)}")
        for fg in fgs:
            _, tm = find_text(fg, text_cfg)
            near = nd.binary_dilation(tm, iterations=18)
            c = fg.copy()
            c[near, 3] = 0
            keys.append(Image.fromarray(c))
    else:
        keys = [Image.fromarray(f) for f in fgs]

    seq = cfg["sequence"]
    n = len(seq)
    frame_ms = cfg.get("frame_ms", 200)
    squash = cfg.get("tween_squash", 0.03)
    log(f"{n}フレームを組み立てています（中間カットを補完）…")
    canvases, durations = [], []
    for k, step in enumerate(seq):
        if "key" in step:
            body = keys[step["key"]]
        else:
            i, j, t = step["tween"]
            body = tween(keys, i, j, t, squash)
        body = transform(body, step)
        cv = Image.new("RGBA", body.size)
        cv.alpha_composite(body)
        if letters:
            amp = text_cfg.get("pulse", 0.13)
            cycles = text_cfg.get("cycles", 2)
            lag = text_cfg.get("lag", math.pi / 2)
            for li, (im, (x0, y0, x1, y1)) in enumerate(letters):
                s = 1 + amp * math.sin(2 * math.pi * cycles * k / n - li * lag)
                w, h = im.size
                nw, nh = max(1, int(w * s)), max(1, int(h * s))
                cv.alpha_composite(im.resize((nw, nh), Image.LANCZOS), ((x0 + x1) // 2 - nw // 2, y1 - nh))
        canvases.append(cv)
        durations.append(step.get("ms", frame_ms))

    crop = cfg.get("crop") or auto_crop(canvases, cfg.get("margin", 0.05))
    frames = [finish(cv, crop, cfg.get("size", 240), cfg.get("outline_px", 3)) for cv in canvases]

    out = rel(cfg["output"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    limit = cfg.get("max_bytes", 1_000_000)
    log("減色してGIFに保存しています…")
    for colors in (255, 128, 64, 32):
        save_gif(frames, durations, out, colors)
        if os.path.getsize(out) <= limit:
            break
        log(f"  1MBを超えたので {colors} 色からさらに減色します…")
    sheet = os.path.splitext(out)[0] + "_preview.png"
    contact_sheet(frames, sheet)
    log("検証結果:")
    print(json.dumps(verify(out), ensure_ascii=False, indent=2))
    log(f"確認用シート: {sheet}")


if __name__ == "__main__":
    main(sys.argv[1])

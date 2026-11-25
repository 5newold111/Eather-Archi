#!/usr/bin/env python3
"""
デビュー曲 5 枚のジャケット（3000×3000）を、画像生成の鍵なしで作る下書き。

各組の設定書の visual（色・ジャケットの決まり・書体）に沿って、写真の代わりに図形と文字で組む。
画像生成の鍵が入ったら generate_visuals.py の写真版に差し替えてよい。

  python3 debut_kit/2026-10-11/make_covers.py         → debut_kit/2026-10-11/covers/<組>.jpg と見本の小さい版

フォントは Debian の fonts-ibm-plex・fonts-inter・fonts-comic-neue（無ければ apt-get install で入れる）。
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "covers"
S = 3000

PLEX = "/usr/share/fonts/truetype/ibm-plex/"
INTER = "/usr/share/fonts/opentype/inter/"
F = {
    "cond_light": PLEX + "IBMPlexSansCondensed-Light.ttf",
    "cond_xlight": PLEX + "IBMPlexSansCondensed-ExtraLight.ttf",
    "hand": "/usr/share/fonts/opentype/comic-neue/ComicNeue-Light.otf",
    "grot_thin": INTER + "Inter-ExtraLight.otf",
    "grot_light": INTER + "Inter-Light.otf",
    "sign": INTER + "Inter-Medium.otf",
    "mono": PLEX + "IBMPlexMono-Regular.ttf",
    "mono_light": PLEX + "IBMPlexMono-Light.ttf",
    "serif_xlight": PLEX + "IBMPlexSerif-ExtraLight.ttf",
}


def font(key: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(F[key], size)


def hexrgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def tracked(draw: ImageDraw.ImageDraw, xy, text: str, fnt, fill, track: float = 0.3, anchor_right: bool = False) -> int:
    """字間を広げて文字を置く（track は文字の大きさに対する割合）。戻り値は横幅"""
    gap = fnt.size * track
    widths = [draw.textlength(c, font=fnt) for c in text]
    total = sum(widths) + gap * (len(text) - 1)
    x, y = xy
    if anchor_right:
        x -= total
    for c, w in zip(text, widths):
        draw.text((x, y), c, font=fnt, fill=fill)
        x += w + gap
    return int(total)


def handwritten(img: Image.Image, xy, text: str, fnt, fill, seed: int = 7, anchor_right: bool = False) -> None:
    """1 文字ずつ少し傾け・上下させて、手で書いたような揺れを出す"""
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    widths = [d.textlength(c, font=fnt) * 1.02 for c in text]
    x, y = xy
    if anchor_right:
        x -= sum(widths)
    for c, w in zip(text, widths):
        if c != " ":
            pad = fnt.size
            tile = Image.new("RGBA", (int(w + pad * 2), int(fnt.size * 2.2)), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((pad, fnt.size * 0.4), c, font=fnt, fill=fill)
            tile = tile.rotate(rnd.uniform(-4, 4), resample=Image.BICUBIC)
            img.alpha_composite(tile, (int(x - pad), int(y + rnd.uniform(-fnt.size * 0.05, fnt.size * 0.05) - fnt.size * 0.4)))
        x += w


def grain(img: Image.Image, amount: int = 18, seed: int = 1) -> Image.Image:
    """フィルムの粒子（薄く）"""
    import numpy as np
    rng = np.random.default_rng(seed)
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    n = rng.normal(0, amount, (a.shape[0], a.shape[1], 1)).astype(np.int16)
    return Image.fromarray(np.clip(a + n, 0, 255).astype("uint8"))


def glow(size, center, radius, color, strength=1.0) -> Image.Image:
    """光のにじみ（中心から外へ弱くなる）"""
    import numpy as np
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt(((xx - center[0]) / radius[0]) ** 2 + ((yy - center[1]) / radius[1]) ** 2)
    alpha = np.clip(1 - dist, 0, 1) ** 2.2 * 255 * strength
    layer = np.zeros((h, w, 4), dtype="uint8")
    layer[..., 0], layer[..., 1], layer[..., 2] = color
    layer[..., 3] = alpha.astype("uint8")
    return Image.fromarray(layer, "RGBA")


# ---------------------------------------------------------------------------
def declan() -> Image.Image:
    navy, amber, cream, green = hexrgb("#141C2E"), hexrgb("#E8892B"), hexrgb("#EDE3CF"), hexrgb("#1F3B2D")
    img = Image.new("RGBA", (S, S), navy + (255,))
    # 上からナトリウム灯の光が落ちる。下の方は瓶の深い緑にわずかに沈む
    img.alpha_composite(glow((S, S), (S * 0.62, -S * 0.05), (S * 0.75, S * 0.85), amber, 0.55))
    img.alpha_composite(glow((S, S), (S * 0.62, S * 0.02), (S * 0.18, S * 0.16), (255, 196, 120), 0.6))
    img.alpha_composite(glow((S, S), (S * 0.2, S * 1.05), (S * 0.9, S * 0.45), green, 0.55))
    d = ImageDraw.Draw(img)
    # 街灯の半円（ロゴのマークの元）
    cx, cy, r = S * 0.62, S * 0.0, S * 0.07
    d.pieslice((cx - r, cy - r, cx + r, cy + r), 0, 180, fill=(255, 214, 160, 255))
    tracked(d, (S * 0.09, S * 0.09), "DECLAN FAY", font("cond_light", 118), cream + (255,), 0.42)
    tracked(d, (S * 0.09, S * 0.09 + 190), "SIDE B", font("cond_light", 92), (240, 160, 80, 255), 0.5)
    handwritten(img, (S * 0.91, S * 0.885), "2:40 AM  401 W", font("hand", 112), cream + (235,), seed=12, anchor_right=True)
    return grain(img, 14)


def hollowplan() -> Image.Image:
    balsa, grey, amberc, kente, ink = hexrgb("#E8DEC8"), hexrgb("#9A968E"), hexrgb("#D99A3E"), hexrgb("#1E5A3C"), hexrgb("#3B3934")
    img = Image.new("RGBA", (S, S), balsa + (255,))
    d = ImageDraw.Draw(img)
    # 真上から見た平面図。中央の中庭には何も置かない
    o0, o1 = S * 0.2, S * 0.8          # 外壁
    i0, i1 = S * 0.38, S * 0.62        # 中庭
    wall = 9
    d.rectangle((o0, o0, o1, o1), outline=ink, width=wall)
    d.rectangle((i0, i0, i1, i1), outline=ink, width=wall)
    # 部屋の仕切り（中庭から外壁へ）。扉の隙間を残す
    for t in (0.29, 0.5, 0.71):
        p = S * t
        for a, b in ((o0, i0), (i1, o1)):
            gap = (a + b) / 2
            d.line((p, a, p, gap - 42), fill=ink, width=5)
            d.line((p, gap + 42, p, b), fill=ink, width=5)
            d.line((a, p, gap - 42, p), fill=ink, width=5)
            d.line((gap + 42, p, b, p), fill=ink, width=5)
    # 柱の点・寸法の補助線（グレーボードの灰）
    for t in [0.2 + 0.06 * k for k in range(11)]:
        for u in (0.15, 0.85):
            d.ellipse((S * t - 7, S * u - 7, S * t + 7, S * u + 7), fill=grey)
            d.ellipse((S * u - 7, S * t - 7, S * u + 7, S * t + 7), fill=grey)
    d.line((o0, S * 0.12, o1, S * 0.12), fill=grey, width=3)
    for x in (o0, o1):
        d.line((x, S * 0.105, x, S * 0.135), fill=grey, width=3)
    tracked(d, (S * 0.5 - 210, S * 0.085), "36 000", font("grot_thin", 54), grey, 0.3)
    # 夜の街灯の琥珀色の点（廊下）と、ケンテ布の緑 1 点
    for x, y in ((0.245, 0.245), (0.755, 0.245), (0.245, 0.755), (0.755, 0.755)):
        img.alpha_composite(glow((S, S), (S * x, S * y), (S * 0.05, S * 0.05), amberc, 0.75))
    d.ellipse((S * 0.705 - 22, S * 0.335 - 22, S * 0.705 + 22, S * 0.335 + 22), fill=kente)
    # 図面の注記のような文字
    f = font("grot_light", 62)
    tracked(d, (S * 0.2, S * 0.86), "HOLLOWPLAN", f, ink, 0.55)
    tracked(d, (S * 0.2, S * 0.86 + 100), "SERVICES AT THREE", font("grot_thin", 52), ink, 0.5)
    tracked(d, (S * 0.8, S * 0.86 + 100), "1:200", font("grot_thin", 52), ink, 0.3, anchor_right=True)
    return grain(img, 8)


def nao() -> Image.Image:
    import numpy as np
    blue, amber, gold, duck, frost = hexrgb("#1B2856"), hexrgb("#E39B32"), hexrgb("#F2D79A"), hexrgb("#3A2E24"), hexrgb("#F4F3EF")
    horizon = int(S * 0.36)
    # 空：上の群青から地平線の淡い金へ
    t = np.linspace(0, 1, horizon)[:, None]
    sky = (np.array(blue) * (1 - t ** 1.6) + np.array(gold) * t ** 1.6)
    arr = np.zeros((S, S, 3))
    arr[:horizon] = sky[:, None, :]
    arr[horizon:] = np.array(duck) * 0.55
    img = Image.fromarray(arr.astype("uint8")).convert("RGBA")
    # 太陽は道の中央線の真上（真東）
    img.alpha_composite(glow((S, S), (S * 0.5, horizon), (S * 0.32, S * 0.16), gold, 0.9))
    d = ImageDraw.Draw(img)
    r = S * 0.055
    d.pieslice((S * 0.5 - r, horizon - r, S * 0.5 + r, horizon + r), 180, 360, fill=(255, 238, 200, 255))
    # 道：消失点へ集まる路肩の線と、除雪車のアンバーの中央線
    vp = (S * 0.5, horizon)
    d.polygon([vp, (S * -0.4, S), (S * 1.4, S)], fill=duck + (255,))
    for xb in (S * -0.4, S * 1.4):   # 画面の左右の辺から抜ける（左下の文字にかからない）
        d.line((vp, (xb, S)), fill=frost + (200,), width=10)
    for k in range(9):
        a, b = (k / 9) ** 1.8, ((k + 0.45) / 9) ** 1.8
        y0, y1 = horizon + (S - horizon) * a, horizon + (S - horizon) * b
        w0, w1 = 3 + 30 * a, 3 + 30 * b
        d.polygon([(S * 0.5 - w0, y0), (S * 0.5 + w0, y0), (S * 0.5 + w1, y1), (S * 0.5 - w1, y1)], fill=amber + (255,))
    # 文字は左下に小さく、日の出時刻は右上の隅
    tracked(d, (S * 0.07, S * 0.86), "VERNALIGHT", font("sign", 86), frost + (255,), 0.32)
    tracked(d, (S * 0.07, S * 0.86 + 128), "DUE EAST", font("grot_light", 66), frost + (220,), 0.45)
    tracked(d, (S * 0.93, S * 0.06), "SUNRISE 06:08", font("grot_light", 54), frost + (230,), 0.35, anchor_right=True)
    return grain(img, 10)


def soft_cantilever() -> Image.Image:
    white, sign, euc, rust, ink = hexrgb("#F1EEE6"), hexrgb("#1E5B3A"), hexrgb("#8FA39A"), hexrgb("#B5562A"), hexrgb("#2D2F2E")
    img = Image.new("RGBA", (S, S), white + (255,))
    d = ImageDraw.Draw(img)
    # 画面の上半分に、柱 1 本から張り出すサービスエリアのキャノピー
    col_x, top, slab = S * 0.18, S * 0.2, S * 0.07
    d.rectangle((S * 0.06, top, S * 1.02, top + slab), fill=sign)
    d.rectangle((col_x - 45, top + slab, col_x + 45, S * 0.62), fill=sign)
    d.polygon([(col_x + 45, top + slab), (col_x + 300, top + slab), (col_x + 45, top + slab + 260)], fill=sign)
    # 下の道：2 車線（ユーカリの灰緑の破線）
    for y in (S * 0.66, S * 0.74):
        for x in range(0, S, 260):
            d.rectangle((x, y, x + 150, y + 14), fill=euc)
    d.line((0, S * 0.62, S, S * 0.62), fill=ink, width=4)
    d.line((0, S * 0.82, S, S * 0.82), fill=ink, width=4)
    # 3 人を小さく（建築図面の点景のように）
    for k, x in enumerate((0.58, 0.62, 0.665)):
        cx, base = S * x, S * 0.62
        d.ellipse((cx - 16, base - 150, cx + 16, base - 118), fill=ink)
        d.line((cx, base - 118, cx, base - 40), fill=ink, width=10)
        d.line((cx, base - 40, cx - 18 + 8 * k, base), fill=ink, width=8)
        d.line((cx, base - 40, cx + 18 - 4 * k, base), fill=ink, width=8)
    # 図面の寸法線を 1 本だけ（錆びたオレンジ）
    y = top - 110
    d.line((col_x, y, S * 0.94, y), fill=rust, width=5)
    for x in (col_x, S * 0.94):
        d.line((x - 26, y + 26, x + 26, y - 26), fill=rust, width=5)
    tracked(d, (S * 0.56 - 140, y - 92), "12 000", font("mono", 58), rust, 0.15)
    tracked(d, (S * 0.07, S * 0.88), "SOFT CANTILEVER", font("mono", 70), ink, 0.28)
    tracked(d, (S * 0.07, S * 0.88 + 105), "TWO LANES OVER", font("mono_light", 58), ink, 0.32)
    return grain(img, 7)


def tidewool() -> Image.Image:
    cream, slate, tar, moss, gorse = hexrgb("#EEE8DA"), hexrgb("#5B6A72"), hexrgb("#232222"), hexrgb("#4E5B3A"), hexrgb("#E1B238")
    img = Image.new("RGBA", (S, S), cream + (255,))
    # 2 人の手が触れている素材：2 本の糸が上下に交差しながら撚り合わさって 1 本になる
    cycles, top, length = 6, S * 0.2, S * 0.58

    def pts(phase: float, t0: float = 0.0, t1: float = 1.0):
        out = []
        for i in range(int(t0 * 1200), int(t1 * 1200) + 1):
            t = i / 1200
            out.append((S * 0.5 + S * 0.024 * math.sin(t * math.pi * 2 * cycles + phase), top + length * t))
        return out

    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(layer)
    dd.line(pts(math.pi), fill=tar + (255,), width=66, joint="curve")
    dd.line(pts(0.0), fill=slate + (255,), width=72, joint="curve")
    # 半周ごとに、黒い糸が上に来る区間を描き直す（撚りの上下）
    for k in range(cycles * 2):
        if k % 2 == 0:
            a = (k + 0.5) / (cycles * 2) - 0.25 / (cycles * 2)
            b = (k + 0.5) / (cycles * 2) + 0.25 / (cycles * 2)
            dd.line(pts(math.pi, a, b), fill=tar + (255,), width=66, joint="curve")
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1.5)))
    # 撚り始めの結び目と、ハリエニシダの黄の 1 点
    d = ImageDraw.Draw(img)
    d.ellipse((S * 0.5 - 60, S * 0.2 - 90, S * 0.5 + 60, S * 0.2 + 30), outline=moss, width=18)
    d.ellipse((S * 0.62 - 20, S * 0.47 - 20, S * 0.62 + 20, S * 0.47 + 20), fill=gorse)
    # 文字はロゴだけ（細いセリフの小文字・字間広め・少し揺らす）
    f = font("serif_xlight", 150)
    rnd = random.Random(3)
    gap = f.size * 0.38
    word = "tidewool"
    widths = [d.textlength(c, font=f) for c in word]
    x = S * 0.5 - (sum(widths) + gap * (len(word) - 1)) / 2
    for c, w in zip(word, widths):
        d.text((x, S * 0.86 + rnd.uniform(-5, 5)), c, font=f, fill=tar + (255,))
        x += w + gap
    return grain(img, 9)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for slug, fn in (("declan_fay", declan), ("hollowplan", hollowplan), ("nao_easterly", nao),
                     ("soft_cantilever", soft_cantilever), ("tidewool", tidewool)):
        im = fn().convert("RGB")
        assert im.size == (S, S)
        im.save(OUT / f"{slug}.jpg", quality=92, subsampling=0)
        im.resize((600, 600), Image.LANCZOS).save(OUT / f"{slug}_preview.jpg", quality=85)
        print(f"   ○ {slug}.jpg（{S}×{S}）")


if __name__ == "__main__":
    main()

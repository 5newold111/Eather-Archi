#!/usr/bin/env python3
"""
ジャケットに曲名とアーティスト名を重ねる（決定 36）

絵は文字なしで作り（generate_visuals.py）、仕上げ（finalize_cover.py）のあとにこの部品が文字を重ねる。
  ・綴りは DistroKid に登録する曲名・アーティスト名と同じ（_songs.py の曲の情報からそのまま。画像を作る AI には書かせない）
  ・書体と置き方は組ごと（templates/cover_type.json。設定書の「文字の決まり」から）。書体は music-label/fonts（SIL Open Font License）
  ・置く場所は、絵の上下のうち落ち着いている方（決まりで top / bottom を決めた組はそこ）。下の明るさで白か黒かを選び、
    絵がにぎやかなら薄い影を付けて読めるようにする。端から 7% は空ける（サイトの表示で切れないように）
  ・文字なしの版も <名前>_notext.jpg として残す。入れた文字は <名前>.text.json に記録する（曲名を変えたら作り直す目印）
  ・書体に無い文字（日本語など）があるときは、日本語も書ける予備の書体（Mac のヒラギノなど）で入れる。どの書体にも無いときだけ入れずに知らせる
  ・文字は必ず入れる（オーナーの指示 2026-10-27。決定 116）：入っていないジャケットは DistroKid の準備に進めない（publish.py）。
    曲名を変えたときは、文字なしの版から入れ直す（文字が二重にならない）

使い方：
  python3 scripts/cover_text.py <ジャケット.jpg> --artist "TIDEWOOL" --title "The Low Part" --slug tidewool
  python3 scripts/cover_text.py --demo        # 仮の絵で 5 組の見本を作る（out/visuals/_type_demo/）
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, read_json, write_json  # noqa: E402

FONTS = ROOT / "fonts"
SPEC = ROOT / "templates" / "cover_type.json"
SAFE = 0.07


def spec_for(slug: str) -> dict:
    d = read_json(SPEC) if SPEC.exists() else {}
    s = {k: v for k, v in (d.get("default") or {}).items() if not k.startswith("_")}
    s.update({k: v for k, v in (d.get(slug) or {}).items() if not k.startswith("_")})
    return s


def _font(name: str, weight: str, px: int):
    from PIL import ImageFont
    p = FONTS / f"{name}-{weight}.ttf"
    if not p.exists():
        p = next(iter(sorted(FONTS.glob(f"{name}-*.ttf"))), FONTS / "inter-400.ttf")
    return ImageFont.truetype(str(p), px), p


def covers(font_path: Path, text: str) -> bool:
    """書体にすべての文字があるか（無ければ □ になる）"""
    try:
        from fontTools.ttLib import TTFont
        kw = {"fontNumber": 0} if str(font_path).lower().endswith(".ttc") else {}
        cmap = TTFont(str(font_path), **kw)["cmap"].getBestCmap()
        return all(ord(c) in cmap for c in text if not c.isspace())
    except ImportError:
        return all(ord(c) < 0x250 for c in text)   # fonttools が無いとき：ラテン文字だけなら大丈夫とみる
    except Exception:  # noqa: BLE001  読めない書体
        return False


# 日本語なども書ける予備の書体（上から順に。Mac・Linux。fonts/ に置いた書体も使う。決定 116）
FALLBACK = ["/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/noto/NotoSansJP-Bold.ttf"]


def font_for(name: str, weight: str, px: int, text: str):
    """組の書体で書けなければ、書ける予備の書体を使う。(書体, 場所, 予備を使ったか)。どれも無ければ (None, None, True)"""
    from PIL import ImageFont
    f, p = _font(name, weight, px)
    if covers(p, text):
        return f, p, False
    for c in [*sorted(FONTS.glob("*.otf")), *sorted(FONTS.glob("*.ttc")), *sorted(FONTS.glob("*.ttf")), *map(Path, FALLBACK)]:
        if c.exists() and covers(c, text):
            return ImageFont.truetype(str(c), px), c, True
    return None, None, True


def _cased(t: str, case: str) -> str:
    return t.upper() if case == "upper" else t.lower() if case == "lower" else t


def _width(font, text: str, track: float) -> float:
    return sum(font.getlength(c) for c in text) + track * max(0, len(text) - 1)


def _draw_line(draw, xy, text, font, track, fill, jitter_rng=None, size=0):
    x, y = xy
    for c in text:
        if jitter_rng:
            dy = jitter_rng.uniform(-0.03, 0.03) * size
            draw.text((x, y + dy), c, font=font, fill=fill)
        else:
            draw.text((x, y), c, font=font, fill=fill)
        x += font.getlength(c) + track


def _band_stats(img, top: int, bottom: int) -> tuple[float, float]:
    """その帯の明るさ（0〜1）とにぎやかさ（明るさのばらつき）"""
    from PIL import ImageStat
    g = img.crop((0, top, img.width, bottom)).convert("L").resize((300, max(1, (bottom - top) * 300 // img.width)))
    st = ImageStat.Stat(g)
    return st.mean[0] / 255, st.stddev[0]


def apply(path: Path, artist: str, title: str, slug: str, out: Path | None = None) -> dict:
    """path のジャケットに文字を重ねて out（省略時は上書き）に保存。文字なしの版を残す。結果 {ok, reason, place, color, …}"""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    s = spec_for(slug)
    out = out or path
    notext = out.with_name(out.stem + "_notext" + out.suffix)
    base = notext if notext.exists() else path   # 入れ直すときは文字なしの版から（文字が二重にならないように）
    img = Image.open(base).convert("RGB")
    S = img.width
    t_txt, a_txt = _cased(title, s["case"]), _cased(artist, s["case"])
    t_size, a_size = int(S * float(s["size"])), int(S * float(s["artist_size"]))
    tf, tp, t_fb = font_for(s["font"], s["title_weight"], t_size, t_txt)
    af, ap, a_fb = font_for(s["font"], s["artist_weight"], a_size, a_txt)
    if tf is None or af is None:
        return {"ok": False, "reason": "どの書体にも無い文字があるので、文字を入れられませんでした（日本語の書体を fonts/ に置いてください）"}
    room = S * (1 - 2 * SAFE)
    while _width(tf, t_txt, t_size * s["tracking"]) > room and t_size > S * 0.025:   # 長い曲名は小さくして 1 行に収める
        t_size = int(t_size * 0.92)
        tf = ImageFont.truetype(str(tp), t_size) if t_fb else _font(s["font"], s["title_weight"], t_size)[0]
    t_track, a_track = t_size * float(s["tracking"]), a_size * float(s["tracking"])
    gap = int(a_size * 0.6)
    block = a_size + gap + t_size
    m = int(S * SAFE)
    bands = {"top": (m, m + block), "bottom": (S - m - block, S - m)}
    place = s["place"]
    stats = {k: _band_stats(img, max(0, a - gap), min(S, b + gap)) for k, (a, b) in bands.items()}
    if place == "auto":
        place = min(stats, key=lambda k: stats[k][1])
    lum, busy = stats[place]
    ink, halo = ((28, 27, 25), (245, 242, 234)) if lum > 0.55 else ((245, 242, 234), (20, 19, 18))
    y0 = bands[place][0]
    lines = [(a_txt, af, a_track, a_size, y0), (t_txt, tf, t_track, t_size, y0 + a_size + gap)]

    def x_of(text, font, track):
        return (S - _width(font, text, track)) / 2 if s["align"] == "center" else m

    rng = random.Random(f"{slug}|{title}") if s.get("jitter") else None
    if busy > 45:   # にぎやかな絵：文字の下に薄い影（読めるように。四角い帯は敷かない）
        sh = Image.new("L", img.size, 0)
        d = ImageDraw.Draw(sh)
        for text, font, track, size, y in lines:
            _draw_line(d, (x_of(text, font, track), y), text, font, track, 255)
        sh = sh.filter(ImageFilter.GaussianBlur(max(4, t_size // 6)))
        img = Image.composite(Image.new("RGB", img.size, halo), img, sh.point(lambda v: int(v * 0.55)))
    draw = ImageDraw.Draw(img)
    for text, font, track, size, y in lines:
        _draw_line(draw, (x_of(text, font, track), y), text, font, track, ink, rng and random.Random(f"{slug}|{text}"), size)
    if not notext.exists():
        Image.open(path).convert("RGB").save(notext, "JPEG", quality=92)
    img.save(out, "JPEG", quality=92)
    font_name = s["font"] if not (t_fb or a_fb) else f"{s['font']}（日本語などは {Path(str(tp if t_fb else ap)).stem}）"
    res = {"ok": True, "artist": artist, "title": title, "shown": [a_txt, t_txt], "font": font_name, "place": place,
           "ink": "dark" if lum > 0.55 else "light", "shadow": busy > 45}
    write_json(out.with_suffix(".text.json"), res)
    return res


def text_matches(cover: Path, artist: str, title: str) -> bool | None:
    """ジャケットに入れた文字が、今の曲名・アーティスト名と同じか（文字を入れていなければ None）"""
    p = cover.with_suffix(".text.json")
    if not p.exists():
        return None
    r = read_json(p)
    return r.get("artist") == artist and r.get("title") == title


def demo() -> None:
    from PIL import Image, ImageDraw
    out = OUT / "visuals" / "_type_demo"
    out.mkdir(parents=True, exist_ok=True)
    acts = [("declan_fay", "DECLAN FAY", "Side B", (18, 22, 40)), ("hollowplan", "HOLLOWPLAN", "Services at Three", (210, 205, 195)),
            ("nao_easterly", "VERNALIGHT", "Due East", (240, 180, 120)), ("soft_cantilever", "SOFT CANTILEVER", "Two Lanes Over", (90, 140, 170)),
            ("tidewool", "TIDEWOOL", "The Low Part", (60, 70, 62))]
    for slug, artist, title, col in acts:
        img = Image.new("RGB", (3000, 3000), col)
        d = ImageDraw.Draw(img)
        for i in range(12):   # 少し模様のある仮の絵
            d.ellipse((300 + i * 160, 900 + (i % 3) * 200, 900 + i * 160, 1500 + (i % 3) * 200), outline=tuple(min(255, c + 40) for c in col), width=8)
        p = out / f"{slug}.jpg"
        img.save(p, "JPEG", quality=92)
        r = apply(p, artist, title, slug)
        print(f"   {artist}：{r}")
    print(f"▶ 見本：{out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="ジャケットに曲名とアーティスト名を重ねる")
    ap.add_argument("cover", nargs="?", type=Path)
    ap.add_argument("--artist")
    ap.add_argument("--title")
    ap.add_argument("--slug", default="default")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo:
        demo()
        return
    if not (a.cover and a.artist and a.title):
        ap.error("ジャケット・--artist・--title を指定してください")
    print(json.dumps(apply(a.cover, a.artist, a.title, a.slug), ensure_ascii=False))


if __name__ == "__main__":
    main()

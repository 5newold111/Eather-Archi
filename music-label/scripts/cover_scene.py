#!/usr/bin/env python3
"""
ジャケットの「ワンシーン」：曲のテーマから、映画のワンカットのような場面を選ぶ（オーナーの指示 2026-10-27。決定 115）

  ねらい：ジャケットを見ただけで「この曲だ」と分かるようにし、曲を出すほどレパートリーの広さが伝わるようにする。
    例）ドライブの曲 → ハンドルを握る手、回想の曲 → 思い出しているその場面
  ・曲ごとに 6 つの場面（何が写っているか・寄りか引きか・場所・時間と光・小物・人の写り方（顔は写さない）・回想なら思い出らしい写り）
  ・材料：曲名・エピソード（テーマ・いつどこで・見た・聞いた・感じた・気づいた）・歌詞の頭・聴かれる場面・ジャケットの種（cover_prompt_seed）
  ・同じ組の前の曲のジャケットの場面と重ならないようにする（写っているもの・場所・寄り方を変える）
  ・（決定 117 のときは 3 つの場面）場面のうち、1 つ以上はアーティスト本人（管理表の見た目・顔の隠し方のとおり、手・背中・シルエット・首から下など）、
    1 つは曲の物語に出てくる人物（「君」・助手席の人・友人など。同じく顔なし）、残りは自由（人なし・小物の寄りなど）。
    手の寄り・腰から下・遠くのシルエット・肩越しなど、体の写し方と距離も毎回変える（オーナーの指示 2026-10-28。決定 117）
  ・組らしさは色と決まりごとで薄く残す（場面がいちばん前）
  ・6 つの場面にする（オーナーの指示 2026-10-29。決定 122）：曲のテーマとイメージを一番に、曲の中のいちばん印象的なシーンを 6 つ選び、
    1 シーン = ジャケットの案 1 つ。本人（バンドならメンバー。楽器や役割で）を 2 つ以上・曲に出てくる人を 2 つ以上。
    引きの絵だけでなく、手元などのアップ・シーンの一部を切り取った画も入れる（アップ 2 つ以上・切り取り 1 つ以上・中くらい 1 つ以上・引き 1 つ以上）。
    場面ごとに、アーティスト名と曲名の文字のデザイン（書体・置く場所・寄せ方・大きさ・色）も、組の好み（文字の雰囲気・グラフィック）に合わせて決める
  ・6 案は、それぞれ印象のまったく違うデザインにする（オーナーの指示 2026-10-29。決定 123）：案ごとに「見た目の作り」（look：画材・手法・
    色の組み立て・光と色調・構図）を別々にし、文字も書体・置き方・並べ方・色を 6 案で重ねない。Claude が重ねてきても、仕組みが書体と置き方を
    入れ替えて、6 案とも別の書体・別の置き方にする（diversify）
  ・Claude（効き目の低い設定・答えも短い）で 1 曲 数円。ブリーフに保存して、作り直すとき以外はもう一度は呼ばない。鍵が無いときはエピソードから 1 場面を組み立てる
  ・実在の名前・ブランド・建物・文字は入れない

使い方：python3 scripts/cover_scene.py <ブリーフ.json> [--redo]   # 場面を表示（無ければ作る）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, read_json, write_json  # noqa: E402

SCENE_VERSION = 4   # 場面の選び方を変えたら上げる（古い版の場面は選び直す）
N_SCENES = 6
SHOTS = ["extreme close-up", "close-up", "cut-out fragment", "medium", "wide"]

def _system() -> str:
    import cover_text
    fonts = "\n".join(f"  - {k}: {v}" for k, v in cover_text.FONT_STYLES.items() if k in cover_text.fonts_available())
    return f"""You are the art director of a record label. For ONE song, choose the six most memorable, striking scenes of the song - each
like one frame cut out of a film of the song - and each becomes one album-cover idea. A listener must recognise THIS song at first sight.
The song's theme and imagery come FIRST, before the act's look: read the lyrics and the episode and pick the moments the song is about.
Rules:
- Each scene is concrete and photographable in the real world (no surreal or fantasy imagery). Name what is in the frame.
- Ground it in the song: a driving song may show hands gripping the steering wheel at night; a memory may show the remembered scene
  itself (with a memory-like look such as a faded print or light leak), not someone thinking.
- Cast the six scenes: at least two show the artist themself ("who": "artist"; for a band or duo this means the members - say in
  "member" which member or which instrument/role, alone or together); at least two show people from the song's story ("who": "other":
  the "you", a passenger, a friend...); the rest are free ("artist", "other" or "none" for a people-free detail or place).
  The artist's real look is in "artist"; hide faces the way "artist.face_hiding" describes. Never show a face or eyes. Respect "artist.never".
- Vary the shot ("shot"): across the six use at least two "extreme close-up" or "close-up" (hands, an object, fabric, skin with a
  ring, a cup...), at least one "cut-out fragment" (a cropped part of a moment: over-the-shoulder, half a window, a slice of a room),
  at least one "medium" and at least one "wide". Vary angles and distance too.
- Choose scenes that suit the act's art taste in "artist.art_taste" (favourite imagery and mediums), and do not repeat the subjects,
  places or framings listed under "previous_covers".
- No text in the image itself, no logos, no brands, no real people, no real or identifiable buildings, signs or places.
- "type": design the lettering of the artist name and the song title for THIS scene, in the act's taste ("artist.art_taste.type_feel",
  "artist.art_taste.graphic", colours). Put it where the scene leaves calm space and where it belongs in the composition
  (place: top / bottom / middle; align: left / center / right; layout: "stack" = artist name above the title, "title_first" = title
  above the name, "split" = name at the top edge and title at the bottom edge). size = title letter height as a share of the cover
  side (0.035-0.10; small and refined is often best), artist_size likewise (0.02-0.06). tracking = letter spacing (0-0.35; 0 for
  script and handwriting). case: upper / lower / as_is. ink: "light", "dark" or a hex colour taken from the scene. Fonts (pick the
  title font in "font"; "artist_font" may pair a second font or be ""):
{fonts}
- SIX COMPLETELY DIFFERENT IMPRESSIONS: the six covers must not look like a set. Give each its own "look" (in English): a different
  medium or technique (e.g. 35mm film snapshot, large-format still life, risograph print, cut-paper collage, cyanotype, Polaroid,
  flash photograph at night, painted illustration, photocopy zine, long-exposure blur - start from the act's favourite mediums
  and go beyond them), a different colour scheme (warm / cold / monochrome / one vivid accent / pastel / high-contrast), a different
  light and grade, and a different composition (centred, off-centre with big empty space, tight crop, symmetric, layered, framed by
  a foreground). No two looks may share the medium or the colour scheme.
- The lettering must differ just as much: every cover uses a DIFFERENT title font, and no two covers share the same place + align +
  layout. Mix small refined captions with one or two bold title-led designs; mix light, dark and coloured ink. Each still fits the
  act's taste and its own look.
- text_space: in English, where the image must stay calm for the lettering (matching "type").
- song_image: one English sentence of the song's theme and imagery (used first in every cover prompt).
- Write moment / framing / place / time_light / props / human / memory_look / text_space / look in English; why_ja in one short
  Japanese sentence (why this scene is this song); look_ja in a few Japanese words (the impression of this design, e.g. 夜のフラッシュ写真・
  冷たい青); type_ja in one short Japanese sentence (why this lettering)."""


TYPE_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["font", "artist_font", "case", "tracking", "place", "align", "layout", "size", "artist_size", "ink"],
               "properties": {"font": {"type": "string"}, "artist_font": {"type": "string"},
                              "case": {"type": "string", "enum": ["upper", "lower", "as_is"]}, "tracking": {"type": "number"},
                              "place": {"type": "string", "enum": ["top", "bottom", "middle"]},
                              "align": {"type": "string", "enum": ["left", "center", "right"]},
                              "layout": {"type": "string", "enum": ["stack", "title_first", "split"]},
                              "size": {"type": "number"}, "artist_size": {"type": "number"}, "ink": {"type": "string"}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["song_image", "scenes"],
          "properties": {"song_image": {"type": "string"}, "scenes": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["who", "member", "shot", "moment", "framing", "place", "time_light", "props", "human", "memory_look",
                           "look", "text_space", "type", "why_ja", "look_ja", "type_ja"],
              "properties": {"who": {"type": "string", "enum": ["artist", "other", "none"]}, "member": {"type": "string"},
                             "shot": {"type": "string", "enum": SHOTS},
                             "moment": {"type": "string"}, "framing": {"type": "string"}, "place": {"type": "string"},
                             "time_light": {"type": "string"}, "props": {"type": "array", "items": {"type": "string"}},
                             "human": {"type": "string"}, "memory_look": {"type": "string"}, "text_space": {"type": "string"},
                             "look": {"type": "string"}, "look_ja": {"type": "string"},
                             "type": TYPE_SCHEMA, "why_ja": {"type": "string"}, "type_ja": {"type": "string"}}}}}}


def previous(brief: dict, n: int = 8) -> list[str]:
    """同じ組の前の曲で使ったジャケットの場面（重ならないように Claude に見せる）"""
    slug, me = brief.get("artist_slug"), brief.get("week_start")
    out = []
    for p in sorted((OUT / "briefs").glob(f"*_{slug}.json"), reverse=True) if slug else []:
        b = read_json(p)
        if b.get("week_start") == me:
            continue
        for sc in ((b.get("cover_scenes") or {}).get("scenes") or [])[:N_SCENES]:
            out.append(f"[{sc.get('who', '')}/{sc.get('shot', '')}] {sc.get('moment', '')} ({sc.get('framing', '')}, {sc.get('place', '')}; {sc.get('human', '')})")
        if len(out) >= n * 4:
            break
    return out[: n * 4]


def lyrics_head(brief_path: Path | None, brief: dict, lines: int = 16) -> str:
    final = brief_path.with_suffix(".lyrics_final.txt") if brief_path else None
    text = final.read_text(encoding="utf-8") if final and final.exists() else str(brief.get("suno_lyrics") or "")
    return "\n".join([ln for ln in text.splitlines() if ln.strip() and "{{" not in ln][:lines])


# 鍵が無いときと、Claude の案が重なったときに使う、印象の違う 6 通りの作りと文字（決定 123）
LOOKS = [("35mm film snapshot, warm amber grade, off-centre subject with wide empty space", "フィルムの温かい色・余白"),
         ("night flash photograph, hard direct flash, deep black background, one vivid accent colour", "夜のフラッシュ・黒に差し色"),
         ("two-colour risograph print, grainy ink, flat shapes, pastel paper", "リソグラフ・2 色・パステル"),
         ("black-and-white large-format still life, centred and symmetric, soft window light", "白黒・静物・中央"),
         ("faded Polaroid with light leak, cold blue-green cast, tight crop", "ポラロイド・冷たい色・寄り"),
         ("cut-paper collage with photographic fragments, layered, bold primary colours", "コラージュ・原色・重ね")]
TYPE_PRESETS = [
    {"font": "cormorant-garamond", "case": "lower", "tracking": 0.18, "place": "bottom", "align": "center", "layout": "stack", "size": 0.05, "artist_size": 0.028, "ink": "light"},
    {"font": "bebas-neue", "case": "upper", "tracking": 0.06, "place": "top", "align": "left", "layout": "title_first", "size": 0.1, "artist_size": 0.03, "ink": "light"},
    {"font": "special-elite", "case": "lower", "tracking": 0.03, "place": "bottom", "align": "right", "layout": "stack", "size": 0.045, "artist_size": 0.028, "ink": "dark"},
    {"font": "dm-serif-display", "artist_font": "inter", "case": "as_is", "tracking": 0.0, "place": "middle", "align": "center", "layout": "title_first", "size": 0.085, "artist_size": 0.026, "ink": "dark"},
    {"font": "kalam", "case": "as_is", "tracking": 0.0, "place": "top", "align": "right", "layout": "stack", "size": 0.06, "artist_size": 0.03, "ink": "light"},
    {"font": "ibm-plex-mono", "case": "upper", "tracking": 0.22, "place": "top", "align": "center", "layout": "split", "size": 0.035, "artist_size": 0.024, "ink": "light"},
]


MEDIUMS = ["polaroid", "risograph", "collage", "cyanotype", "flash", "black-and-white", "monochrome", "illustration", "painted",
           "painting", "photocopy", "zine", "long-exposure", "large-format", "still life", "film", "digital", "screen print", "woodcut",
           "watercolor", "watercolour", "charcoal", "pencil", "infrared", "lomo", "instant", "double exposure"]


def _medium(look: str) -> str:
    """作りの画材・手法（重なりの判定用）：知っている言葉があればそれ、無ければ最初の 2 語"""
    lk = str(look or "").lower()
    return next((m for m in MEDIUMS if m in lk), " ".join(lk.split()[:2]))


def diversify(scenes: list[dict], slug: str = "") -> list[dict]:
    """6 案の印象を重ねない：同じ書体・同じ置き方（場所＋寄せ＋並べ方）が出たら、まだ使っていない文字のデザインに丸ごと入れ替える。
    同じ画材・手法の作り（look）が出たら、まだ使っていない作りに入れ替える（決定 123）"""
    try:
        import cover_text
        have = set(cover_text.fonts_available())
    except Exception:  # noqa: BLE001
        have = {p["font"] for p in TYPE_PRESETS}
    fonts, spots, mediums = set(), set(), set()
    for i, sc in enumerate(scenes):
        t = dict(sc.get("type") or {})
        spot = (t.get("place"), t.get("align"), t.get("layout"))
        if not t.get("font") or t["font"] not in have or t["font"] in fonts or spot in spots:
            pr = next((p for p in TYPE_PRESETS if p["font"] in have and p["font"] not in fonts
                       and (p["place"], p["align"], p["layout"]) not in spots), None)
            if pr:
                t = dict(pr)   # 書体だけでなく、大きさ・大文字小文字・字間・色も入れ替える（印象を変えるため）
                sc["text_space"] = ""   # 文字の場所が変わったので、空けておく場所は置き方から決め直す
            else:   # 用意した 6 通りも使い切ったとき：使っていない書体だけ替える
                t["font"] = next((f for f in sorted(have) if f not in fonts), t.get("font"))
            spot = (t.get("place"), t.get("align"), t.get("layout"))
        fonts.add(t.get("font"))
        spots.add(spot)
        sc["type"] = t
        md = _medium(sc.get("look", ""))
        if not sc.get("look") or md in mediums:
            en, ja = next(((e, j) for e, j in LOOKS if _medium(e) not in mediums), LOOKS[i % len(LOOKS)])
            sc["look"], sc["look_ja"] = en, ja
            md = _medium(en)
        mediums.add(md)
    return scenes


def fallback(brief: dict) -> list[dict]:
    """鍵が無いとき：エピソードから 6 場面を組み立てる（本人・物語の人・人なしと、寄り・引き、印象の違う作りと文字を入れ替える）"""
    ep = brief.get("episode") or {}
    saw = ep.get("saw") if isinstance(ep.get("saw"), list) else [ep.get("saw")] if ep.get("saw") else []
    saw = [str(x) for x in saw if x] or [str(ep.get("theme") or brief.get("cover_prompt_seed") or "")[:200]]
    plan = [("artist", "extreme close-up", "hands only"), ("other", "medium", "seen from behind"), ("none", "cut-out fragment", "no people"),
            ("artist", "wide", "a small silhouette"), ("other", "close-up", "hands only"), ("none", "close-up", "no people")]
    return diversify([{"who": who, "member": "", "shot": shot, "moment": saw[i % len(saw)], "framing": shot, "place": str(ep.get("when_where") or ""),
                       "time_light": "", "props": saw[(i + 1) % len(saw):][:2], "human": human, "memory_look": "none",
                       "look": LOOKS[i][0], "look_ja": LOOKS[i][1], "text_space": "", "type": dict(TYPE_PRESETS[i]),
                       "why_ja": "エピソードの『見たもの』から", "type_ja": ""}
                      for i, (who, shot, human) in enumerate(plan)], brief.get("artist_slug", ""))


def artist_card(slug: str) -> dict:
    """本人の見た目と顔の隠し方（管理表から。名前・出身地などは渡さない）"""
    from _common import load_artist
    a = load_artist(slug, missing_ok=True) or {}
    life, fc = a.get("life") or {}, (a.get("visual") or {}).get("face_concealment") or {}
    members = [{k: m.get(k) for k in ("role", "part", "gender", "age", "height", "hair_color", "hair_length", "hair_style", "fashion", "instruments") if m.get(k)}
               for m in life.get("members", [])]
    marks = [{k: x.get(k) for k in ("where", "what") if x.get(k)} for x in life.get("marks", []) if x.get("kind") not in (None, "", "なし")]
    t = (a.get("visual") or {}).get("art_taste") or {}
    return {"formation": a.get("formation", ""), "members_look": members, "visible_marks": marks,
            "face_hiding": [fc.get("primary", ""), *(fc.get("secondary") or [])], "never": fc.get("never") or [],
            "art_taste": {k: t.get(k) for k in ("mediums", "imagery", "taste", "graphic", "type_feel", "colors", "from_works", "avoid") if t.get(k)},   # 組の好きな絵柄（決定 118）
            "lettering_now": {k: v for k, v in _type_now(slug).items() if k in ("font", "case", "tracking", "place", "align")}}   # 今の組の文字の決まり（出発点）


def _type_now(slug: str) -> dict:
    try:
        import cover_text
        return cover_text.spec_for(slug)
    except Exception:  # noqa: BLE001
        return {}


def scenes_for(brief: dict, brief_path: Path | None = None, redo: bool = False) -> list[dict]:
    """その曲のジャケットの場面（6 つ）。ブリーフに保存してあればそれを使う（古い選び方の場面は選び直す）"""
    cs = brief.get("cover_scenes") or {}
    have = cs.get("scenes")
    if have and not redo and int(cs.get("version") or 1) >= SCENE_VERSION:
        return have
    import _claude
    if not _claude.available():
        return fallback(brief)
    ep = brief.get("episode") or {}
    sc = brief.get("scene") or {}
    user = json.dumps({
        "song_title": (brief.get("title_candidates") or [brief.get("title", "")])[0],
        "episode": {k: ep.get(k) for k in ("theme", "when_where", "saw", "heard", "thought", "felt", "realized", "story_ja") if ep.get(k)},
        "listening_scene": sc.get("name") or sc.get("key") or "",
        "lyrics_head": lyrics_head(brief_path, brief),
        "cover_seed_from_brief": brief.get("cover_prompt_seed", ""),
        "previous_covers": previous(brief),
        "artist": artist_card(brief.get("artist_slug", "")),
    }, ensure_ascii=False)
    res, _st = _claude.call_json(_system(), user, SCHEMA, effort="low", max_tokens=9000)
    scenes = [s for s in (res or {}).get("scenes") or [] if s.get("moment")][:N_SCENES]
    if not scenes:
        return fallback(brief)
    scenes = diversify(scenes, brief.get("artist_slug", ""))   # 書体・置き方・作りが重なったら入れ替える（決定 123）
    song_image = str((res or {}).get("song_image") or "")
    if brief_path:
        b = read_json(brief_path)
        b["cover_scenes"] = {"made_at": datetime.now().isoformat(timespec="seconds"), "version": SCENE_VERSION,
                             "song_image": song_image, "scenes": scenes}
        write_json(brief_path, b)
    brief["cover_scenes"] = {"version": SCENE_VERSION, "song_image": song_image, "scenes": scenes}
    return scenes


def song_image(brief: dict) -> str:
    """曲のテーマとイメージ（英語の 1 文。指示文のいちばん先に置く）。無ければエピソードから"""
    t = str((brief.get("cover_scenes") or {}).get("song_image") or "").strip()
    if t:
        return t
    ep = brief.get("episode") or {}
    return "; ".join(str(x) for x in (ep.get("theme"), brief.get("cover_prompt_seed")) if x)[:400]


def scene_text(sc: dict) -> str:
    """指示文の「場面」の部分（英語）"""
    t = (f"The cover is one single moment cut out of this song, like a still frame from a film: {sc.get('moment', '')}. "
         f"Shot: {sc.get('shot', '') or 'as framed'}. Framing: {sc.get('framing', '')}. Place: {sc.get('place', '')}. "
         f"Time and light: {sc.get('time_light', '')}. ")
    if sc.get("props"):
        t += "In frame: " + ", ".join(sc["props"]) + ". "
    if sc.get("human"):
        t += f"People: {sc['human']} (never a face, never eyes). "
    if sc.get("memory_look") and str(sc["memory_look"]).lower() not in ("none", "no", ""):
        t += f"It is a memory: {sc['memory_look']}. "
    if sc.get("look"):   # この案だけの見た目の作り（6 案で印象を変える。決定 123）
        t += f"This cover's own look, different from the other covers of this song: {sc['look']}. "
    return t


SHOT_JA = {"extreme close-up": "大きく寄った画", "close-up": "寄った画", "cut-out fragment": "一部を切り取った画", "medium": "中くらい", "wide": "引きの画"}


def main() -> None:
    ap = argparse.ArgumentParser(description="ジャケットのワンシーン")
    ap.add_argument("brief", type=Path)
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()
    b = read_json(a.brief)
    for i, sc in enumerate(scenes_for(b, a.brief, a.redo), 1):
        who = {"artist": "本人", "other": "物語の人物", "none": "人なし"}.get(sc.get("who"), "")
        ty = sc.get("type") or {}
        print(f"{i}. [{who}{('・' + sc['member']) if sc.get('member') else ''}・{SHOT_JA.get(sc.get('shot'), sc.get('shot', ''))}] "
              f"{sc.get('moment')}（{sc.get('place')}・{sc.get('time_light')}）— {sc.get('why_ja')}"
              + (f"\n   作り：{sc.get('look_ja') or sc.get('look', '')}" if sc.get("look") else "")
              + (f"\n   文字：{ty.get('font')}・{ty.get('place')}・{ty.get('align')}・{ty.get('layout')}（{sc.get('type_ja', '')}）" if ty else ""))


if __name__ == "__main__":
    main()

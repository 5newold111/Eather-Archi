#!/usr/bin/env python3
"""
ジャケットの「ワンシーン」：曲のテーマから、映画のワンカットのような場面を選ぶ（オーナーの指示 2026-10-27。決定 115）

  ねらい：ジャケットを見ただけで「この曲だ」と分かるようにし、曲を出すほどレパートリーの広さが伝わるようにする。
    例）ドライブの曲 → ハンドルを握る手、回想の曲 → 思い出しているその場面
  ・曲ごとに 3 つの場面（何が写っているか・寄りか引きか・場所・時間と光・小物・人の写り方（顔は写さない）・回想なら思い出らしい写り）
  ・材料：曲名・エピソード（テーマ・いつどこで・見た・聞いた・感じた・気づいた）・歌詞の頭・聴かれる場面・ジャケットの種（cover_prompt_seed）
  ・同じ組の前の曲のジャケットの場面と重ならないようにする（写っているもの・場所・寄り方を変える）
  ・3 つの場面のうち、1 つ以上はアーティスト本人（管理表の見た目・顔の隠し方のとおり、手・背中・シルエット・首から下など）、
    1 つは曲の物語に出てくる人物（「君」・助手席の人・友人など。同じく顔なし）、残りは自由（人なし・小物の寄りなど）。
    手の寄り・腰から下・遠くのシルエット・肩越しなど、体の写し方と距離も毎回変える（オーナーの指示 2026-10-28。決定 117）
  ・組らしさは色と決まりごとで薄く残す（場面がいちばん前）
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

SCENE_VERSION = 2   # 場面の選び方を変えたら上げる（古い版の場面は選び直す）

SYSTEM = """You are the art director of a record label. For ONE song, choose three different single moments - each like one
frame cut out of a film of the song - that will become the album cover, so that a listener recognises THIS song at first sight
and the act's covers, song after song, show a wide repertoire.
Rules:
- Each moment is concrete and photographable in the real world (no surreal or fantasy imagery). Name what is in the frame.
- Ground it in the song: if the song is about driving, show e.g. hands gripping the steering wheel at night; if it is a memory,
  show the remembered scene itself (with a memory-like look such as a faded print or light leak), not someone thinking.
- Vary the three moments: different subjects, different framing (extreme close-up / medium / wide), different angles.
- People may appear only as hands, a back, a silhouette or a cropped body; never a face or eyes.
- Cast the three moments: at least one shows the artist themself ("who": "artist") - their real look is in "artist", hide the face
  the way "artist.face_hiding" describes; one shows another person from the song's story ("who": "other": the "you", a passenger,
  a friend...); the third is free ("artist", "other" or "none" for a people-free detail or place).
- Vary how the body is shown and how far away: hands in extreme close-up, waist-down, a far silhouette, an over-the-shoulder view...
  Respect "artist.never". Choose moments that suit the act's own art taste in "artist.art_taste" (its favourite imagery and mediums).
- Do not repeat the subjects, places or framings listed under "previous_covers" (they belong to the act's earlier songs).
- No text, no logos, no brands, no real people, no real or identifiable buildings, signs or places.
- Write moment / framing / place / time_light / props / human / memory_look in English; why_ja in one short Japanese sentence."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["scenes"],
          "properties": {"scenes": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["who", "moment", "framing", "place", "time_light", "props", "human", "memory_look", "why_ja"],
              "properties": {"who": {"type": "string", "enum": ["artist", "other", "none"]}, "moment": {"type": "string"}, "framing": {"type": "string"}, "place": {"type": "string"},
                             "time_light": {"type": "string"}, "props": {"type": "array", "items": {"type": "string"}},
                             "human": {"type": "string"}, "memory_look": {"type": "string"}, "why_ja": {"type": "string"}}}}}}


def previous(brief: dict, n: int = 8) -> list[str]:
    """同じ組の前の曲で使ったジャケットの場面（重ならないように Claude に見せる）"""
    slug, me = brief.get("artist_slug"), brief.get("week_start")
    out = []
    for p in sorted((OUT / "briefs").glob(f"*_{slug}.json"), reverse=True) if slug else []:
        b = read_json(p)
        if b.get("week_start") == me:
            continue
        for sc in ((b.get("cover_scenes") or {}).get("scenes") or [])[:3]:
            out.append(f"[{sc.get('who', '')}] {sc.get('moment', '')} ({sc.get('framing', '')}, {sc.get('place', '')}; {sc.get('human', '')})")
        if len(out) >= n * 3:
            break
    return out[: n * 3]


def lyrics_head(brief_path: Path | None, brief: dict, lines: int = 16) -> str:
    final = brief_path.with_suffix(".lyrics_final.txt") if brief_path else None
    text = final.read_text(encoding="utf-8") if final and final.exists() else str(brief.get("suno_lyrics") or "")
    return "\n".join([ln for ln in text.splitlines() if ln.strip() and "{{" not in ln][:lines])


def fallback(brief: dict) -> list[dict]:
    """鍵が無いとき：エピソードから 1 場面を組み立てる"""
    ep = brief.get("episode") or {}
    saw = ep.get("saw") if isinstance(ep.get("saw"), list) else [ep.get("saw")] if ep.get("saw") else []
    return [{"who": "artist", "moment": ", ".join(str(x) for x in saw[:2]) or str(ep.get("theme") or brief.get("cover_prompt_seed") or "")[:200],
             "framing": "close-up", "place": str(ep.get("when_where") or ""), "time_light": "", "props": [str(x) for x in saw[2:4]],
             "human": "hands only", "memory_look": "none", "why_ja": "エピソードの『見たもの』から"}]


def artist_card(slug: str) -> dict:
    """本人の見た目と顔の隠し方（管理表から。名前・出身地などは渡さない）"""
    from _common import load_artist
    a = load_artist(slug, missing_ok=True) or {}
    life, fc = a.get("life") or {}, (a.get("visual") or {}).get("face_concealment") or {}
    members = [{k: m.get(k) for k in ("gender", "age", "height", "hair_color", "hair_length", "hair_style", "fashion", "instruments") if m.get(k)}
               for m in life.get("members", [])]
    marks = [{k: x.get(k) for k in ("where", "what") if x.get(k)} for x in life.get("marks", []) if x.get("kind") not in (None, "", "なし")]
    t = (a.get("visual") or {}).get("art_taste") or {}
    return {"formation": a.get("formation", ""), "members_look": members, "visible_marks": marks,
            "face_hiding": [fc.get("primary", ""), *(fc.get("secondary") or [])], "never": fc.get("never") or [],
            "art_taste": {k: t.get(k) for k in ("mediums", "imagery", "taste", "from_works", "avoid") if t.get(k)}}   # 組の好きな絵柄（決定 118）


def scenes_for(brief: dict, brief_path: Path | None = None, redo: bool = False) -> list[dict]:
    """その曲のジャケットの場面（3 つ）。ブリーフに保存してあればそれを使う（古い選び方の場面は選び直す）"""
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
    res, _st = _claude.call_json(SYSTEM, user, SCHEMA, effort="low", max_tokens=4000)
    scenes = [s for s in (res or {}).get("scenes") or [] if s.get("moment")][:3]
    if not scenes:
        return fallback(brief)
    if brief_path:
        b = read_json(brief_path)
        b["cover_scenes"] = {"made_at": datetime.now().isoformat(timespec="seconds"), "version": SCENE_VERSION, "scenes": scenes}
        write_json(brief_path, b)
    brief["cover_scenes"] = {"version": SCENE_VERSION, "scenes": scenes}
    return scenes


def scene_text(sc: dict) -> str:
    """指示文の「場面」の部分（英語）"""
    t = (f"The cover is one single moment cut out of this song, like a still frame from a film: {sc.get('moment', '')}. "
         f"Framing: {sc.get('framing', '')}. Place: {sc.get('place', '')}. Time and light: {sc.get('time_light', '')}. ")
    if sc.get("props"):
        t += "In frame: " + ", ".join(sc["props"]) + ". "
    if sc.get("human"):
        t += f"People: {sc['human']} (never a face, never eyes). "
    if sc.get("memory_look") and str(sc["memory_look"]).lower() not in ("none", "no", ""):
        t += f"It is a memory: {sc['memory_look']}. "
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description="ジャケットのワンシーン")
    ap.add_argument("brief", type=Path)
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args()
    b = read_json(a.brief)
    for i, sc in enumerate(scenes_for(b, a.brief, a.redo), 1):
        who = {"artist": "本人", "other": "物語の人物", "none": "人なし"}.get(sc.get("who"), "")
        print(f"{i}. [{who}] {sc.get('moment')}（{sc.get('framing')}・{sc.get('place')}・{sc.get('time_light')}）— {sc.get('why_ja')}")


if __name__ == "__main__":
    main()

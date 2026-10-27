#!/usr/bin/env python3
"""
ジャケットの「ワンシーン」：曲のテーマから、映画のワンカットのような場面を選ぶ（オーナーの指示 2026-10-27。決定 115）

  ねらい：ジャケットを見ただけで「この曲だ」と分かるようにし、曲を出すほどレパートリーの広さが伝わるようにする。
    例）ドライブの曲 → ハンドルを握る手、回想の曲 → 思い出しているその場面
  ・曲ごとに 3 つの場面（何が写っているか・寄りか引きか・場所・時間と光・小物・人の写り方（顔は写さない）・回想なら思い出らしい写り）
  ・材料：曲名・エピソード（テーマ・いつどこで・見た・聞いた・感じた・気づいた）・歌詞の頭・聴かれる場面・ジャケットの種（cover_prompt_seed）
  ・同じ組の前の曲のジャケットの場面と重ならないようにする（写っているもの・場所・寄り方を変える）
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

SYSTEM = """You are the art director of a record label. For ONE song, choose three different single moments - each like one
frame cut out of a film of the song - that will become the album cover, so that a listener recognises THIS song at first sight
and the act's covers, song after song, show a wide repertoire.
Rules:
- Each moment is concrete and photographable in the real world (no surreal or fantasy imagery). Name what is in the frame.
- Ground it in the song: if the song is about driving, show e.g. hands gripping the steering wheel at night; if it is a memory,
  show the remembered scene itself (with a memory-like look such as a faded print or light leak), not someone thinking.
- Vary the three moments: different subjects, different framing (extreme close-up / medium / wide), different angles.
- People may appear only as hands, a back, a silhouette or a cropped body; never a face or eyes.
- Do not repeat the subjects, places or framings listed under "previous_covers" (they belong to the act's earlier songs).
- No text, no logos, no brands, no real people, no real or identifiable buildings, signs or places.
- Write moment / framing / place / time_light / props / human / memory_look in English; why_ja in one short Japanese sentence."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["scenes"],
          "properties": {"scenes": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["moment", "framing", "place", "time_light", "props", "human", "memory_look", "why_ja"],
              "properties": {"moment": {"type": "string"}, "framing": {"type": "string"}, "place": {"type": "string"},
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
            out.append(f"{sc.get('moment', '')} ({sc.get('framing', '')}, {sc.get('place', '')})")
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
    return [{"moment": ", ".join(str(x) for x in saw[:2]) or str(ep.get("theme") or brief.get("cover_prompt_seed") or "")[:200],
             "framing": "close-up", "place": str(ep.get("when_where") or ""), "time_light": "", "props": [str(x) for x in saw[2:4]],
             "human": "hands only", "memory_look": "none", "why_ja": "エピソードの『見たもの』から"}]


def scenes_for(brief: dict, brief_path: Path | None = None, redo: bool = False) -> list[dict]:
    """その曲のジャケットの場面（3 つ）。ブリーフに保存してあればそれを使う"""
    have = (brief.get("cover_scenes") or {}).get("scenes")
    if have and not redo:
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
    }, ensure_ascii=False)
    res, _st = _claude.call_json(SYSTEM, user, SCHEMA, effort="low", max_tokens=4000)
    scenes = [s for s in (res or {}).get("scenes") or [] if s.get("moment")][:3]
    if not scenes:
        return fallback(brief)
    if brief_path:
        b = read_json(brief_path)
        b["cover_scenes"] = {"made_at": datetime.now().isoformat(timespec="seconds"), "scenes": scenes}
        write_json(brief_path, b)
    brief["cover_scenes"] = {"scenes": scenes}
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
        print(f"{i}. {sc.get('moment')}（{sc.get('framing')}・{sc.get('place')}・{sc.get('time_light')}）— {sc.get('why_ja')}")


if __name__ == "__main__":
    main()

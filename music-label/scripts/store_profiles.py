#!/usr/bin/env python3
"""
各ストアと SNS のプロフィール文を、設定書から作る（貼るのは人。Spotify などに書き込む公開 API が無いため）。

  作るもの（文字数の上限を守る）
    Spotify for Artists の Bio（1,500 字）／短い紹介（300 字。Apple・Amazon・DistroKid の紹介欄など）
    YouTube チャンネルの説明（1,000 字）／Instagram の自己紹介（150 字）／TikTok の自己紹介（80 字）／日本語の紹介（500 字）
  決まりごと
    ・必ず「EtherArchi の AI を使ったバーチャルアーティスト」であることを書く
    ・実在アーティスト名は既定では入れない（--with-favorites で「好きなアーティスト」として bio にだけ入れる）
    ・話し方はレーベルのトーン（洗練されていて、親しみやすい。大げさにしない）

使い方
  python3 scripts/store_profiles.py                    # 全組
  python3 scripts/store_profiles.py --artist light
  python3 scripts/store_profiles.py --label focus
  出力：out/profiles/<組>.md（コピー用）と .json

ANTHROPIC_API_KEY があれば Claude が書く。無ければ設定書の bio_en から短い版だけ作る。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _claude import available, call_json  # noqa: E402
from _common import OUT, all_artists, load_label, step, write_json  # noqa: E402
from _life import real_names, scrub  # noqa: E402

LIMITS = {"spotify_bio_en": 1500, "short_bio_en": 300, "youtube_description_en": 1000,
          "instagram_bio": 150, "tiktok_bio": 80, "bio_ja": 500}
LABELS_JA = {"spotify_bio_en": "Spotify for Artists の Bio", "short_bio_en": "短い紹介（Apple・Amazon・DistroKid など）",
             "youtube_description_en": "YouTube チャンネルの説明", "instagram_bio": "Instagram の自己紹介",
             "tiktok_bio": "TikTok の自己紹介", "bio_ja": "日本語の紹介"}
DISCLOSURE = "AI-assisted virtual artist from EtherArchi"

SYSTEM = """You write artist profile copy for EtherArchi, a label where AI-generated music meets spatial design.
Every act is a virtual artist defined by a scene of daily life and a space. Voice: refined but approachable, calm,
concrete images of light, material, time and place; never theatrical, no hype words ("revolutionary", "ultimate").
Rules: state clearly that the act is an AI-assisted virtual artist from EtherArchi in every field; do not claim the
act is a human or tours; do not name real artists unless the input explicitly allows favorite artists, and then only
in spotify_bio_en as things the character loves; respect each character limit strictly; bio_ja is natural Japanese.
instagram_bio and tiktok_bio are one or two short lines, no hashtags."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": list(LIMITS),
          "properties": {k: {"type": "string"} for k in LIMITS}}


def card(a: dict, with_fav: bool) -> dict:
    p, prof = a.get("persona") or {}, a.get("profile") or {}
    c = {"name": a["name"], "label": load_label(a["label_slug"]).get("name"), "formation": a.get("formation"),
         "scene": p.get("drive_scene") or p.get("scene"), "story": p.get("story"),
         "formation_story": {k: v for k, v in (p.get("formation_story") or {}).items() if k != "inspired_by_pattern"},
         "origin": p.get("origin"), "genres": (a.get("sound") or {}).get("genres"),
         "visual": {k: (a.get("visual") or {}).get(k) for k in ("space", "light", "palette")},
         "profile": {k: v for k, v in prof.items() if k != "favorite_artists_real" and not k.startswith("_")},
         "existing_bio_en": (a.get("distribution") or {}).get("bio_en")}
    life = a.get("life") or {}
    if life:   # 台帳の人生：生い立ち・デビューの経緯・ファンのつき方・人柄（影響と参考曲の候補は実名なので既定では渡さない）
        act = life.get("act", {})
        c["life"] = scrub({
            "act": {k: act.get(k) for k in ("origin", "base_now", "culture", "strengths", "debut_summary", "fan_growth",
                                             "values", "future", "current_chapter", "expression") if act.get(k)},
            "members": [{k: m.get(k) for k in ("name", "role", "age", "birthplace", "roots", "languages", "personality",
                                               "holidays", "expression", "likes") if m.get(k)} for m in life.get("members", [])],
            "debut_steps": life.get("debut_steps", []),
        }, [] if with_fav else real_names(a))
        if with_fav:
            c["favorite_works_allowed"] = [f"{i.get('title')} / {i.get('author')}" for i in life.get("influences", [])]
    if with_fav:
        c["favorite_artists_allowed"] = prof.get("favorite_artists_real", [])
    return c


def fallback(a: dict) -> dict:
    bio = (a.get("distribution") or {}).get("bio_en") or f"{a['name']} is a virtual artist from EtherArchi."
    if "AI" not in bio:
        bio += f" {a['name']} is an {DISCLOSURE}."
    genres = ", ".join((a.get("sound") or {}).get("genres", [])[:2])
    return {"spotify_bio_en": bio, "short_bio_en": bio[:300],
            "youtube_description_en": f"{bio}\n\n{genres}. New music every week.",
            "instagram_bio": f"{DISCLOSURE}. {genres}."[:150], "tiktok_bio": "AI virtual artist · EtherArchi"[:80],
            "bio_ja": f"{a['name']}：EtherArchi の AI を使ったバーチャルアーティスト。{(a.get('persona') or {}).get('story', '')}"[:500]}


def check(texts: dict, a: dict, with_fav: bool) -> list[str]:
    probs = []
    for k, lim in LIMITS.items():
        if len(texts.get(k, "")) > lim:
            probs.append(f"{LABELS_JA[k]} が {len(texts[k])} 字（上限 {lim}）")
        if k != "bio_ja" and "ai" not in texts.get(k, "").lower():
            probs.append(f"{LABELS_JA[k]} に AI の明記が無い")
    if "AI" not in texts.get("bio_ja", ""):
        probs.append("日本語の紹介に AI の明記が無い")
    for n in sorted(set((a.get("profile") or {}).get("favorite_artists_real", [])) | set(real_names(a))):
        for k, v in texts.items():
            if n and n.lower() in v.lower() and not (with_fav and k == "spotify_bio_en"):
                probs.append(f"{LABELS_JA[k]} に実在アーティスト名（{n}）")
    return probs


def write_md(path: Path, a: dict, texts: dict, probs: list[str], how: str) -> None:
    lines = [f"# {a['name']} のプロフィール文", "", f"作り方：{how}", ""]
    if probs:
        lines += ["**直すこと**", ""] + [f"- ⚠ {p}" for p in probs] + [""]
    for k, lim in LIMITS.items():
        lines += [f"## {LABELS_JA[k]}（{len(texts.get(k, ''))} / {lim} 字）", "", "```", texts.get(k, ""), "```", ""]
    lines += ["## 貼る場所", "", "- Spotify for Artists：Profile → Bio（最初の配信のあとにアーティストページを申請してから）",
              "- Apple Music for Artists：アーティストページを申請（紹介文の欄が無ければ写真だけ）",
              "- YouTube：YouTube Studio → カスタマイズ → 基本情報 → 説明", "- Instagram / TikTok：プロフィールを編集", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="ストアと SNS のプロフィール文を作る")
    ap.add_argument("--artist")
    ap.add_argument("--label")
    ap.add_argument("--with-favorites", action="store_true", help="好きな実在アーティストを Spotify の bio にだけ入れる")
    a = ap.parse_args()
    arts = [x for x in all_artists() if (not a.artist or x["slug"] == a.artist) and (not a.label or x["label_slug"] == a.label)]
    if not arts:
        sys.exit("[エラー] 対象の組が見つかりません")
    out = OUT / "profiles"
    out.mkdir(parents=True, exist_ok=True)
    for art in arts:
        if available():
            step(f"{art['name']}：Claude がプロフィール文を書いています")
            texts, msg = call_json(SYSTEM, json.dumps({"artist": card(art, a.with_favorites), "limits": LIMITS},
                                                      ensure_ascii=False, indent=1), SCHEMA, effort="medium")
            how = "Claude" if texts else f"設定書から（Claude に失敗：{msg}）"
            texts = texts or fallback(art)
        else:
            texts, how = fallback(art), "設定書の bio_en から（ANTHROPIC_API_KEY が無いので短い版だけ）"
        probs = check(texts, art, a.with_favorites)
        write_json(out / f"{art['slug']}.json", texts)
        write_md(out / f"{art['slug']}.md", art, texts, probs, how)
        print(f"   {'⚠' if probs else '○'} {art['name']}：out/profiles/{art['slug']}.md" + (f"（{probs[0]}）" if probs else ""))


if __name__ == "__main__":
    main()

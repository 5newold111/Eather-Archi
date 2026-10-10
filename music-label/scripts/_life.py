"""
管理表から読み込んだ「人生」（設定書の life）を、毎週の曲づくりに使う部品。

  pick_moment   その週の歌のもとになる瞬間を 1 つ選ぶ
                『近況』で「歌にする」が付いた新しい出来事 → 『歌の種』の順に、まだ使っていないものから
                （全部使い切ったら、いちばん前に使った歌の種へ戻る）
  life_context  Claude に渡す人生の要約（実在の名前は伏せる）
  choice_boost  その組が選んだ参考曲の候補に当たる参考曲の重み
  real_names    実在のアーティスト名・曲名（指示文に混ぜてはいけない言葉）
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path


def _norm(s) -> str:
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "", str(s or "").lower())


def moment_id(kind: str, row: dict) -> str:
    if kind == "seed":
        return f"seed:{row.get('id')}"
    h = hashlib.sha1(f"{row.get('date')}|{row.get('event')}".encode()).hexdigest()[:8]
    return f"update:{row.get('date')}:{h}"


def used_moments(briefs_dir: Path, slug: str, before_week: str) -> dict[str, str]:
    """これまでのブリーフで使った瞬間 → 使った週"""
    out: dict[str, str] = {}
    for p in sorted(briefs_dir.glob(f"*_{slug}.json")):
        week = p.name[:10]
        if week >= before_week or not re.match(r"\d{4}-\d{2}-\d{2}_", p.name):
            continue
        try:
            m = (json.loads(p.read_text(encoding="utf-8")).get("life_moment") or {}).get("id")
        except (OSError, json.JSONDecodeError):
            continue
        if m:
            out[m] = week
    return out


def pick_moment(artist: dict, week_monday: date, briefs_dir: Path) -> dict | None:
    life = artist.get("life") or {}
    if not life:
        return None
    used = used_moments(briefs_dir, artist["slug"], week_monday.isoformat())
    names = real_names(artist)
    sketches = {s.get("seed_id"): s for s in life.get("lyric_sketches", []) if s.get("seed_id")}
    ups = [u for u in life.get("updates", []) if u.get("to_song") and str(u.get("date", "")) <= week_monday.isoformat()]
    ups.sort(key=lambda u: str(u.get("date", "")), reverse=True)
    for u in ups:
        mid = moment_id("update", u)
        if mid not in used:
            return scrub({"id": mid, "kind": "近況", "date": u.get("date"), "event": u.get("event"),
                          "feeling": u.get("feeling")}, names)
    seeds = life.get("seeds", [])
    fresh = [s for s in seeds if moment_id("seed", s) not in used]
    if not fresh and seeds:
        fresh = sorted(seeds, key=lambda s: used.get(moment_id("seed", s), ""))[:1]
    if not fresh:
        return None
    s = fresh[0]
    m = {"id": moment_id("seed", s), "kind": "歌の種", **{k: s.get(k) for k in ("period", "feeling", "scene", "pov", "idea", "words")}}
    if s.get("id") in sketches:
        sk = sketches[s["id"]]
        m["owner_sketch"] = {k: sk.get(k) for k in ("title", "chorus", "verse", "meaning_ja") if sk.get(k)}
    return scrub(m, names)


def real_names(artist: dict) -> list[str]:
    """影響・参考曲の候補に書かれた実在の名前（短すぎる語と 1 語の曲名は誤検出が多いので除く）"""
    life = artist.get("life") or {}
    out = set(artist.get("profile", {}).get("favorite_artists_real", []) or [])
    for i in life.get("influences", []):
        if str(i.get("kind", "")).startswith("音楽"):
            out.add(i.get("author") or i.get("title") or "")
    for m in life.get("members", []):
        out.update(m.get("fav_artists") or [])
    for c in life.get("song_choices", []):
        out.add(c.get("artist", ""))
        if len(str(c.get("title", "")).split()) >= 2:
            out.add(c.get("title", ""))
    return sorted(x for x in out if x and len(x) > 3 and x != "（bio のみ）")


def scrub(obj, names: list[str]):
    """文字列の中の実在の名前を伏せる"""
    if isinstance(obj, str):
        for n in names:
            obj = re.sub(re.escape(n), "(a musician)", obj, flags=re.I)
        return obj
    if isinstance(obj, list):
        return [scrub(x, names) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v, names) for k, v in obj.items()}
    return obj


MEMBER_KEYS = ("name", "role", "age", "gender", "roots", "personality", "values", "speech", "catchphrases", "family",
               "holidays", "work_now", "work_music", "technique", "play_strength", "play_preference",
               "likes", "like_words", "dislikes", "dislike_words")   # 楽器の機種名・好きなアーティスト（実名）は渡さない
ACT_KEYS = ("origin", "base_now", "culture", "values", "current_chapter", "landscape_words", "dynamics", "lyricist", "talk")


def life_context(artist: dict, recent: int = 3) -> dict:
    """曲を書くときに参照する人生の要約。影響・参考曲の候補（実名）は渡さない"""
    life = artist.get("life") or {}
    if not life:
        return {}
    act = life.get("act", {})
    ctx = {
        "act": {k: act[k] for k in ACT_KEYS if act.get(k)},
        "members": [{k: m[k] for k in MEMBER_KEYS if m.get(k)} for m in life.get("members", [])],
        "song_worthy_past": [{k: r.get(k) for k in ("years", "place", "event", "feeling")}
                             for r in life.get("timeline", []) if r.get("seed")],
        "recent_updates": sorted(life.get("updates", []), key=lambda u: str(u.get("date", "")))[-recent:],
    }
    return scrub(ctx, real_names(artist))


def choice_boost(artist: dict, title: str, artist_name: str, slot: str) -> float:
    """その組が選んだ参考曲なら重みを上げる（候補の『借りたい要素』に入っている枠ならさらに）"""
    for c in (artist.get("life") or {}).get("song_choices", []):
        if _norm(c.get("title")) == _norm(title) and _norm(c.get("artist")) == _norm(artist_name):
            slots = {"vocal_main" if s == "vocal" else s for s in c.get("slots", [])}
            if slot in slots or (slot.startswith("vocal") and "vocal_main" in slots):
                return 3.0
            return 1.8
    return 1.0

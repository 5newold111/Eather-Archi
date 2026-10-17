#!/usr/bin/env python3
"""
今週の話題曲を Claude に Web で調べさせ、「傾向レポート」「トレンド言語」「話題曲の解析シート（Web で分かる範囲）」を作る。

  1. Claude が Web 検索・閲覧で調べる：Spotify の週間グローバル上位、Billboard Global 200、TikTok で伸びている曲、
     各場面（ドライブ・眠り・朝のコーヒー・集中・歩く／走る）で伸びている曲
  2. 上位 50 曲の歌詞の言語の割合を数え、英語以外で 10% 以上の言語があれば「今週のトレンド言語」にする
  3. 場面ごとの話題曲を、音源なしで作れる範囲の解析シート（世界観・歌詞のテーマ・見た目）にして参考曲フォルダへ置く
     → 音源が無いので、借りられる枠は「世界観」と「歌詞の構造」だけ（usable_slots）。音の枠には使わない
     → 2 週間は「トレンド曲」として少し優先して割り当てられる

  出力
    out/trends/<週>.json        … select_references.py がトレンド言語を自動で読む。supabase_sync.py trends で DB へ
    out/trends/<週>.md          … 人が読む傾向レポート
    out/references/trend_<週>_<番号>.json … 話題曲の解析シート（音源なし）

使い方
  python scripts/fetch_trends.py --week this       # 今週（定期実行は月曜の夜）
  python scripts/fetch_trends.py --week 2026-10-12
  python scripts/fetch_trends.py --week this --refresh   # 作り直す

鍵：ANTHROPIC_API_KEY。無ければ Claude に渡す内容を out/trends/<週>.prompt.md に書き出して終わる。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _claude import available, call_json, call_research, dry_run_file  # noqa: E402
from _common import OUT, ROOT, monday_of, step, write_json  # noqa: E402

THRESHOLD = 0.10   # 上位 50 曲のうち英語以外の言語がこの割合以上なら、トレンド言語にする
SCENES = {"drive": "driving (morning highway, night drive, long distance)", "sleep": "falling asleep / sleep",
          "morning": "morning coffee / slow morning", "focus": "focus, study, work",
          "move": "walking, running, gym"}

RESEARCH_SYSTEM = """You are a music market researcher for a small independent label. Use web search and web fetch to
find CURRENT, verifiable chart data for the week the user names. Prefer primary sources (charts.spotify.com,
billboard.com, the TikTok Creative Center, Apple Music charts). If a chart page cannot be read, say so instead of
guessing, and use a reputable secondary source that reproduces it. Never invent chart positions."""

RESEARCH_PROMPT = """Week (Monday): {week}

Collect, as plain markdown:
1. Spotify Weekly Top Songs Global - the latest available week: ranks 1-50 with title, artist, and the main language of
   the lyrics (ISO 639-1 code; "instrumental" if none).
2. Billboard Global 200 - latest week, ranks 1-50 with the same columns (skip if not accessible).
3. Songs currently rising on TikTok (Creative Center or reputable coverage): up to 15, same columns.
4. For each listening scene below, 2-3 songs (released or rising within the last ~2 months) that are clearly growing in
   playlists or short-form video for that scene, with one line on why:
{scenes}
5. Three to five short notes on sound trends you can see across these lists (tempo, instruments, vocal style, themes).

Give the source URL and the chart date for each list."""

STRUCTURE_SYSTEM = """Convert the research notes into the JSON schema. Copy chart rows faithfully; do not add songs that
are not in the notes. For scene picks, describe lyric theme / arc / tone / world / visual only from what the notes and
general public knowledge support - never quote lyric lines. Tags are short lowercase English words (e.g. night, drive,
synth, acoustic, lofi, uplift)."""

ROW = {"type": "object", "additionalProperties": False, "required": ["rank", "title", "artist", "language"],
       "properties": {"rank": {"type": "integer"}, "title": {"type": "string"}, "artist": {"type": "string"},
                      "language": {"type": "string"}}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["sources", "sound_trends", "scene_picks"],
    "properties": {
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["source", "chart_date", "url", "chart"],
            "properties": {"source": {"type": "string", "enum": ["spotify_global_weekly", "billboard_global_200", "tiktok_rising", "apple_music_global", "other"]},
                           "chart_date": {"type": "string"}, "url": {"type": "string"},
                           "chart": {"type": "array", "items": ROW}}}},
        "sound_trends": {"type": "array", "items": {"type": "string"}},
        "scene_picks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["scene", "title", "artist", "language", "why", "tags", "vocal_sex", "lyric_theme",
                         "emotional_arc", "tone", "point_of_view", "setting", "era_feel", "visual_notes"],
            "properties": {
                "scene": {"type": "string", "enum": list(SCENES)},
                "title": {"type": "string"}, "artist": {"type": "string"}, "language": {"type": "string"},
                "why": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
                "vocal_sex": {"type": "string", "enum": ["female", "male", "mixed", "none", "unknown"]},
                "lyric_theme": {"type": "string"}, "emotional_arc": {"type": "string"}, "tone": {"type": "string"},
                "point_of_view": {"type": "string"}, "setting": {"type": "string"}, "era_feel": {"type": "string"},
                "visual_notes": {"type": "string"}}}},
    },
}


def parse_week(v: str) -> date:
    if v in ("this", "今週"):
        return monday_of(date.today())
    if v in ("next", "来週"):
        return monday_of(date.today()) + timedelta(weeks=1)
    return monday_of(date.fromisoformat(v))


def language_share(chart: list[dict]) -> dict[str, float]:
    langs = [str(r.get("language", "")).lower()[:2] or "?" for r in chart[:50]]
    langs = ["instrumental" if l == "in" else l for l in langs]
    n = len(langs) or 1
    return {k: round(v / n, 3) for k, v in Counter(langs).most_common()}


def pick_trend_language(share: dict[str, float]) -> str | None:
    cands = [(v, k) for k, v in share.items() if k not in ("en", "instrumental", "?") and v >= THRESHOLD]
    return max(cands)[1] if cands else None


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def trend_sheet(p: dict, week: str, n: int) -> dict:
    """音源なしの解析シート。世界観と歌詞の構造の枠だけに使う"""
    return {
        "_注意": "話題曲の Web 調査から作った下書き。音源の解析はしていない（音・ボーカル・フレーズの枠には使わない）",
        "id": f"trend_{week}_{n:02d}",
        "reference": {"title": p["title"], "artist_name": p["artist"], "language": p["language"], "source": "trend",
                      "acquired_from": "Web 上の公開情報のみ（音源は未入手）", "tags": sorted({*p["tags"], p["scene"]})},
        "usable_slots": ["worldview", "lyrics"],
        "trend_week": week,
        "trend_reason": p["why"],
        "lyrics": {"theme": p["lyric_theme"], "emotional_arc": p["emotional_arc"], "tone": p["tone"],
                   "point_of_view": p["point_of_view"], "key_words": []},
        "worldview": {"setting": p["setting"], "era_feel": p["era_feel"], "scene": SCENES[p["scene"]]},
        "visual_web": {"notes": p["visual_notes"]},
        "vocal": {"sex": None if p["vocal_sex"] == "unknown" else p["vocal_sex"], "range": None},
        "analyzed_by": "claude（web）",
        "version": 1,
    }


def report(week: str, data: dict, trend_lang: str | None, primary_share: dict, urls: list[str]) -> str:
    lines = [f"# 今週の傾向レポート（制作週 {week}）", "",
             f"- トレンド言語：**{trend_lang or 'なし'}**（上位 50 曲で英語以外が {int(THRESHOLD * 100)}% 以上の言語）",
             "- 言語の割合：" + "、".join(f"{k} {int(v * 100)}%" for k, v in list(primary_share.items())[:6]), "",
             "## 音の傾向", ""] + [f"- {t}" for t in data.get("sound_trends", [])] + ["", "## 場面ごとの話題曲", ""]
    for p in data.get("scene_picks", []):
        lines.append(f"- **{p['scene']}**：{p['title']} / {p['artist']}（{p['language']}）… {p['why']}")
    for src in data.get("sources", []):
        lines += ["", f"## {src['source']}（{src['chart_date']}）", "", src["url"], "", "| 順位 | 曲 | アーティスト | 言語 |", "|---|---|---|---|"]
        lines += [f"| {r['rank']} | {r['title']} | {r['artist']} | {r['language']} |" for r in src["chart"][:50]]
    if urls:
        lines += ["", "## 参照したページ", ""] + [f"- {u}" for u in urls[:30]]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="今週の話題曲・トレンド言語を調べる")
    ap.add_argument("--week", default="this")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--references", default=str(OUT / "references"), help="話題曲の解析シートを置くフォルダ")
    a = ap.parse_args()
    week = parse_week(a.week).isoformat()
    out = OUT / "trends" / f"{week}.json"
    if out.exists() and not a.refresh:
        step(f"{week} の傾向レポートは作成済みです（作り直すときは --refresh）")
        return
    scenes = "\n".join(f"   - {k}: {v}" for k, v in SCENES.items())
    prompt = RESEARCH_PROMPT.format(week=week, scenes=scenes)
    if not available():
        dry_run_file(OUT / "trends" / f"{week}.prompt.md", RESEARCH_SYSTEM, prompt)
        step(f"ANTHROPIC_API_KEY が無いので調べていません。依頼文を out/trends/{week}.prompt.md に書き出しました")
        return

    step("Claude が Web で今週のチャートと話題曲を調べています（数分かかります）")
    notes, urls, msg = call_research(RESEARCH_SYSTEM, prompt, max_searches=12)
    if not notes:
        sys.exit(f"[停止] 調査に失敗しました：{msg}")
    (OUT / "trends").mkdir(parents=True, exist_ok=True)
    (OUT / "trends" / f"{week}.notes.md").write_text(notes, encoding="utf-8")
    step("調べた内容を表の形に整えています")
    data, msg = call_json(STRUCTURE_SYSTEM, f"Research notes for week {week}:\n\n{notes}", SCHEMA, effort="medium")
    if not data:
        sys.exit(f"[停止] 整形に失敗しました：{msg}")

    srcs = [s for s in data["sources"] if s["chart"]]
    primary = next((s for s in srcs if s["source"] == "spotify_global_weekly"), srcs[0] if srcs else None)
    share = language_share(primary["chart"]) if primary else {}
    trend_lang = pick_trend_language(share)
    sources = []
    for s in srcs:
        sources.append({**s, "language_share": language_share(s["chart"]), "is_primary": s is primary})
    write_json(out, {"week_start": week, "trend_language": trend_lang, "threshold": THRESHOLD,
                     "primary_source": primary["source"] if primary else None, "sources": sources,
                     "sound_trends": data["sound_trends"], "scene_picks": data["scene_picks"], "urls": urls})
    (OUT / "trends" / f"{week}.md").write_text(report(week, data, trend_lang, share, urls), encoding="utf-8")

    ref_dir = Path(a.references)
    ref_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, p in enumerate(data["scene_picks"], 1):
        if p["language"] and p["title"] and p["artist"]:
            write_json(ref_dir / f"trend_{week}_{i:02d}.json", trend_sheet(p, week, i))
            n += 1
    step(f"トレンド言語：{trend_lang or 'なし'} ／ 場面ごとの話題曲 {n} 曲を参考曲フォルダに置きました")
    step(f"レポート: {(OUT / 'trends' / f'{week}.md').relative_to(ROOT)}")


if __name__ == "__main__":
    main()

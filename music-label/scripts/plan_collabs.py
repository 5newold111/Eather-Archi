#!/usr/bin/env python3
"""
その週のコラボ（feat. / remix）を決めて、ブリーフの骨組みに書き込む。

  決め方（docs/06_growth.md・docs/03_artists.md のルール）
    1. 新人（デビューから 4 週以内）は、既存の組との feat. でリスナーを借りる。相手は「伸びている（up）」組を優先
    2. 伸びている（up）組は、相性のよい相手（collab.preferred_partners）の曲に客演して他の組へ波及させる
    3. それ以外は 4 週に 1 回、相性のよい組を順番に組ませる（同じ組み合わせが続かないよう週ごとに回す）
    4. リミックス型の組（collab_style に remix）は、デビューから 5 週ごとに、前 4 週の相手の曲から 1 曲を選んで再構築する
       （成績があれば一番伸びた曲、無ければ最新の曲）

  ・組んだ 2 組は「A feat. B」「B feat. A」の別々の 2 曲。両方のブリーフに相手を書く（曲名に feat. は書かない）
  ・1 レーベル 1 週に 1 組まで（レーベルの設定書 collab_pairs_per_week で変更可）。別レーベルとは preferred_partners に書いた相手だけ
  ・Claude が書き足し済みのブリーフは変えない（作り直すときは --overwrite）

使い方
  python scripts/plan_collabs.py --week 2026-10-12
  python scripts/plan_collabs.py --week 2026-10-12 --pair light:shape   # 手で組を指定
  python scripts/plan_collabs.py --week 2026-10-12 --none               # 今週はコラボなし（書き込みを消す）
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import LEAD_WEEKS, OUT, ROOT, as_date, load_artist, load_label, read_json, step, write_json  # noqa: E402

NEW_WEEKS = 4         # デビューからこの週数までは「新人」
ROTATE_EVERY = 4      # 通常のコラボは 4 週に 1 回
REMIX_EVERY = 5       # リミックスはデビューから 5 週ごと


def week_index(artist: dict, week: date) -> int | None:
    """制作開始週から数えて何週目か（0 始まり）"""
    d = as_date(artist.get("debut_week"))
    if not d:
        return None
    return (week - (d - timedelta(weeks=LEAD_WEEKS))).days // 7


def trends() -> dict[str, str]:
    p = OUT / "growth" / "hints.json"
    return {k: v.get("artist_trend", "new") for k, v in read_json(p).items()} if p.exists() else {}


def guest_card(a: dict) -> dict:
    """相手の曲に客演するときに渡す情報（実在アーティスト名は含めない）"""
    v = a.get("vocal") or {}
    sig = (v.get("signature_techniques") or {})
    return {"slug": a["slug"], "name": a["name"], "formation": a.get("formation"),
            "guest_description": v.get("guest_description", ""),
            "voice": {k: (v.get("voice_spec") or {}).get(k) for k in ("comfortable_low", "comfortable_high", "falsetto_switch_point")
                      if (v.get("voice_spec") or {}).get(k)},
            "signature": sig.get("primary", ""), "wordless_style": sig.get("wordless_style", ""),
            "collab_style": (a.get("collab") or {}).get("collab_style", "")}


def best_recent_release(remixer: dict, partners: list[str], week: date) -> dict | None:
    """前 4 週の相手の曲から、伸びた曲（無ければ最新）を選ぶ"""
    streams: dict[str, int] = {}
    mp = OUT / "metrics" / "daily.csv"
    if mp.exists():
        with mp.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("kind", "dsp") != "sns":
                    streams[r["release_id"]] = streams.get(r["release_id"], 0) + int(float(r["streams"] or 0))
    cands = []
    for back in range(1, 5):
        w = (week - timedelta(weeks=back)).isoformat()
        for slug in partners:
            meta = OUT / "distrokid" / w / f"{slug}.metadata.json"
            brief = OUT / "briefs" / f"{w}_{slug}.json"
            if meta.exists() and brief.exists():
                m = read_json(meta)
                if m.get("title") and not m["title"].startswith("（"):
                    cands.append((streams.get(f"{w}_{slug}", 0), -back, w, slug, m["title"]))
    if not cands:
        return None
    s, _, w, slug, title = max(cands)
    b = read_json(OUT / "briefs" / f"{w}_{slug}.json")
    return {"artist_slug": slug, "artist_name": load_artist(slug).get("name"), "week_start": w,
            "title": title, "streams": s, "brief": f"out/briefs/{w}_{slug}.json",
            "core": b.get("core"), "suno_style_prompt": b.get("suno_style_prompt"),
            "title_rule": f"{title} ({remixer['name']} Remix)"}


def editable(path: Path, overwrite: bool) -> bool:
    b = read_json(path)
    return overwrite or not (b.get("suno_style_prompt") or b.get("title_candidates"))


def clear(b: dict) -> None:
    for k in ("featured_artist_slug", "featured_guest", "remix_of", "collab_reason"):
        b.pop(k, None) if k != "featured_artist_slug" else b.__setitem__(k, None)


def main() -> None:
    ap = argparse.ArgumentParser(description="その週のコラボを決めてブリーフに書く")
    ap.add_argument("--week", required=True)
    ap.add_argument("--pair", action="append", default=[], help="手で組む。例 light:shape")
    ap.add_argument("--none", action="store_true", help="今週はコラボなし")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    week = date.fromisoformat(a.week)
    week = week - timedelta(days=week.weekday())

    paths = {read_json(p)["artist_slug"]: p for p in sorted((OUT / "briefs").glob(f"{week}_*.json")) if "." not in p.stem}
    if not paths:
        sys.exit(f"[エラー] {week} のブリーフ（骨組み）がありません。先に select_references.py を実行してください")
    artists = {s: load_artist(s, read_json(p).get("label_slug")) for s, p in paths.items()}
    free = {s for s, p in paths.items() if editable(p, a.overwrite)}
    trend = trends()
    briefs = {s: read_json(p) for s, p in paths.items()}
    for s in free:
        clear(briefs[s])

    plan: list[dict] = []
    used: set[str] = {s for s in paths if s not in free and (briefs[s].get("featured_artist_slug") or briefs[s].get("remix_of"))}
    label_count: dict[str, int] = {}

    def cap(label: str) -> int:
        return int(load_label(label).get("collab_pairs_per_week", 1))

    def can_pair(x: str, y: str) -> bool:
        if x == y or x in used or y in used or x not in free or y not in free:
            return False
        lx, ly = artists[x]["label_slug"], artists[y]["label_slug"]
        if lx != ly:   # 別レーベルとは、どちらかの preferred_partners に書いてあるときだけ
            if y not in (artists[x].get("collab") or {}).get("preferred_partners", []) and \
               x not in (artists[y].get("collab") or {}).get("preferred_partners", []):
                return False
        return label_count.get(lx, 0) < cap(lx) and label_count.get(ly, 0) < cap(ly)

    def pair(x: str, y: str, reason: str) -> None:
        used.update((x, y))
        for lab in {artists[x]["label_slug"], artists[y]["label_slug"]}:
            label_count[lab] = label_count.get(lab, 0) + 1
        for me, other in ((x, y), (y, x)):
            briefs[me]["featured_artist_slug"] = other
            briefs[me]["featured_guest"] = guest_card(artists[other])
            briefs[me]["collab_reason"] = reason
        plan.append({"type": "feat", "a": x, "b": y, "reason": reason})

    def is_remixer(s: str) -> bool:
        return "remix" in str((artists[s].get("collab") or {}).get("collab_style", "")).lower()

    if not a.none:
        # 0. 手で指定した組
        for p in a.pair:
            x, _, y = p.partition(":")
            if x in paths and y in paths and can_pair(x, y):
                pair(x, y, "オーナーの指定")
        # 1. リミックス（5 週ごと）
        for s in sorted(free):
            n = week_index(artists[s], week)
            if is_remixer(s) and n is not None and n > 0 and n % REMIX_EVERY == REMIX_EVERY - 1 and s not in used:
                src = best_recent_release(artists[s], (artists[s].get("collab") or {}).get("preferred_partners", []), week)
                if src:
                    briefs[s]["remix_of"] = src
                    briefs[s]["collab_reason"] = f"デビューから {n + 1} 週目のリミックス（前 4 週で一番伸びた相手の曲）"
                    used.add(s)
                    plan.append({"type": "remix", "a": s, "source": f"{src['artist_slug']} / {src['title']}"})
        # 2. 新人 × 伸びている相手
        for s in sorted(free, key=lambda x: week_index(artists[x], week) or 99):
            n = week_index(artists[s], week)
            if n is None or n >= NEW_WEEKS or s in used:
                continue
            partners = (artists[s].get("collab") or {}).get("preferred_partners", [])
            partners = sorted(partners, key=lambda p: {"up": 0, "flat": 1, "new": 2, "down": 3}.get(trend.get(p, "new"), 2))
            for p in partners:
                if p in paths and (week_index(artists[p], week) or 0) >= NEW_WEEKS and can_pair(s, p):
                    pair(s, p, f"新人（{n + 1} 週目）は既存の組との feat. でリスナーを借りる")
                    break
        # 3. 伸びている組を客演に
        for s in sorted(free):
            if trend.get(s) != "up" or s in used:
                continue
            for p in (artists[s].get("collab") or {}).get("preferred_partners", []):
                if p in paths and can_pair(s, p):
                    pair(s, p, "伸びている組を客演に出して他の組へ波及")
                    break
        # 4. 4 週に 1 回の順番
        if week.isocalendar()[1] % ROTATE_EVERY == 0:
            cands = sorted({tuple(sorted((s, p))) for s in free for p in (artists[s].get("collab") or {}).get("preferred_partners", [])
                            if p in paths})
            if cands:
                k = week.isocalendar()[1] // ROTATE_EVERY
                for i in range(len(cands)):
                    x, y = cands[(k + i) % len(cands)]
                    if can_pair(x, y):
                        pair(x, y, "4 週に 1 回の定期コラボ（相性のよい組を順番に）")
                        break

    for s in free:
        write_json(paths[s], briefs[s])
    out = OUT / "collabs" / f"{week}.json"
    write_json(out, {"week_start": week.isoformat(), "plan": plan})
    if not plan:
        step(f"{week}：今週のコラボはありません")
    for p in plan:
        if p["type"] == "feat":
            na, nb = artists[p["a"]]["name"], artists[p["b"]]["name"]
            step(f"コラボ：{na} feat. {nb} ／ {nb} feat. {na}（{p['reason']}）")
        else:
            step(f"リミックス：{artists[p['a']]['name']} が {p['source']} を再構築")
    print(f"   記録: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

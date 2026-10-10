#!/usr/bin/env python3
"""
伸びているアーティスト・曲・要素を分析し、次のリリースに返す

入力：成績データ（Supabase の metrics をエクスポートした CSV、または --demo で架空データ）
出力：
  out/growth/report.md     ← 人が読む週次レポート（日本語）
  out/growth/weights.json  ← select_references.py が読む「参考曲 × 枠」の重み（1.0〜3.0）
  out/growth/hints.json    ← ブリーフに入れる組ごとのヒント（trend / top_slots / notes）

使い方：
  python3 scripts/analyze_growth.py --demo
  python3 scripts/analyze_growth.py --metrics out/metrics.csv --sources out/brief_sources.csv

CSV の列：
  metrics.csv       : release_id, artist_slug, title, release_date, date, platform, streams, saves, skips
  brief_sources.csv : release_id, slot, reference_id

標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEIGHT_CAP = 3.0          # ルール 5：成績由来の重みは最大 3 倍
MIN_STREAMS = 1000        # これ未満の曲は重み計算に使わない（Spotify の支払い下限と同じ）
UP, DOWN = 1.2, 0.8       # 直近 4 週 / その前 4 週 がこれ以上なら up、以下なら down


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def demo_data(seed: int, today: date) -> tuple[list[dict], list[dict]]:
    """架空の 5 組 × 8 週ぶんの成績。実在のデータではない。"""
    rng = random.Random(seed)
    artists = {"a": 1.6, "b": 1.0, "c": 0.55, "d": 1.1, "e": 0.0}   # 伸び方の違いを仕込む（e は新人）
    slots = ["lyrics", "worldview", "instruments", "performance", "structure",
             "harmony", "groove", "phrase", "vocal_main", "vocal_sub1", "vocal_sub2"]
    metrics, sources = [], []
    for ai, (slug, growth) in enumerate(artists.items()):
        weeks = 2 if slug == "e" else 8
        for w in range(weeks):
            rel = today - timedelta(days=7 * (weeks - w) + 3)
            rid = f"{slug}-{w:02d}"
            base = rng.randint(40, 400)
            for d in range(min(56, (today - rel).days)):
                day = rel + timedelta(days=d)
                age_factor = max(0.3, 1.0 - d / 60)
                trend = 1 + (growth - 1) * ((today - day).days < 28)   # 直近 4 週に伸び方を反映
                streams = int(base * age_factor * trend * rng.uniform(0.7, 1.3))
                metrics.append({
                    "release_id": rid, "artist_slug": slug, "title": f"架空の曲 {rid}",
                    "release_date": rel.isoformat(), "date": day.isoformat(), "platform": "spotify",
                    "streams": streams, "saves": int(streams * rng.uniform(0.02, 0.12)),
                    "skips": int(streams * rng.uniform(0.1, 0.4)),
                })
            used = rng.sample(range(1, 21), len(slots))
            for s, n in zip(slots, used):
                sources.append({"release_id": rid, "slot": s, "reference_id": f"demo-{n:02d}"})
    print(f"  デモ用の架空の成績データを用意しました（{len(metrics)} 行、実在のデータではありません）")
    return metrics, sources


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def aggregate(metrics: list[dict], today: date) -> dict[str, dict]:
    """曲ごとに 合計 / 直近 4 週 / その前 4 週 を集計"""
    tracks: dict[str, dict] = {}
    for m in metrics:
        t = tracks.setdefault(m["release_id"], {
            "artist": m["artist_slug"], "title": m["title"], "release_date": m["release_date"],
            "streams": 0, "saves": 0, "skips": 0, "s4": 0, "sprev4": 0,
        })
        s, d = int(m["streams"]), date.fromisoformat(m["date"])
        t["streams"] += s; t["saves"] += int(m["saves"]); t["skips"] += int(m["skips"])
        age = (today - d).days
        if age < 28:
            t["s4"] += s
        elif age < 56:
            t["sprev4"] += s
    for t in tracks.values():
        t["save_rate"] = t["saves"] / t["streams"] if t["streams"] else 0.0
        t["skip_rate"] = t["skips"] / t["streams"] if t["streams"] else 0.0
        t["growth"] = (t["s4"] / t["sprev4"]) if t["sprev4"] else None
        t["reached_1000"] = t["streams"] >= 1000
    return tracks


def trend_label(ratio: float | None) -> str:
    if ratio is None:
        return "new"
    if ratio >= UP:
        return "up"
    if ratio <= DOWN:
        return "down"
    return "flat"


def artist_summary(tracks: dict[str, dict]) -> dict[str, dict]:
    by: dict[str, dict] = defaultdict(lambda: {"tracks": 0, "streams": 0, "s4": 0, "sprev4": 0, "reached": 0, "save": 0})
    for t in tracks.values():
        a = by[t["artist"]]
        a["tracks"] += 1; a["streams"] += t["streams"]; a["s4"] += t["s4"]; a["sprev4"] += t["sprev4"]
        a["reached"] += int(t["reached_1000"]); a["save"] += t["saves"]
    for a in by.values():
        a["growth"] = (a["s4"] / a["sprev4"]) if a["sprev4"] else None
        a["trend"] = trend_label(a["growth"])
        a["avg_per_track"] = a["streams"] / a["tracks"] if a["tracks"] else 0
        a["reached_rate"] = a["reached"] / a["tracks"] if a["tracks"] else 0
        a["save_rate"] = a["save"] / a["streams"] if a["streams"] else 0
    return dict(by)


def slot_weights(tracks: dict[str, dict], sources: list[dict]) -> tuple[dict[str, float], dict[str, dict]]:
    """
    参考曲 × 枠 の重み。保存率がレーベル平均より高いほど重く（1.0〜WEIGHT_CAP）。
    返り値：weights["slot|reference_id"] = 重み、組ごとの保存率上位の枠
    """
    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {"streams": 0, "saves": 0})
    per_artist_slot: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"streams": 0, "saves": 0}))
    for s in sources:
        t = tracks.get(s["release_id"])
        if not t:
            continue
        k = (s["slot"], s["reference_id"])
        agg[k]["streams"] += t["streams"]; agg[k]["saves"] += t["saves"]
        ps = per_artist_slot[t["artist"]][s["slot"]]
        ps["streams"] += t["streams"]; ps["saves"] += t["saves"]

    total_streams = sum(v["streams"] for v in agg.values())
    total_saves = sum(v["saves"] for v in agg.values())
    label_rate = (total_saves / total_streams) if total_streams else 0.0

    weights: dict[str, float] = {}
    for (slot, ref), v in agg.items():
        if v["streams"] < MIN_STREAMS or not label_rate:
            continue
        ratio = (v["saves"] / v["streams"]) / label_rate
        weights[f"{slot}|{ref}"] = round(min(WEIGHT_CAP, max(1.0, ratio)), 2)

    top_slots: dict[str, dict] = {}
    for artist, slots in per_artist_slot.items():
        ranked = sorted(
            ((sl, v["saves"] / v["streams"]) for sl, v in slots.items() if v["streams"]),
            key=lambda x: -x[1],
        )
        top_slots[artist] = {"top_slots": [sl for sl, _ in ranked[:3]], "label_save_rate": round(label_rate, 4)}
    return weights, top_slots


# ---------------------------------------------------------------------------
# 判断ルール → ヒント
# ---------------------------------------------------------------------------

PIVOT_DOWN_STREAK = 2        # 連続 down の回数（＝ 8 週）で転換
PIVOT_MIN_TRACKS = 8         # これ以上出していて
PIVOT_REACHED_RATE = 0.3     # 1,000 再生到達率がこれ未満なら転換


PIVOT_COOLDOWN_DAYS = 56    # 転換から 8 週は再判定しない


def last_pivot_dates() -> dict[str, date]:
    """設定書の concept_history から、組ごとの最後の転換日を読む"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from _common import all_artists
    except ImportError:
        return {}
    out = {}
    for a in all_artists():
        hist = (a.get("concept_history") or {})
        hist = hist.get("history", []) if isinstance(hist, dict) else hist
        dates = [h.get("date") for h in hist if isinstance(h, dict) and h.get("date")]
        if dates:
            out[a["slug"]] = date.fromisoformat(max(dates))
    return out


def detect_pivots(artists: dict[str, dict], history: dict[str, list[str]], today: date | None = None) -> dict[str, dict]:
    """
    伸び悩みの判定 → 方針転換の提案（docs/06_growth.md「方針転換」）
    history[slug] = 過去の trend の並び（新しい順）。無ければ今回だけで判定。
    レベル 1：音の微調整（枠の組み合わせ・テンポ）／ レベル 2：コンセプト転換（場面・色・空間を変え、写真を撮り直す）
    """
    pivots = {}
    recent = last_pivot_dates()
    for slug, a in artists.items():
        if today and slug in recent and (today - recent[slug]).days < PIVOT_COOLDOWN_DAYS:
            continue   # 転換から 8 週は効果を待つ
        past = history.get(slug, [])
        streak = 1 if a["trend"] == "down" else 0
        for t in past:
            if t == "down" and streak: streak += 1
            else: break
        low_reach = a["tracks"] >= PIVOT_MIN_TRACKS and a["reached_rate"] < PIVOT_REACHED_RATE
        if streak >= PIVOT_DOWN_STREAK or low_reach:
            level = 2 if (streak >= PIVOT_DOWN_STREAK and low_reach) or streak >= PIVOT_DOWN_STREAK + 1 else 1
            pivots[slug] = {
                "level": level,
                "reason": f"down {streak} 回連続" + ("、1,000 再生到達率 {:.0%}".format(a["reached_rate"]) if low_reach else ""),
                "actions": (["drive_scene / visual.space / visual.palette を隣の場面へ変える", "composition_habits を 1 項目変える",
                             "アーティスト写真とロゴを撮り直す（generate_visuals.py --kind reshoot）", "concept_history に version +1 を記録"]
                            if level == 2 else
                            ["枠の組み合わせを top_slots 以外へ大きく変える", "BPM 範囲を ±5 ずらす", "トレンド曲の枠を 3 に増やす"]),
                "photos_reshot": level == 2,
            }
    return pivots


def make_hints(artists: dict[str, dict], top: dict[str, dict]) -> dict[str, dict]:
    hints: dict[str, dict] = {}
    for slug, a in artists.items():
        notes = []
        if a["trend"] == "up":
            notes.append("伸びている：この組の成功要素（top_slots）を次週も優先し、コラボの客演に出して他の組へ波及させる")
        elif a["trend"] == "down":
            notes.append("下がっている：枠の組み合わせを大きく変える（top_slots 以外を優先）。2 週連続 down なら設定書の sound / lyrics を見直す")
        elif a["trend"] == "new":
            notes.append("データ不足：判断しない。既存組との feat. でリスナーを借りる")
        else:
            notes.append("横ばい：トレンド曲の枠を 2 → 3 に増やして変化をつける")
        if a["tracks"] >= 4 and a["reached_rate"] < 0.5:
            notes.append(f"1,000 再生到達率 {a['reached_rate']:.0%}：曲数より 1 曲への告知を厚くする（プレイリスト・ショート動画）")
        hints[slug] = {
            "artist_trend": a["trend"],
            "growth_ratio": round(a["growth"], 2) if a["growth"] else None,
            "top_slots": top.get(slug, {}).get("top_slots", []),
            "notes": notes,
        }
    return hints


# ---------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------

def write_report(path: Path, today: date, artists: dict, tracks: dict, weights: dict, hints: dict) -> None:
    lines = [f"# 成長レポート（{today}）", ""]
    lines += ["## アーティスト", "", "| 組 | 傾向 | 直近4週 | その前4週 | 曲数 | 1,000到達率 | 保存率 |", "|---|---|---|---|---|---|---|"]
    for slug, a in sorted(artists.items(), key=lambda x: -x[1]["s4"]):
        g = f"{a['growth']:.2f}" if a["growth"] else "—"
        lines.append(f"| {slug} | **{a['trend']}**（{g}） | {a['s4']:,} | {a['sprev4']:,} | {a['tracks']} | {a['reached_rate']:.0%} | {a['save_rate']:.1%} |")
    lines += ["", "## 伸びている曲（直近 4 週の伸び率 上位 5）", "", "| 曲 | 組 | 伸び率 | 合計再生 | 保存率 |", "|---|---|---|---|---|"]
    for rid, t in sorted(((r, t) for r, t in tracks.items() if t["growth"]), key=lambda x: -x[1]["growth"])[:5]:
        lines.append(f"| {t['title']} | {t['artist']} | {t['growth']:.2f} | {t['streams']:,} | {t['save_rate']:.1%} |")
    lines += ["", "## 落ちている曲（下位 3）", "", "| 曲 | 組 | 伸び率 | 合計再生 |", "|---|---|---|---|"]
    for rid, t in sorted(((r, t) for r, t in tracks.items() if t["growth"]), key=lambda x: x[1]["growth"])[:3]:
        lines.append(f"| {t['title']} | {t['artist']} | {t['growth']:.2f} | {t['streams']:,} |")
    lines += ["", f"## 参考曲 × 枠 の重み（1.0 超えのもの、上限 {WEIGHT_CAP}）", ""]
    strong = sorted(((k, w) for k, w in weights.items() if w > 1.0), key=lambda x: -x[1])[:15]
    lines += [f"- `{k}` → {w}" for k, w in strong] or ["- （まだ重みをつけられるデータがありません）"]
    lines += ["", "## 組ごとのヒント（ブリーフに入ります）", ""]
    for slug, h in hints.items():
        lines.append(f"### {slug}（{h['artist_trend']}）")
        lines.append(f"- 保存率が高かった枠: {', '.join(h['top_slots']) or '—'}")
        lines += [f"- {n}" for n in h["notes"]]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="伸びているアーティスト・曲・要素を分析し、重みとヒントを出す")
    ap.add_argument("--metrics", type=Path, help="metrics.csv")
    ap.add_argument("--sources", type=Path, help="brief_sources.csv")
    ap.add_argument("--demo", action="store_true", help="架空の成績データで動作確認する")
    ap.add_argument("--today", help="集計基準日（YYYY-MM-DD）。省略時は今日")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "growth")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    print("=== 成長分析を始めます ===")
    print(f"  基準日: {today}")

    if args.demo:
        metrics, sources = demo_data(args.seed, today)
    else:
        if not (args.metrics and args.sources):
            ap.error("--metrics と --sources の両方、または --demo を指定してください")
        metrics, sources = load_csv(args.metrics), load_csv(args.sources)
        print(f"  成績 {len(metrics)} 行、枠の割り当て {len(sources)} 行を読み込みました")

    print("  曲ごとに集計しています…")
    tracks = aggregate(metrics, today)
    print("  アーティストごとの伸びを判定しています…")
    artists = artist_summary(tracks)
    print("  参考曲 × 枠 の重みを計算しています…")
    weights, top = slot_weights(tracks, sources)
    hints = make_hints(artists, top)
    hist_path = args.out / "trend_history.json"
    raw_hist = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
    # 履歴は [週の月曜, 傾向] の並び（新しい順）。同じ週に何度実行しても 1 回分として数える
    week_key = (today - timedelta(days=today.weekday())).isoformat()
    hist_rows = {k: [e if isinstance(e, list) else [None, e] for e in v] for k, v in raw_hist.items()}
    history = {k: [t for w, t in v if w != week_key] for k, v in hist_rows.items()}
    pivots = detect_pivots(artists, history, today)
    for slug, h in hints.items():
        if slug in pivots:
            h["pivot"] = pivots[slug]
            h["notes"].append(f"【方針転換 レベル {pivots[slug]['level']}】{pivots[slug]['reason']} → " + "／".join(pivots[slug]["actions"]))

    args.out.mkdir(parents=True, exist_ok=True)
    for slug, a in artists.items():
        rows = [e for e in hist_rows.get(slug, []) if e[0] != week_key]
        hist_rows[slug] = ([[week_key, a["trend"]]] + rows)[:12]
    hist_path.write_text(json.dumps(hist_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "pivots.json").write_text(json.dumps(pivots, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "weights.json").write_text(json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "hints.json").write_text(json.dumps(hints, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(args.out / "report.md", today, artists, tracks, weights, hints)

    for slug, a in artists.items():
        g = f"{a['growth']:.2f}" if a["growth"] else "—"
        print(f"    {slug:<8} {a['trend']:<5} 直近4週 {a['s4']:>7,} / 前4週 {a['sprev4']:>7,}（{g}）")
    if pivots:
        summary = ", ".join(f"{k}（レベル {v['level']}）" for k, v in pivots.items())
        print(f"  方針転換の提案: {summary} → pivots.json")
    print(f"  重みをつけた参考曲×枠: {len(weights)} 件（1.0 超え {sum(1 for w in weights.values() if w > 1.0)} 件）")
    print(f"\n=== 完了。{args.out.relative_to(ROOT)}/ に report.md / weights.json / hints.json を書き出しました ===")
    print("    次：select_references.py に --weights と --hints を渡すと、来週の割り当てに反映されます")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
四半期ごとに、配信済みの曲を「組ごとの EP」と「レーベルのコンピレーション」にまとめる計画と登録シートを作る。

  組ごとの EP        その四半期に出した曲を配信順に（4 曲以上の組だけ。最大 13 曲）
  コンピレーション   レーベルごとに、各組でいちばん伸びた曲を 1 曲ずつ（成績が無ければ最新の曲）
  タイトル           季節＋年（例：Autumn 2026）。コンピは「<レーベル名> — Autumn 2026」

  ・新しく作る曲は無い。既に配信した曲の ISRC（国際標準レコーディングコード）をそのまま使うので、再生数は元の曲に積み上がる
  ・DistroKid への登録（アルバムとして）は人が行う。シートを見ながらコピーして貼る
  ・ジャケットは generate_visuals.py で作る（シートに手順を書く）

使い方
  python scripts/plan_quarterly.py --quarter 2026Q4
  python scripts/plan_quarterly.py --if-quarter-start      # 定期実行：1・4・7・10 月の 1 日だけ、前の四半期をまとめる
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_artist, load_label, read_json, step, write_json  # noqa: E402

SEASON = {1: "Winter", 2: "Spring", 3: "Summer", 4: "Autumn"}
MIN_EP, MAX_EP = 4, 13


def quarter_range(q: str) -> tuple[date, date]:
    y, n = int(q[:4]), int(q[-1])
    start = date(y, 3 * (n - 1) + 1, 1)
    end = date(y + (n == 4), (3 * n) % 12 + 1, 1)
    return start, end


def previous_quarter(today: date) -> str:
    n = (today.month - 1) // 3 + 1
    return f"{today.year - 1}Q4" if n == 1 else f"{today.year}Q{n - 1}"


def streams_by_release() -> dict[str, int]:
    out: dict[str, int] = {}
    p = OUT / "metrics" / "daily.csv"
    if p.exists():
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("kind", "dsp") != "sns":
                    out[r["release_id"]] = out.get(r["release_id"], 0) + int(float(r["streams"] or 0))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="四半期の EP・コンピレーション計画")
    ap.add_argument("--quarter", help="例 2026Q4")
    ap.add_argument("--if-quarter-start", action="store_true")
    a = ap.parse_args()
    today = date.today()
    if a.if_quarter_start:
        if today.month not in (1, 4, 7, 10) or today.day > 7:
            step("四半期の初めではないので、何もしません")
            return
        q = previous_quarter(today)
    elif a.quarter:
        q = a.quarter.upper()
    else:
        sys.exit("[エラー] --quarter（例 2026Q4）か --if-quarter-start を指定してください")
    start, end = quarter_range(q)
    title_base = f"{SEASON[int(q[-1])]} {q[:4]}"
    streams = streams_by_release()

    tracks: dict[str, list[dict]] = {}
    for mp in sorted((OUT / "distrokid").glob("*/*.metadata.json")):
        m = read_json(mp)
        d = date.fromisoformat(m["release_at_utc"][:10])
        if not (start <= d < end) or not m.get("title") or m["title"].startswith("（"):
            continue
        rid = f"{m['week_start']}_{m['artist_slug']}"
        tracks.setdefault(m["artist_slug"], []).append({**m, "release_date": d.isoformat(), "streams": streams.get(rid, 0)})
    if not tracks:
        step(f"{q}（{start}〜{end}）に配信した曲がありません")
        return

    eps, comps = [], {}
    for slug, ts in sorted(tracks.items()):
        a_ = load_artist(slug, ts[0].get("label_slug"))
        ts.sort(key=lambda t: t["release_date"])
        if len(ts) >= MIN_EP:
            eps.append({"artist": a_["name"], "slug": slug, "label": a_["label_slug"], "title": title_base,
                        "tracks": ts[:MAX_EP]})
        best = max(ts, key=lambda t: (t["streams"], t["release_date"]))
        comps.setdefault(a_["label_slug"], []).append({"artist": a_["name"], **best})

    lines = [f"# 四半期のまとめ（{q}：{start} 〜 {end}）", "",
             "既に配信した曲を、同じ ISRC のままアルバム形式で出し直す計画です。DistroKid の「Album」で登録します。", ""]
    for ep in eps:
        lines += [f"## EP：{ep['artist']} — {ep['title']}", "", "| # | 曲 | Featured | ISRC | 音源 |", "|---|---|---|---|---|"]
        for i, t in enumerate(ep["tracks"], 1):
            feat = load_artist(t["featured_artist_slug"])["name"] if t.get("featured_artist_slug") else "—"
            lines.append(f"| {i} | {t['title']} | {feat} | {t.get('isrc') or '⚠ 未記入（metadata.json に書く）'} | `{t['audio']}` |")
        lines.append("")
    for label, items in comps.items():
        if len(items) < 3:
            continue
        lname = load_label(label).get("name")
        lines += [f"## コンピレーション：{lname} — {title_base}", "", "Artist 欄は「Various Artists」。各曲に元のアーティスト名を入れる。", "",
                  "| # | アーティスト | 曲 | 再生数 | ISRC |", "|---|---|---|---|---|"]
        for i, t in enumerate(sorted(items, key=lambda t: -t["streams"]), 1):
            lines.append(f"| {i} | {t['artist']} | {t['title']} | {t['streams']:,} | {t.get('isrc') or '⚠ 未記入'} |")
        lines.append("")
    lines += ["## 登録の手順", "",
              "1. DistroKid で Upload → 「Album」を選ぶ（EP も同じ。曲数でストアが EP と表示する）",
              "2. 各曲で「この曲は以前に配信したか」に Yes、ISRC に上の値を入れる（再生数が元の曲に積み上がる）",
              "3. ジャケットはシリーズの部屋で季節だけ変える：`generate_visuals.py --artist <組> --kind cover --brief <その組の最新ブリーフ>` → `finalize_cover.py`",
              "4. 配信日は 2 週間以上先の水曜（子レーベルは設定書の曜日）",
              "5. AI 使用の申告は単曲と同じく必ず行う", ""]
    out = OUT / "quarterly" / q
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(out / "plan.json", {"quarter": q, "eps": eps, "compilations": comps})
    step(f"{q}：EP {len(eps)} 枚・コンピレーション {sum(1 for v in comps.values() if len(v) >= 3)} 枚の計画を書き出しました: "
         f"{(out / 'plan.md').relative_to(ROOT)}")


if __name__ == "__main__":
    main()

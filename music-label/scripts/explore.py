#!/usr/bin/env python3
"""
方向性を探る（探索モード）：何が伸びるか分からないうちは、曲ごとに作曲の方向性を変えて試し、
再生が伸びた方向性を少しずつ固めていく（オーナーの指示 2026-10-26。決定 96）

  ・探索モード（いまの既定・全組）
      - 管理表の「毎曲の癖」は細かく決めなくてよい。入っていても毎曲必ず入れるものではなく「たまに」扱い
      - 作曲の癖・得意な歌い方・テンポの幅・ジャンルは「寄りどころ」。曲ごとの方向性（下の 7 つの軸）のほうを優先する
      - 変えないもの：声（Suno のペルソナ・声の性別と高さ）・名前・人生・禁止事項（その組であることの芯）
  ・曲ごとの方向性（7 つの軸）：テンポ・熱量・ジャンルの寄せ方・編成・構成・気分・歌い方。曲ごとにくじで選ぶ
      - 同じ組の直近 3 曲と同じ値は出にくくする（いろいろ試すため）
      - 成績が集まると、伸びた値ほどくじが厚くなる（最大 4 倍）。どの値も最低 8% は残す（探るのをやめない）
      - 組の数字が少ないうちは、レーベル全体（5 組まとめて）の傾向を半分の重みで使う
  ・固める：ある軸で 1 つの値が 6 割以上・3 曲以上なら「固まりつつある」、75% 以上・5 曲以上なら「ほぼ固まった」と知らせる。
    固める組は focus にすると、毎曲の癖が毎曲に戻り、いちばん伸びた値を 85% で選ぶ（15% は探り続ける）
  ・成績：collect_metrics.py の out/metrics/metrics.csv。配信から 14 日以上たった曲の、最初の 28 日の再生（14〜27 日は 28 日に換算）

使い方：
  python3 scripts/explore.py report            # 成績から学んで、組ごとの探り具合を表示（定期実行の「成長分析」でも動く）
  python3 scripts/explore.py focus tidewool    # この組を固める（毎曲の癖を戻し、伸びた方向性を主に）
  python3 scripts/explore.py explore tidewool  # 探索に戻す
  python3 scripts/explore.py pick tidewool     # 次の曲の方向性のくじを試しに引く
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, all_artists, load_artist, read_json, write_json  # noqa: E402

SETTINGS = OUT / "explore" / "settings.json"
LEARNED = OUT / "growth" / "explore.json"
REPORT = OUT / "growth" / "explore.md"
METRICS = OUT / "metrics" / "metrics.csv"
FLOOR = 0.08          # どの値も最低 8% は残す
MAX_W = 4.0
FOCUS_BEST = 0.85     # 固めた組は、いちばん伸びた値を 85%

# 軸：{値: (Claude への英語, 日本語)}
AXES: dict[str, dict[str, tuple[str, str]]] = {
    "tempo": {"slower": ("noticeably slower than the act's usual tempo", "いつもより遅い"),
              "usual": ("within the act's usual tempo range", "いつものテンポ"),
              "faster": ("noticeably faster than the act's usual tempo", "いつもより速い")},
    "energy": {"quiet": ("quiet and intimate, low energy", "静か・近い"),
               "mid": ("medium energy, steady", "中くらい"),
               "high": ("high energy, big and driving", "熱量が高い")},
    "genre": {"core": ("the act's core genre", "いつものジャンル"),
              "neighbor": ("lean into a neighbouring genre that suits the voice (pick one, name it in notes_ja)", "近いジャンルに寄せる"),
              "contrast": ("cross over with a contrasting genre while keeping the voice (pick one, name it in notes_ja)", "離れたジャンルと混ぜる")},
    "arrangement": {"sparse": ("sparse and minimal arrangement, lots of space", "少ない音・すき間"),
                    "band": ("full live-band arrangement", "バンドの厚い編成"),
                    "electronic": ("produced electronic arrangement (synths, programmed drums)", "打ち込み中心")},
    "structure": {"hook_first": ("hook-first: the chorus or hook lands within the first 15 seconds", "サビが最初の 15 秒に来る"),
                  "classic": ("classic verse-chorus structure", "Aメロ・サビの定番"),
                  "slow_build": ("slow build that opens up in the second half", "後半に向けて盛り上がる")},
    "mood": {"bright": ("bright and uplifting", "明るい"),
             "bittersweet": ("bittersweet, warm with a little sadness", "ほろ苦い"),
             "dark": ("dark and moody", "暗い・ムーディー")},
    "vocal": {"close": ("intimate close-mic singing, soft dynamics", "近くでささやくように"),
              "anthemic": ("open, anthemic singing with big held notes", "大きく張って歌う"),
              "rhythmic": ("rhythmic, talk-sing delivery with tight phrasing", "リズムで語るように")},
}
AXIS_JA = {"tempo": "テンポ", "energy": "熱量", "genre": "ジャンル", "arrangement": "編成", "structure": "構成", "mood": "気分", "vocal": "歌い方"}


def settings() -> dict:
    try:
        return read_json(SETTINGS) if SETTINGS.exists() else {}
    except (OSError, ValueError):
        return {}


def mode(artist: dict) -> str:
    """explore（探る・既定）か focus（固める）"""
    return (settings().get("modes") or {}).get(artist.get("slug", ""), "explore")


def relax(artist: dict) -> dict:
    """探索モードの組は、毎曲の癖を「たまに」扱いにした写しを返す（必ず入れる・足す・採点する、をしない）"""
    if mode(artist) != "explore":
        return artist
    a = json.loads(json.dumps(artist))
    for t in ((a.get("vocal") or {}).get("signature_techniques") or {}).get("tics", []) or []:
        if t.get("frequency") == "every_song":
            t["frequency"] = "sometimes"
    return a


def learned() -> dict:
    try:
        return read_json(LEARNED) if LEARNED.exists() else {}
    except (OSError, ValueError):
        return {}


def probs(slug: str, recent: list[dict] | None = None, data: dict | None = None, focus: bool = False) -> dict[str, dict[str, float]]:
    """軸ごとの値のくじの確率"""
    data = learned() if data is None else data
    act = (data.get("acts") or {}).get(slug) or {}
    lab = data.get("label") or {}
    out = {}
    for ax, vals in AXES.items():
        w = {}
        for v in vals:
            wa = ((act.get(ax) or {}).get(v) or {}).get("weight", 1.0)
            na = ((act.get(ax) or {}).get(v) or {}).get("n", 0)
            wl = ((lab.get(ax) or {}).get(v) or {}).get("weight", 1.0)
            k = na / (na + 2)   # 組の数字が少ないうちは、レーベル全体を半分の重みで混ぜる
            w[v] = k * wa + (1 - k) * (1 + (wl - 1) * 0.5)
        if recent:   # 直近 3 曲と同じ値は出にくく（いろいろ試す）
            for r in recent[-3:]:
                v = ((r.get("axes") or {}).get(ax) or {}).get("value")
                if v in w and not focus:
                    w[v] *= 0.6
        if focus:
            best = max(w, key=w.get)
            rest = [v for v in w if v != best]
            p = {best: FOCUS_BEST, **{v: (1 - FOCUS_BEST) / len(rest) for v in rest}}
        else:
            tot = sum(w.values())
            p = {v: x / tot for v, x in w.items()}
            low = [v for v in p if p[v] < FLOOR]
            if low:   # どの値も最低 8%
                fix = sum(FLOOR - p[v] for v in low)
                hi = sum(p[v] for v in p if v not in low)
                p = {v: (FLOOR if v in low else p[v] - fix * p[v] / hi) for v in p}
        out[ax] = {v: round(x, 3) for v, x in p.items()}
    return out


def recent_axes(slug: str, n: int = 3) -> list[dict]:
    """その組の直近のブリーフの方向性"""
    out = []
    briefs = OUT / "briefs"
    for p in sorted(briefs.glob(f"*_{slug}.json"), key=lambda x: x.stat().st_mtime)[-n:] if briefs.exists() else []:
        try:
            e = read_json(p).get("explore")
        except (OSError, ValueError):
            continue
        if e:
            out.append(e)
    return out


def pick(artist: dict, rng: random.Random | None = None) -> dict:
    """次の曲の方向性をくじで選ぶ。ブリーフの explore に入る"""
    rng = rng or random.Random()
    slug = artist.get("slug", "")
    m = mode(artist)
    pr = probs(slug, recent_axes(slug), focus=(m == "focus"))
    axes = {}
    for ax, p in pr.items():
        v = rng.choices(list(p), weights=list(p.values()))[0]
        en, ja = AXES[ax][v]
        axes[ax] = {"value": v, "en": en, "ja": ja, "p": p[v]}
    return {"mode": m, "axes": axes, "picked_at": date.today().isoformat()}


# ---------------------------------------------------------------------------
# 成績から学ぶ
# ---------------------------------------------------------------------------
def release_scores(today: date | None = None) -> dict[str, float]:
    """曲ごとの「最初の 28 日の再生」（配信から 14 日以上たった曲だけ。14〜27 日は 28 日に換算）"""
    today = today or date.today()
    if not METRICS.exists():
        return {}
    by: dict[str, dict] = defaultdict(lambda: {"rel": None, "streams": 0.0, "last": None})
    with METRICS.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rel, d = date.fromisoformat(r["release_date"][:10]), date.fromisoformat(r["date"][:10])
                s = float(r.get("streams") or 0)
            except (KeyError, ValueError):
                continue
            x = by[r["release_id"]]
            x["rel"] = rel
            if d < rel + timedelta(days=28):
                x["streams"] += s
    out = {}
    for rid, x in by.items():
        if not x["rel"]:
            continue
        age = (today - x["rel"]).days
        if age < 14:
            continue
        out[rid] = x["streams"] * (28 / min(age, 28))
    return out


def learn(today: date | None = None) -> dict:
    """方向性の値ごとの伸び：組ごと・レーベル全体。weight は「その軸の平均より何倍伸びたか」（0.5〜4）"""
    scores = release_scores(today)
    rows = []
    for bp in sorted((OUT / "briefs").glob("*.json")) if (OUT / "briefs").exists() else []:
        if "." in bp.stem:
            continue
        try:
            b = read_json(bp)
        except (OSError, ValueError):
            continue
        rid = f"{b.get('week_start')}_{b.get('artist_slug')}"
        if b.get("explore") and rid in scores:
            rows.append((b["artist_slug"], b["explore"]["axes"], math.log1p(scores[rid])))

    def table(rs):
        t = {}
        for ax in AXES:
            vals = defaultdict(list)
            for _, axes, sc in rs:
                v = (axes.get(ax) or {}).get("value")
                if v:
                    vals[v].append(sc)
            if not vals:
                continue
            mean = sum(sum(x) for x in vals.values()) / sum(len(x) for x in vals.values())
            t[ax] = {v: {"n": len(x), "weight": round(max(0.5, min(MAX_W, math.exp(sum(x) / len(x) - mean))), 3)} for v, x in vals.items()}
        return t

    acts = defaultdict(list)
    for r in rows:
        acts[r[0]].append(r)
    data = {"updated": date.today().isoformat(), "songs": len(rows), "label": table(rows), "acts": {s: table(r) for s, r in acts.items()}}
    LEARNED.parent.mkdir(parents=True, exist_ok=True)
    write_json(LEARNED, data)
    return data


def firming(slug: str, data: dict) -> list[str]:
    """固まりつつある軸の知らせ"""
    act = (data.get("acts") or {}).get(slug) or {}
    pr = probs(slug, data=data)
    notes = []
    for ax, p in pr.items():
        best = max(p, key=p.get)
        n = ((act.get(ax) or {}).get(best) or {}).get("n", 0)
        if p[best] >= 0.75 and n >= 5:
            notes.append(f"{AXIS_JA[ax]}は「{AXES[ax][best][1]}」でほぼ固まった（{int(p[best] * 100)}%・{n} 曲）")
        elif p[best] >= 0.6 and n >= 3:
            notes.append(f"{AXIS_JA[ax]}は「{AXES[ax][best][1]}」に固まりつつある（{int(p[best] * 100)}%・{n} 曲）")
    return notes


def report() -> None:
    data = learn()
    lines = [f"# 方向性の探り具合（{data['updated']}・成績のある曲 {data['songs']} 曲）", "",
             "くじの確率：曲ごとに方向性を選ぶ確率。伸びた値ほど高くなる（どの値も最低 8%）。", ""]
    print(f"▶ 方向性の探り具合（成績のある曲 {data['songs']} 曲から）")
    for a in all_artists():
        slug = a.get("slug")
        m = mode(a)
        pr = probs(slug, data=data, focus=(m == "focus"))
        notes = firming(slug, data)
        head = f"■ {a.get('name')}（{'固める' if m == 'focus' else '探索中'}）"
        print(head)
        lines += [f"## {head[2:]}", ""]
        for ax, p in pr.items():
            row = "・".join(f"{AXES[ax][v][1]} {int(x * 100)}%" for v, x in sorted(p.items(), key=lambda kv: -kv[1]))
            print(f"   {AXIS_JA[ax]}：{row}")
            lines.append(f"- {AXIS_JA[ax]}：{row}")
        for n in notes:
            print(f"   ★ {n}")
            lines.append(f"- ★ {n}")
        if notes and m == "explore" and sum("ほぼ固まった" in n for n in notes) >= 3:
            msg = f"3 つ以上の軸がほぼ固まりました。固めるなら  python3 scripts/explore.py focus {slug}"
            print(f"   → {msg}")
            lines.append(f"- → {msg}")
        lines.append("")
    if not data["songs"]:
        print("   （まだ成績のある曲がありません。配信から 14 日たった曲の数字が入ると、くじに反映されます）")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("【結果】方向性の探り具合を out/growth/explore.md に書きました")


def set_mode(slug: str, m: str) -> None:
    if not load_artist(slug, missing_ok=True):
        sys.exit(f"[エラー] 組 {slug} が見つかりません")
    st = settings()
    st.setdefault("modes", {})[slug] = m
    write_json(SETTINGS, st)
    print(f"【結果】{slug} を{'固める（毎曲の癖を毎曲に戻し、伸びた方向性を 85% で選ぶ）' if m == 'focus' else '探索（毎曲の癖はたまに・方向性はいろいろ試す）'}にしました")


def main() -> None:
    ap = argparse.ArgumentParser(description="方向性を探る・固める")
    ap.add_argument("cmd", choices=["report", "learn", "focus", "explore", "pick"])
    ap.add_argument("slug", nargs="?")
    a = ap.parse_args()
    if a.cmd == "report":
        report()
    elif a.cmd == "learn":
        d = learn()
        print(f"【結果】方向性の伸びを学びました（成績のある曲 {d['songs']} 曲）")
    elif a.cmd in ("focus", "explore"):
        if not a.slug:
            sys.exit("[エラー] 組の slug を指定してください")
        set_mode(a.slug, a.cmd)
    else:
        art = load_artist(a.slug or "", missing_ok=True)
        if not art:
            sys.exit("[エラー] 組の slug を指定してください")
        e = pick(art)
        print(f"■ {art.get('name')}（{'固める' if e['mode'] == 'focus' else '探索中'}）の次の曲の方向性")
        for ax, x in e["axes"].items():
            print(f"   {AXIS_JA[ax]}：{x['ja']}（{int(x['p'] * 100)}%）")


if __name__ == "__main__":
    main()

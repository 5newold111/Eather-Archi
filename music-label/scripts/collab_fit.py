#!/usr/bin/env python3
"""
組と組のコラボの相性を吟味して、管理表の「コラボの相性」タブに書く（オーナーの指示 2026-10-26。決定 97）

  ・組が足されるたびに（artist_book.py pull のあと自動で）、まだ吟味していない組み合わせだけを Claude が見る
  ・1 組み合わせ（A と B）から 2 行：「A の曲に B が客演するとき」と「B の曲に A が客演するとき」
      相性（1〜5）・相性の理由・相手から取り込める強み（旋律・編成・歌い方）・組み方の案・気をつけること
  ・書いた行は『採用』（オーナーの指示）。管理表で直したり『却下』にしたりしてよい（直した値が使われる。却下の組み合わせは吟味し直さない）
  ・Claude の鍵が無いときは、ジャンル・声・BPM から仮の相性を書く（理由に「仮」と書く）
  ・ダッシュボードの「曲の案を作る」で「コラボする」にすると、この相性の高い順に相手を選べる。相手の強みを 3〜5 割、
    曲の構成を崩さない範囲で取り込む（write_brief.py の決まり 10）

使い方：
  python3 scripts/collab_fit.py            # まだ吟味していない組み合わせを吟味して書く
  python3 scripts/collab_fit.py --if-new   # 吟味していない組み合わせが無ければ何もしない（artist_book.py pull が使う）
  python3 scripts/collab_fit.py --show     # 組ごとの相性の一覧
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, all_artists, load_artist, load_dotenv  # noqa: E402

load_dotenv()
BLEND = "30-50%"

SYSTEM = """You judge how well two virtual music acts of the same label can collaborate (one sings or plays as a featured guest
on the other's song). You get both acts' profiles (sound, voice, composition habits, persona, scene). Write in Japanese.
Return: score (1-5; 5 = natural and exciting, 3 = workable with care, 1 = clashes), why (one or two sentences), and for each
direction - "a_song" (B is the guest on A's song) and "b_song" (A is the guest on B's song):
partner_brings (the guest's concrete strengths to bring into the host's song at about 30-50%: melodic habits, phrasing,
harmony, arrangement colour, rhythm feel - writable as music direction), how (a concrete way to combine them without breaking
the host's song structure: which sections, call-and-response, harmony layer, instrumental break, etc.), caution (what would
clash: range, tempo, mood, scene). Consider voice ranges and sexes (duet balance), tempo and genre distance, scenes, and
whether the guest's strengths add something new to the host. No real artist names."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["score", "why", "a_song", "b_song"],
          "properties": {"score": {"type": "integer"}, "why": {"type": "string"},
                         **{k: {"type": "object", "additionalProperties": False, "required": ["partner_brings", "how", "caution"],
                                "properties": {"partner_brings": {"type": "string"}, "how": {"type": "string"}, "caution": {"type": "string"}}}
                            for k in ("a_song", "b_song")}}}


def card(a: dict) -> dict:
    """Claude に渡す組の要約（実在の名前は入れない）"""
    import write_brief
    p = write_brief.public_safe_artist(a)
    v, snd = p.get("vocal") or {}, p.get("sound") or {}
    return {"name": p.get("name"), "formation": p.get("formation"),
            "sound": {k: snd.get(k) for k in ("genres", "dna_tags", "bpm_min", "bpm_max", "palette", "scene_spec") if snd.get(k)},
            "voice": {k: v.get(k) for k in ("sex", "voice_spec", "signature_techniques", "melodic_tendency", "guest_description") if v.get(k)},
            "composition_habits": p.get("composition_habits"), "scene": (p.get("persona") or {}).get("scene") or (p.get("persona") or {}).get("drive_scene"),
            "story": (p.get("persona") or {}).get("story")}


def _words(a: dict) -> set[str]:
    snd = a.get("sound") or {}
    return {w for t in (snd.get("genres") or []) + (snd.get("dna_tags") or []) for w in re.findall(r"[a-z]+", str(t).lower()) if len(w) > 2}


def rough(a: dict, b: dict) -> dict:
    """鍵が無いときの仮の相性：ジャンルの言葉の重なり・声の組み合わせ・BPM の重なり"""
    wa, wb = _words(a), _words(b)
    j = len(wa & wb) / max(1, len(wa | wb))
    sa, sb = (a.get("vocal") or {}).get("sex"), (b.get("vocal") or {}).get("sex")
    duet = 1 if {sa, sb} == {"male", "female"} else (0 if "none" in (sa, sb) else 0.5)
    ra, rb = a.get("sound") or {}, b.get("sound") or {}
    try:
        bpm = 1 if max(ra["bpm_min"], rb["bpm_min"]) <= min(ra["bpm_max"], rb["bpm_max"]) + 10 else 0
    except (KeyError, TypeError):
        bpm = 0.5
    score = max(1, min(5, round(1 + j * 6 + duet + bpm)))
    note = "（仮：Claude の鍵が無いので、ジャンルの言葉・声・BPM から計算）"
    side = lambda g: {"partner_brings": f"{g.get('name')} の声と音の色（{'・'.join(list((g.get('sound') or {}).get('dna_tags') or [])[:3])}）",  # noqa: E731
                      "how": "サビの後半のハモりか、2 番の掛け合いで入る", "caution": "テンポと声の高さが合うか、聴いて確かめる"}
    return {"score": score, "why": f"ジャンルの重なり {int(j * 100)}%・声の組み合わせ・BPM の近さから {note}", "a_song": side(b), "b_song": side(a)}


def judge(a: dict, b: dict) -> tuple[dict, str]:
    import _claude
    if not _claude.available():
        return rough(a, b), "仮"
    user = json.dumps({"A": card(a), "B": card(b)}, ensure_ascii=False)
    res, st = _claude.call_json(SYSTEM, user, SCHEMA, effort="medium", max_tokens=8000)
    if not res:
        return rough(a, b), f"仮（Claude で吟味できませんでした：{st}）"
    res["score"] = max(1, min(5, int(res.get("score") or 3)))
    return res, "Claude"


def book_rows() -> tuple[object, dict]:
    from _artist_book import open_book
    book = open_book()
    return book, book.read()


def missing_pairs(data: dict) -> list[tuple[str, str]]:
    from _artist_book import adopted
    ids = [x["id"] for x in adopted(data.get("acts", [])) if x.get("id")]
    have = {(r.get("act_id"), r.get("partner_id")) for r in data.get("collabs", [])}
    return [(x, y) for x, y in combinations(ids, 2) if (x, y) not in have and (y, x) not in have]


def run(if_new: bool) -> int:
    try:
        book, data = book_rows()
    except Exception as e:  # noqa: BLE001
        if if_new:
            return 0
        print(f"【結果】管理表を読めませんでした（{e}）。SSD をつないでから、もう一度動かしてください")
        return 2
    todo = missing_pairs(data)
    if not todo:
        if not if_new:
            print("【結果】吟味していない組み合わせはありません")
        return 0
    from _artist_book import append_rows
    print(f"▶ コラボの相性を吟味します：{len(todo)} 組み合わせ")
    rows, today = [], date.today().isoformat()
    for i, (x, y) in enumerate(todo, 1):
        a, b = load_artist(x, missing_ok=True), load_artist(y, missing_ok=True)
        if not a or not b:
            continue
        print(f"【進み具合】{i}/{len(todo)}：{a.get('name')} × {b.get('name')}", flush=True)
        r, how = judge(a, b)
        print(f"   相性 {r['score']}/5（{how}）：{r['why'][:80]}")
        for host, guest, side in ((x, y, "a_song"), (y, x, "b_song")):
            s = r.get(side) or {}
            rows.append({"act_id": host, "status": "採用", "partner_id": guest, "score": r["score"], "why": r["why"],
                         "partner_brings": s.get("partner_brings", ""), "how": s.get("how", ""), "caution": s.get("caution", ""),
                         "updated": today})
    where = append_rows(book, "collabs", rows)
    print(f"   管理表の「コラボの相性」に {len(rows)} 行を足しました：{where}")
    # 設定書に相性を入れ直す（pull の中から呼ばれたときも。次の pull では吟味する組み合わせが無いので、ここで止まる）
    subprocess.run([sys.executable, str(ROOT / "scripts" / "artist_book.py"), "pull", "--if-exists"], capture_output=True)
    print(f"【結果】{len(todo)} 組み合わせの相性を書きました")
    return 0


# ---------------------------------------------------------------------------
# 使う側（ダッシュボード・曲の案）
# ---------------------------------------------------------------------------
def partners_of(slug: str) -> list[dict]:
    """その組の曲に客演できる相手（相性の高い順）：[{slug, name, score, why, partner_brings, how, caution}]"""
    a = load_artist(slug, missing_ok=True) or {}
    names = {x.get("slug"): x.get("name") for x in all_artists()}
    out = [{**f, "slug": f.get("partner"), "name": names.get(f.get("partner"), f.get("partner"))}
           for f in (a.get("collab") or {}).get("fit") or [] if f.get("partner") in names]
    return sorted(out, key=lambda f: -(f.get("score") or 0))


def guest_for(host: str, guest: str) -> dict:
    """ブリーフの featured_guest：客演する組の要約と、取り込む強み（3〜5 割）"""
    import plan_collabs
    g = load_artist(guest)
    fit = next((f for f in partners_of(host) if f["slug"] == guest), {})
    v = g.get("vocal") or {}
    return {**plan_collabs.guest_card(g),
            "strengths": {"melodic_tendency": v.get("melodic_tendency") or {}, "composition_habits": g.get("composition_habits") or {},
                          "sound": {k: (g.get("sound") or {}).get(k) for k in ("genres", "dna_tags", "palette") if (g.get("sound") or {}).get(k)}},
            "fit": {k: fit.get(k, "") for k in ("score", "why", "partner_brings", "how", "caution")},
            "blend": {"ratio": BLEND, "rule": "keep the host's song structure and melodic spine; bring in the guest's strengths at about 30-50%"}}


def show() -> None:
    for a in all_artists():
        ps = partners_of(a["slug"])
        print(f"■ {a.get('name')}")
        for p in ps:
            print(f"   {'★' * int(p.get('score') or 0)}{'☆' * (5 - int(p.get('score') or 0))} {p['name']}：{p.get('why', '')[:70]}")
        if not ps:
            print("   －（まだ吟味していません）")


def main() -> None:
    ap = argparse.ArgumentParser(description="組と組のコラボの相性")
    ap.add_argument("--if-new", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show:
        show()
        return
    sys.exit(run(a.if_new))


if __name__ == "__main__":
    main()

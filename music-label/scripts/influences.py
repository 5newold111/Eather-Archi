#!/usr/bin/env python3
"""
影響を受けたアーティストを、SSD のデータベース（SSDDB：参考データベースと「既存楽曲」）の中のアーティストから選び、
組の「影響」に足して、その人たちの曲を今後の曲づくりの参考に優先して使う（オーナーの指示 2026-10-25。決定 91）

流れ：
  1 候補：SSDDB の曲をアーティストごとにまとめ、組の音（ジャンル・楽器・雰囲気の言葉・テンポ・声の性別）との近さで並べる
  2 選ぶ：Claude が組の設定書（音・声・人生）と候補の特徴を見て、組ごとに 3 組まで選ぶ（候補の中からだけ）。
          受け取ったもの（taken）と、どこに表れているか（shows_in：声・演奏・曲づくり）も書く。鍵が無ければ近さの上位 3 組
  3 記録（--apply）：管理表（SSD の アーティスト情報.xlsx）の「影響」タブに『採用』で足す → 設定書を作り直す（artist_book.py pull）。
          out/reference_db/influences.json にも残す（参考曲の割り当て・歌詞の聞き取り・メロディー・Web の調べ物で、その人たちの曲を先に使う）
  実在の名前は「影響」タブと参考曲の記録だけに入る。Suno の指示文・画像の依頼文には入れない（これまでどおり）

使い方：
  python3 scripts/influences.py              # 候補と選んだ結果を見るだけ（管理表は変えない）
  python3 scripts/influences.py --apply      # 管理表の「影響」に足して、設定書を作り直す
  python3 scripts/influences.py --artist tidewool --max 2
  python3 scripts/influences.py --analyze    # 影響を受けたアーティストの曲の歌詞・メロディーを先に起こす（Mac の中・無料）
  python3 scripts/influences.py --analyze --enrich   # Web の調べ物も（Claude・1 曲 20〜50 円）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_dotenv  # noqa: E402

load_dotenv()
import reference_db as rdb  # noqa: E402

INFLUENCES = OUT / "reference_db" / "influences.json"


def load() -> dict:
    try:
        return json.loads(INFLUENCES.read_text(encoding="utf-8")) if INFLUENCES.exists() else {}
    except (OSError, ValueError):
        return {}


def names_for(slug: str) -> set[str]:
    """その組が影響を受けたアーティスト（比べるための形）"""
    got = load().get(slug) or {}
    return {rdb.akey(x["artist"]) for x in got.get("picks", [])} if got.get("applied") else set()


def db_artists() -> dict[str, dict]:
    """SSDDB のアーティストごとのまとめ：{名前: {songs, ids, tags, bpm, vocal, instruments, themes, titles}}"""
    own = {rdb.akey(a.get("name")) for a in all_artists()}
    out: dict[str, dict] = {}
    for p in sorted(rdb.REF_DIR.glob(f"{rdb.PREFIX}*.json")) if rdb.REF_DIR.exists() else []:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = str((s.get("reference") or {}).get("artist_name") or "").strip()
        if not name or name == "?" or rdb.akey(name) in own:
            continue
        a = out.setdefault(name, {"songs": [], "tags": Counter(), "bpm": [], "vocal": Counter(), "instruments": Counter(), "themes": []})
        a["songs"].append(s)
        a["tags"].update(rdb.norm(t) for t in (s.get("reference") or {}).get("tags", []) if t)
        if isinstance(b := (s.get("groove") or {}).get("bpm"), (int, float)) and b > 0:
            a["bpm"].append(b)
        if (v := (s.get("vocal") or {}).get("sex")):
            a["vocal"][v] += 1
        a["instruments"].update(i.get("name", "") for i in (s.get("instruments") or {}).get("list", []) if i.get("name"))
        if (t := (s.get("lyrics") or {}).get("theme")):
            a["themes"].append(t)
    return out


def candidates(artist: dict, dbs: dict[str, dict], top: int = 12) -> list[dict]:
    """組の音に近い順の候補（曲ごとの近さの上位 3 曲の平均）。設定書の「影響」に既にいる人は、近さに関係なく候補に入れる"""
    aw = rdb.artist_words(artist)
    listed = {rdb.akey(i.get("name")) for i in (artist.get("persona") or {}).get("influences", [])} - {""}
    rows = []
    for name, a in dbs.items():
        # 影響の加点（+1）を除いた、音そのものの近さで並べる
        sc = sorted((v - 1.0 if v > 1 else v for v in (rdb.affinity(s, artist, aw)[0] for s in a["songs"])), reverse=True)
        score = sum(sc[:3]) / min(3, len(sc))
        rows.append({"artist": name, "score": round(score, 3), "songs": len(a["songs"]), "already_listed": rdb.akey(name) in listed,
                     "titles": [(s.get("reference") or {}).get("title") for s in a["songs"]][:5],
                     "tags": [t for t, _ in a["tags"].most_common(6)],
                     "bpm": f"{min(a['bpm']):.0f}〜{max(a['bpm']):.0f}" if a["bpm"] else "",
                     "vocal": a["vocal"].most_common(1)[0][0] if a["vocal"] else "",
                     "instruments": [t for t, _ in a["instruments"].most_common(5)], "themes": a["themes"][:3]})
    rows.sort(key=lambda r: (-r["score"], -r["songs"]))
    return [r for r in rows if r["already_listed"]] + [r for r in rows if not r["already_listed"]][:top]


PICK_SYSTEM = """You help a music label give each of its virtual acts a set of musical influences. You get one act's profile
(sound, voice, persona, life) and a list of real artists that exist in the owner's reference library, with what is known
about their songs, plus the members and a short life timeline. Choose up to N artists FROM THE LIST ONLY that would plausibly have shaped this act - by sound, voice,
arrangement, mood or attitude - and that the act's next songs can learn from. Prefer artists with more songs in the library
when the fit is similar. Artists marked already_listed are in the act's profile already: keep them when they fit, and use
the remaining picks for new ones that add something different. For each pick give: member_id (the member who brought it in, from the members list; "" for the whole act), title (the song from
that artist's titles that mattered most), and in Japanese: when_met (when and where that member met this music - one sentence,
consistent with the timeline and ages, concrete place and situation), taken (what they took from it, concrete: e.g. 低く抑えた歌い方,
ゲートリバーブのドラム, 長い間奏), shows_in (where it shows in the act's sound now: voice, playing, song shape - concrete habits),
and why (one sentence on why it fits). Write like the existing profile: plain, specific, no flourish.
If nothing in the list fits, return fewer picks. Never invent artists that are not in the list."""

PICK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["picks"],
               "properties": {"picks": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                    "required": ["artist", "member_id", "title", "when_met", "taken", "shows_in", "why"],
                                                                    "properties": {"artist": {"type": "string"}, "member_id": {"type": "string"},
                                                                                   "title": {"type": "string"}, "when_met": {"type": "string"}, "taken": {"type": "string"},
                                                                                   "shows_in": {"type": "string"}, "why": {"type": "string"}}}}}}


def choose(artist: dict, cands: list[dict], n: int) -> tuple[list[dict], str]:
    """Claude が候補から選ぶ。鍵が無ければ近さの上位"""
    import _claude
    import write_brief
    if not cands:
        return [], "候補なし"
    if _claude.available():
        prof = write_brief.public_safe_artist(artist)
        prof = {k: prof.get(k) for k in ("name", "persona", "vocal", "sound", "composition_habits", "lyrics", "life_context") if prof.get(k)}
        life = artist.get("life") or {}
        members = [{k: m.get(k) for k in ("id", "name", "role", "age", "birthplace")} for m in life.get("members", [])]
        timeline = [{k: t.get(k) for k in ("member_id", "years", "place", "life")} for t in life.get("timeline", [])][:24]
        user = json.dumps({"N": n, "act": prof, "members": members, "timeline": timeline, "library_artists": cands}, ensure_ascii=False)
        res, st = _claude.call_json(PICK_SYSTEM, user, PICK_SCHEMA, effort="medium", max_tokens=16000)
        if res:
            by = {c["artist"]: c for c in cands}
            ids = {m.get("id") for m in members}
            picks = [p for p in res.get("picks", []) if p.get("artist") in by][:n]
            for p in picks:
                c = by[p["artist"]]
                p.update(by="claude", score=c["score"], member_id=p.get("member_id") if p.get("member_id") in ids else "",
                         title=p.get("title") if p.get("title") in c["titles"] else (c["titles"] or [""])[0])
            return picks, st
        note = f"Claude で選べなかったので、近さの上位にしました（{st}）"
    else:
        note = "Claude の鍵が無いので、近さの上位にしました"
    return [{"artist": c["artist"], "member_id": "", "title": (c["titles"] or [""])[0], "when_met": "",
             "taken": "音の近さ：" + ("・".join(c["tags"][:3]) or "テンポ・声"), "shows_in": "曲づくり",
             "why": f"音の近さ {c['score']:.2f}（{c['songs']} 曲）", "by": "affinity", "score": c["score"]}
            for c in cands if c["score"] >= 0.4 or c["already_listed"]][:n], note   # 0.3 以下は言葉が重ならない曲


def apply_to_book(slug: str, picks: list[dict], dbs: dict[str, dict]) -> str:
    """管理表の「影響」タブに『採用』で足す（同じアーティストが既にあれば足さない）"""
    from _artist_book import append_rows, open_book
    book = open_book()
    data = book.read()
    have = {rdb.akey(r.get("author") or r.get("title")) for r in data.get("influences", []) if r.get("act_id") == slug}
    rows = []
    for p in picks:
        if rdb.akey(p["artist"]) in have:
            continue
        titles = [t for t in ((s.get("reference") or {}).get("title") for s in dbs.get(p["artist"], {}).get("songs", [])) if t][:5]
        rows.append({"act_id": slug, "member_id": p.get("member_id", ""), "status": "採用", "kind": "音楽",
                     "title": p.get("title") or p["artist"], "author": p["artist"],
                     "work": "SSDDB の曲：" + "・".join(f"『{t}』" for t in titles), "when_met": p.get("when_met", ""),
                     "taken": p.get("taken", ""), "shows_in": p.get("shows_in", "")})
    return append_rows(book, "influences", rows) if rows else ""


def analyze(enrich: bool) -> int:
    """影響を受けたアーティストの曲を先に解析する（並び順で先頭に来るので、その曲数だけ動かす）"""
    n = rdb.influence_pending()
    print(f"▶ 影響を受けたアーティストの曲：歌詞がまだ {n['lyrics']} 曲・メロディーがまだ {n['melody']} 曲・Web の調べ物がまだ {n['enrich']} 曲")
    if not any(n.values()):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "reference_db.py"), "--artists"])   # 組ごとの近い参考曲は作り直す
        print("【結果】影響を受けたアーティストの曲は、全部解析済みです（SSD がつながっていないときも 0 になります）")
        return 0
    if n["lyrics"]:
        print("【進み具合】歌詞を聞き取ります", flush=True)
        rdb.audio_lyrics(n["lyrics"])
    if n["melody"]:
        print("【進み具合】メロディーを起こします（MIDI にも残します）", flush=True)
        rdb.audio_melody(n["melody"])
    if n["enrich"] and enrich:
        print("【進み具合】Claude に Web で調べてもらいます", flush=True)
        rdb.audio_enrich(n["enrich"])
    elif n["enrich"]:
        print(f"   Web の調べ物は {n['enrich']} 曲残っています（料金がかかるので、ダッシュボードの「足りない項目を Claude に調べてもらう」で。影響の曲から先に調べます）")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "reference_db.py"), "--artists"])
    print("【結果】影響を受けたアーティストの曲を解析して、組ごとの参考曲を作り直しました")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="SSDDB の中から、組が影響を受けたアーティストを選んで足す")
    ap.add_argument("--apply", action="store_true", help="管理表の「影響」に足して、設定書を作り直す")
    ap.add_argument("--artist", help="この組だけ（slug）")
    ap.add_argument("--max", type=int, default=3, help="1 組に何組まで（既定 3）")
    ap.add_argument("--analyze", action="store_true", help="影響を受けたアーティストの曲の歌詞・メロディーを起こす（選び直さない）")
    ap.add_argument("--enrich", action="store_true", help="--analyze のとき、Web の調べ物も（Claude・料金あり）")
    a = ap.parse_args()
    if a.analyze:
        sys.exit(analyze(a.enrich))
    dbs = db_artists()
    if not dbs:
        print("【結果】SSDDB にアーティストがまだありません（「既存楽曲を解析する」で取り込んでから、もう一度動かしてください）")
        sys.exit(2)
    print(f"▶ SSDDB のアーティスト {len(dbs)} 組・曲 {sum(len(x['songs']) for x in dbs.values())} 曲から選びます")
    arts = [x for x in all_artists() if not a.artist or x.get("slug") == a.artist]
    saved = load()
    lines, n_total = [], 0
    for i, art in enumerate(arts, 1):
        print(f"【進み具合】{i}/{len(arts)} 組目：{art.get('name')} の影響を選んでいます", flush=True)
        cands = candidates(art, dbs)
        picks, note = choose(art, cands, a.max)
        print(f"■ {art.get('name')}（候補 {len(cands)} 組）{'：' + note if note and not note.startswith('OK') else ''}")
        for p in picks:
            print(f"   ○ {p['artist']}　受け取ったもの：{p['taken']}／表れているところ：{p['shows_in']}　（{p['why']}）")
        if not picks:
            print("   － 合う候補がありませんでした")
        if a.apply or not (saved.get(art["slug"]) or {}).get("applied"):   # 見るだけのときは、足した結果を上書きしない
            saved[art["slug"]] = {"picks": picks, "at": datetime.now().isoformat(timespec="seconds"), "applied": bool(a.apply)}
        n_total += len(picks)
        lines.append(f"{art.get('name')} {len(picks)} 組")
        if a.apply and picks:
            try:
                where = apply_to_book(art["slug"], picks, dbs)
                print(f"   管理表の「影響」に足しました：{where}" if where else "   （管理表には同じアーティストが既にあります）")
            except Exception as e:  # noqa: BLE001
                print(f"   ✕ 管理表に書けませんでした（{e}）。SSD をつないでから、もう一度 --apply で動かしてください")
    INFLUENCES.parent.mkdir(parents=True, exist_ok=True)
    INFLUENCES.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.apply:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "artist_book.py"), "pull", "--if-exists"])   # 設定書を作り直す
        analyze(False)   # 影響を受けたアーティストの曲の歌詞・メロディーを先に起こす（無料。組ごとの参考曲も作り直す）
    else:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "reference_db.py"), "--artists"])   # 組ごとの近い参考曲を作り直す（影響の曲を先に）
    print(f"【結果】{'・'.join(lines)}（合わせて {n_total} 組）"
          + ("。管理表の「影響」に足して設定書を作り直し、参考曲の優先にも入れました" if a.apply else "。見るだけでした（足すときは --apply）"))


if __name__ == "__main__":
    main()

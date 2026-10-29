#!/usr/bin/env python3
"""
毎日配信（5 組の持ち回り）：期間を決めて、レーベル全体で毎日 1 曲を出す（オーナーの指示 2026-10-26。決定 94）

  ・1 日 1 曲。組は持ち回り（5 組なら、それぞれ 5 日ごとに 1 曲。1 組あたり週 1〜2 曲・30 日で 30 曲）
    → Suno のダウンロード（Premier は月 60 本）と、週の総曲数の上限（8 曲）の中に収まる
  ・配信日は「その日の 17:00 ET」に固定する（いちばん早い日ではない）。Spotify の公式プレイリストへの推薦（ピッチ）は
    配信の 7 日前までなので、曲は配信日の 1〜3 週間前に作って登録する（make --ahead 21）
  ・期間中、その組のふだんの毎週の制作は止める（配信日が期間に入る週。毎日配信の曲が代わりになる）。
    もう作ってある毎週の曲（まだ登録していないもの）は、その組の近い日の枠に入れる
  ・曲の置き場は不定期配信と同じ（その組の曲が無い過去の制作週の枠を借りる）。歌詞は Claude が全部書く
  ・ダッシュボードでは「不定期・毎日配信」のタブに並ぶ

使い方：
  python3 scripts/daily.py start --from 2026-11-16 --days 30   # 期間を決める（既定：2 週間後の月曜から 30 日）
  python3 scripts/daily.py plan            # 日ごとの組と、曲があるか
  python3 scripts/daily.py make            # 21 日先までの空いている日の曲を作る（参考曲の割り当て → Claude が書く）
  python3 scripts/daily.py status          # 日ごとの進み具合
  python3 scripts/daily.py stop            # やめる（ふだんの毎週の制作に戻る。作った曲はそのまま）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, SCENE_TYPES, load_artist, load_dotenv, monday_of, read_json, step, write_json  # noqa: E402

load_dotenv()
PY = sys.executable
S = ROOT / "scripts"
BRIEFS = OUT / "briefs"
SETTINGS = OUT / "daily" / "settings.json"
DEFAULT_DAYS = 30
LEAD_DAYS = 14   # 始める日は、今日から 2 週間あける（作曲・登録・ピッチの 7 日前に間に合わせる）


def settings() -> dict:
    try:
        return read_json(SETTINGS) if SETTINGS.exists() else {}
    except (OSError, ValueError):
        return {}


def window(st: dict | None = None) -> tuple[date, date] | None:
    """毎日配信の期間 [始め, 終わりの翌日)。止めていれば None"""
    st = settings() if st is None else st
    if not st.get("start") or st.get("stopped"):
        return None
    s = date.fromisoformat(st["start"])
    return s, s + timedelta(days=int(st.get("days") or DEFAULT_DAYS))


def in_rotation(slug: str, day: date) -> bool:
    """その日が毎日配信の期間で、この組が持ち回りに入っているか（_common.production_status が使う）"""
    st = settings()
    w = window(st)
    return bool(w and w[0] <= day < w[1] and slug in (st.get("order") or []))


def release_at(day: date, hour_et: int = 17) -> str:
    from zoneinfo import ZoneInfo
    t = datetime.combine(day, datetime.min.time().replace(hour=hour_et), tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("UTC"))
    return t.isoformat().replace("+00:00", "Z")


def slots(st: dict | None = None) -> list[dict]:
    """[{date, slug}]：期間の日ごとの組（持ち回り）"""
    st = settings() if st is None else st
    w, order = window(st), st.get("order") or []
    if not w or not order:
        return []
    return [{"date": (w[0] + timedelta(days=i)).isoformat(), "slug": order[i % len(order)]} for i in range((w[1] - w[0]).days)]


def daily_songs() -> dict[str, dict]:
    """毎日配信の枠に入っている曲：{日付: 曲}"""
    import _songs
    out = {}
    for s in _songs.weekly_songs():
        d = (s.get("daily") or {}).get("date")
        if d:
            out[d] = s
    return out


def mark(path: Path, day: date, tag: str, hour_et: int) -> None:
    """ブリーフを毎日配信の曲にする（配信日をその日の 17:00 ET に固定）"""
    b = read_json(path)
    b["daily"] = {"id": tag, "date": day.isoformat(), "marked_at": date.today().isoformat()}
    b["release_at"] = release_at(day, hour_et)
    b.pop("sprint", None)
    path.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scene_for(slug: str, k: int) -> str:
    """場面は、その組の場面の割合の高い順に回す（同じ場面に偏らないように）"""
    import sprint
    a = load_artist(slug) or {}
    order = sprint.scene_order(a, [])
    return order[k % len(order)] if order else next(iter(SCENE_TYPES))


# ---------------------------------------------------------------------------
def cmd_start(start: str | None, days: int, order: str | None, force: bool) -> int:
    import irregular
    import suno_quota
    if suno_quota.on() and int(suno_quota.settings()["limit"]) < 40:   # 毎日配信は Suno のダウンロード枠（Pro は月 20 曲）に入らない（決定 105）
        sys.exit("[停止] Suno のダウンロード枠に合わせているあいだは、毎日配信は使えません（Premier にしたら  python3 scripts/suno_quota.py set --limit 60 --plan-name Premier）")
    live = [a["slug"] for a in irregular.eligible()]
    want = [x.strip() for x in order.split(",")] if order else live
    bad = [x for x in want if x not in live]
    if bad:
        sys.exit(f"[停止] デビュー曲が配信中ではない組があります：{', '.join(bad)}（毎日配信に入れられるのは配信中の組だけ）")
    if not want:
        sys.exit("[停止] デビュー曲が配信中の組がまだありません")
    earliest = date.today() + timedelta(days=LEAD_DAYS)
    day = date.fromisoformat(start) if start else monday_of(earliest + timedelta(days=6))
    if day < earliest and not force:
        sys.exit(f"[停止] 始める日 {day} が近すぎます。{earliest} 以降にしてください（作曲・登録・Spotify への推薦は配信の 7 日前まで）。"
                 "それでも始めるときは --force")
    st = {"start": day.isoformat(), "days": days, "order": want, "hour_et": 17, "created_at": date.today().isoformat()}
    write_json(SETTINGS, st)
    end = day + timedelta(days=days - 1)
    print(f"【結果】毎日配信を {day} から {end} まで（{days} 日・{days} 曲）にしました。持ち回り：{'→'.join(want)}")
    print("   この期間に配信日が入るふだんの毎週の制作は止めます（毎日配信の曲が代わりになります）")
    print("   次：  python3 scripts/daily.py make   （21 日先までの曲を作る。毎週火曜の定期実行でも続きを作ります）")
    return 0


def cmd_stop() -> int:
    st = settings()
    if not st:
        print("【結果】毎日配信は始めていません")
        return 0
    st["stopped"] = date.today().isoformat()
    write_json(SETTINGS, st)
    print("【結果】毎日配信を止めました（ふだんの毎週の制作に戻ります。作った曲と配信日はそのまま）")
    return 0


def cmd_plan(quiet: bool = False) -> list[dict]:
    st = settings()
    sl = slots(st)
    if not sl:
        if not quiet:
            print("【結果】毎日配信は始めていません（python3 scripts/daily.py start）")
        return []
    import _songs
    have = daily_songs()
    rows = []
    for x in sl:
        s = have.get(x["date"])
        a = load_artist(x["slug"]) or {}
        rows.append({**x, "artist": a.get("name", x["slug"]), "song": s,
                     "status": (_songs.state(s).get("status") or "作曲待ち") if s else "まだ作っていない"})
    if not quiet:
        w = window(st)
        print(f"▶ 毎日配信 {w[0]}〜{w[1] - timedelta(days=1)}（{len(sl)} 日）")
        for r in rows:
            t = (r["song"] or {}).get("title") or ""
            print(f"   {r['date']}　{r['artist']}　{('「' + t + '」') if t else ''}{r['status']}")
        n = sum(1 for r in rows if r["song"])
        print(f"【結果】{len(rows)} 日のうち {n} 日ぶんの曲があります")
    return rows


def cmd_make(ahead: int, dry: bool, if_active: bool) -> int:
    import _songs
    import irregular
    st = settings()
    if not slots(st):
        if if_active:
            return 0
        sys.exit("[停止] 毎日配信は始めていません（python3 scripts/daily.py start）")
    tag = f"daily-{st['start']}"
    hour = int(st.get("hour_et") or 17)
    limit = date.today() + timedelta(days=ahead)
    have = daily_songs()
    todo = [x for x in slots(st) if x["date"] not in have and date.fromisoformat(x["date"]) <= limit
            and date.fromisoformat(x["date"]) > date.today()]
    if not todo:
        print(f"【結果】{limit} までの毎日配信の曲は、全部そろっています")
        return 0
    # 1 もう作ってある毎週の曲（まだ登録していない・配信日が期間の中）を、その組の近い日の枠に入れる
    w = window(st)
    for s in _songs.weekly_songs():
        if s.get("daily") or s.get("irregular") or s.get("sprint") or not s.get("brief"):
            continue
        if _songs.state(s).get("status") in ("registered", "live"):
            continue
        rel = str(s.get("release_at") or "")[:10]
        if not rel or not (w[0] <= date.fromisoformat(rel) < w[1]):
            continue
        mine = [x for x in todo if x["slug"] == s["slug"]]
        if mine:
            x = min(mine, key=lambda y: abs((date.fromisoformat(y["date"]) - date.fromisoformat(rel)).days))
            if not dry:
                mark(Path(s["brief"]), date.fromisoformat(x["date"]), tag, hour)
            todo.remove(x)
            print(f"   ・{s['artist']}：作ってあった曲「{s.get('title') or s['week']}」を {x['date']} の枠に入れました")
    if not todo:
        return 0
    # 2 足りない日の曲を作る（参考曲の割り当て → Claude が全部書く）
    refs = OUT / "references"
    step("SSD の参考データベースを読んでいます（reference_db.py。SSD が無ければ前回の取り込みを使う）")
    if not dry:
        subprocess.run([PY, S / "reference_db.py", "--quiet"])
    n = sum(1 for x in refs.glob("*.json") if not x.name.endswith(".measure.json")) if refs.exists() else 0
    if n < 11 and not dry:
        sys.exit(f"[停止] 参考曲の解析シートが {n} 件しかありません（11 件以上必要）。SSD をつないでから、もう一度実行してください")
    made = []
    count: dict[str, int] = {}
    for i, x in enumerate(todo, 1):
        a = load_artist(x["slug"]) or {}
        k = sum(1 for s in have.values() if s["slug"] == x["slug"]) + count.get(x["slug"], 0)
        scene = scene_for(x["slug"], k)
        count[x["slug"]] = count.get(x["slug"], 0) + 1
        week = irregular.free_week(x["slug"])
        print(f"【進み具合】{i}/{len(todo)} 曲目：{x['date']} の {a.get('name', x['slug'])}（{SCENE_TYPES[scene]['name']}の曲）の参考曲を割り当てています", flush=True)
        if dry:
            continue
        code = subprocess.run([PY, S / "select_references.py", "--artist", x["slug"], "--week", week.isoformat(), "--force",
                               "--scene", scene, "--references", refs]).returncode
        bp = BRIEFS / f"{week}_{x['slug']}.json"
        if code != 0 or not bp.exists():
            print(f"   ⚠ {x['date']} の {a.get('name')} の曲を作れませんでした（次の実行でもう一度試します）")
            continue
        mark(bp, date.fromisoformat(x["date"]), tag, hour)
        made.append((bp, (a.get("lyrics") or {}).get("mode") == "instrumental"))
    for inst in (False, True):
        group = [str(bp) for bp, i in made if i == inst]
        if group:
            step(f"Claude が曲の案を書いています（{len(group)} 曲）")
            subprocess.run([PY, S / "write_brief.py", *group, "--references", refs, *([] if inst else ["--lyrics-mode", "full"])])
    print(f"【結果】毎日配信の曲を {len(made)} 曲作りました（{limit} まで）。次は Suno で作曲 → 仕上げ → DistroKid 登録です"
          "（配信日は決まった日。Spotify への推薦は配信の 7 日前までに）")
    return 0


def summary() -> dict:
    """ダッシュボード用：{active, start, end, days, order, filled, next}"""
    st = settings()
    w = window(st)
    if not w:
        return {"active": False, "earliest": monday_of(date.today() + timedelta(days=LEAD_DAYS + 6)).isoformat()}
    have = daily_songs()
    sl = slots(st)
    name = lambda s: (load_artist(s) or {}).get("name", s)  # noqa: E731
    nx = next((x for x in sl if x["date"] >= date.today().isoformat()), None)
    return {"active": True, "start": w[0].isoformat(), "end": (w[1] - timedelta(days=1)).isoformat(), "days": len(sl),
            "order": [name(s) for s in st.get("order") or []], "filled": sum(1 for x in sl if x["date"] in have),
            "next": {**nx, "slug": name(nx["slug"])} if nx else None}


def main() -> None:
    ap = argparse.ArgumentParser(description="毎日配信（5 組の持ち回り）")
    ap.add_argument("cmd", choices=["start", "stop", "plan", "make", "status"])
    ap.add_argument("--from", dest="start", help="始める日（YYYY-MM-DD。既定：2 週間後の月曜）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--order", help="持ち回りの順（slug をカンマ区切り。既定：デビュー曲が配信中の組）")
    ap.add_argument("--ahead", type=int, default=21, help="make：何日先までの曲を作るか")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--if-active", action="store_true", help="make：毎日配信をしていなければ何もしない（定期実行用）")
    a = ap.parse_args()
    if a.cmd == "start":
        sys.exit(cmd_start(a.start, a.days, a.order, a.force))
    if a.cmd == "stop":
        sys.exit(cmd_stop())
    if a.cmd in ("plan", "status"):
        cmd_plan()
        return
    sys.exit(cmd_make(a.ahead, a.dry_run, a.if_active))


if __name__ == "__main__":
    main()

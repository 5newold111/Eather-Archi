#!/usr/bin/env python3
"""
Suno のダウンロードの枠（Pro は月 20 曲。契約の更新日にリセット）に、制作と配信日を合わせる（オーナーの指示 2026-10-27。決定 105）

  Suno は 2026-09-03 から、ダウンロードできる曲の数に月の上限を付けた（Pro 20・Premier 60。余りは繰り越さない。
  同じ曲のダウンロードし直しは数えない）。作曲（クレジット）は Pro で月 2,500（約 500 曲）あるので、効く上限はダウンロードの方。

  1 数える：曲を取り込むたびに 1 曲として記録（out/suno/downloads.jsonl。同じファイルは 2 回数えない）。
     Suno の画面の残りと合わないときは、ダッシュボードで「今の残り」を入れる（その時点からの取り込みを引いて数える）
  2 予定を合わせる（plan）：作曲待ちの曲を、配信日の早い順に、ダウンロードできる月（更新日から次の更新日まで）に割り当てる。
     1 か月に入れるのは「上限 − 予備」（予備は作り直し用。既定 2）。今の月は「残り − 予備」。
     ダウンロードできる日から配信日まで 7 日あかないときは、配信日を後ろの配信曜日にずらす（ブリーフに元の日を残す）
  3 制作を絞る（_common.production_status から）：組ごとの月の割り当て（上限 − 予備 を組の数で分ける。余りは月ごとに順番に）を
     超える週は作らない。今の月の制作だけは次の月の枠を先に使える（いま作って、ダウンロードと配信を後ろにずらす）
  4 毎日配信（決定 94）は枠に入らないので、枠を使うあいだは止める
  5 作曲の質の確認（決定 100）は、枠を使うあいだは自動で作り直さない（作り直すとダウンロードを 1 曲分使う）。「聴いて決めて」と知らせる

使い方：
  python3 scripts/suno_quota.py            # 残り・リセット日・月ごとの割り当て
  python3 scripts/suno_quota.py plan       # 作曲待ちの曲を枠に割り当て、必要なら配信日を後ろにずらす（定期実行が使う）
  python3 scripts/suno_quota.py left 0     # Suno の画面の「残り」を入れる（数え直し）
  python3 scripts/suno_quota.py set --limit 20 --reset-day 4 --reserve 2   # 枠の設定（プランを変えたとき）
  python3 scripts/suno_quota.py off|on     # 枠に合わせるのをやめる・戻す（Premier にしたときなど）
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, WEEKDAY_OFFSET, all_artists, as_date, load_label, read_json, write_json  # noqa: E402

DIR = OUT / "suno"
SETTINGS = DIR / "quota.json"
LOG = DIR / "downloads.jsonl"
PLAN = DIR / "plan.json"
DEFAULT = {"on": True, "plan": "Pro", "limit": 20, "reset_day": 4, "reserve": 2, "after_download_days": 7,
           # 始めたときの Suno の画面（2026-10-27）：残り 0・11/4 にリセット。10/4〜11/3 の月だけに効く
           "left_set": {"left": 0, "at": "2026-10-27T00:00:00", "cycle": "2026-10-04"}}


def settings() -> dict:
    try:
        st = read_json(SETTINGS) if SETTINGS.exists() else {}
    except (OSError, ValueError):
        st = {}
    return {**DEFAULT, **st}


def on() -> bool:
    return bool(settings().get("on"))


# ---------------------------------------------------------------------------
# 月（更新日から次の更新日まで）
def _on_day(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def cycle_of(d: date, st: dict | None = None) -> date:
    """d が入る月の始まり（更新日）"""
    rd = int((st or settings())["reset_day"])
    here = _on_day(d.year, d.month, rd)
    if d >= here:
        return here
    y, m = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return _on_day(y, m, rd)


def next_cycle(c: date, st: dict | None = None) -> date:
    rd = int((st or settings())["reset_day"])
    y, m = (c.year + 1, 1) if c.month == 12 else (c.year, c.month + 1)
    return _on_day(y, m, rd)


# ---------------------------------------------------------------------------
# 1 数える
def _log() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _fingerprint(f: Path) -> str:
    h = hashlib.sha1()
    with open(f, "rb") as fh:
        h.update(fh.read(1 << 20))
    return f"{h.hexdigest()}-{f.stat().st_size}"


def log_download(song: dict, f: Path) -> None:
    """取り込んだ 1 曲を、Suno のダウンロード 1 曲として記録する（publish.take_in が呼ぶ）"""
    try:
        fp = _fingerprint(f)
    except OSError:
        fp = f"{f.name}-?"
    if any(x.get("fp") == fp for x in _log()):
        return   # 同じファイル（ダウンロードし直し・取り込み直し）は数えない
    DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "song": song.get("id"), "slug": song.get("slug"),
                             "file": f.name, "fp": fp}, ensure_ascii=False) + "\n")


def used(c: date, st: dict | None = None) -> list[dict]:
    st = st or settings()
    return [x for x in _log() if cycle_of(datetime.fromisoformat(x["at"]).date(), st) == c]


def left(today: date | None = None, st: dict | None = None) -> int:
    """今の月に、あと何曲ダウンロードできるか"""
    st = st or settings()
    today = today or date.today()
    c = cycle_of(today, st)
    ls = st.get("left_set") or {}
    if ls.get("cycle") == c.isoformat():   # Gon さんが入れた残りから、そのあとの取り込みを引く
        after = [x for x in _log() if x["at"] > ls["at"]]
        return max(0, int(ls["left"]) - len(after))
    return max(0, int(st["limit"]) - len(used(c, st)))


# ---------------------------------------------------------------------------
# 2 予定を合わせる
def _acts() -> list[dict]:
    import _songs
    return sorted((a for a in all_artists() if a.get("cadence") != "paused" and _songs.debut_registered(a.get("slug", "")) is not False),
                  key=lambda a: a.get("slug", ""))


def share(slug: str, c: date, st: dict | None = None) -> int:
    """その月に、この組が使える曲数（上限 − 予備 を組の数で分ける。余りは月ごとに順番に）"""
    st = st or settings()
    acts = [a["slug"] for a in _acts()]
    if slug not in acts:
        return 0
    cap = max(0, int(st["limit"]) - int(st["reserve"]))
    base, extra = divmod(cap, len(acts))
    k = (c.year * 12 + c.month) % len(acts)
    return base + (1 if (acts.index(slug) - k) % len(acts) < extra else 0)


def room(c: date, st: dict | None = None, today: date | None = None) -> int:
    """その月に新しく割り当てられる曲数（作曲待ちの割り当ては引く前）"""
    st = st or settings()
    today = today or date.today()
    if c == cycle_of(today, st):
        return max(0, left(today, st) - int(st["reserve"]))
    return max(0, int(st["limit"]) - int(st["reserve"]))


def _pending() -> list[dict]:
    """作曲待ち（まだダウンロードしていない）の曲。配信日の早い順"""
    import _songs
    out = []
    for s in _songs.all_songs():
        if str(s.get("week", "")).startswith("2000-") or _songs.state(s).get("status"):
            continue
        if s["kind"] != "debut" and not s.get("title"):
            continue
        rel = str((s.get("daily") or {}).get("date") or s.get("release_at") or "")[:10]
        out.append((0 if s["kind"] == "debut" else 1, rel or "9999", str(s.get("week") or ""), s))
    return [x[3] for x in sorted(out, key=lambda x: x[:3])]


def _release_day(s: dict) -> date | None:
    d = (s.get("daily") or {}).get("date") or s.get("release_at")
    return as_date(str(d)[:10]) if d else None


def _new_release(s: dict, earliest: date) -> tuple[str, date]:
    """earliest 以降で、その組の配信曜日・時刻の日時（UTC の文字列, 日）"""
    from zoneinfo import ZoneInfo
    from _common import load_artist, release_schedule
    a = load_artist(s["slug"], s.get("label_slug"), missing_ok=True) or {}
    wd, hr = release_schedule(a, load_label(s.get("label_slug")))
    want = WEEKDAY_OFFSET.get(wd, 2)
    d = earliest + timedelta(days=(want - earliest.weekday()) % 7)
    t = datetime.combine(d, time(hr, 0), tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("UTC"))
    return t.isoformat().replace("+00:00", "Z"), d


def _postpone(s: dict, rel_utc: str, d: date, why: str) -> None:
    """ブリーフの配信日を後ろにずらす（元の日を残す）"""
    p = Path(s["brief"])
    b = read_json(p)
    old = str((b.get("daily") or {}).get("date") or b.get("release_at") or "")
    b.setdefault("release_postponed", {"from": old})
    b["release_postponed"].update({"to": d.isoformat(), "why": why, "at": datetime.now().isoformat(timespec="seconds")})
    b["release_at"] = rel_utc
    if b.get("daily"):
        b["daily"]["date"] = d.isoformat()
    write_json(p, b)


def plan(apply: bool = True, quiet: bool = False, today: date | None = None) -> dict:
    """作曲待ちの曲を月の枠に割り当てる。apply なら配信日をずらす。戻り値：{曲 ID: {cycle, download_from, release, moved}}"""
    st = settings()
    today = today or date.today()
    if not st.get("on"):
        return {}
    cur = cycle_of(today, st)
    budget: dict[date, int] = {}
    out, moved = {}, []
    for s in _pending():
        c = cur
        while True:
            if c not in budget:
                budget[c] = room(c, st, today)
            if budget[c] > 0:
                break
            c = next_cycle(c, st)
        budget[c] -= 1
        dl = max(today, c)
        item = {"cycle": c.isoformat(), "download_from": dl.isoformat(), "slug": s["slug"], "moved": None}
        rel = _release_day(s)
        need = dl + timedelta(days=int(st["after_download_days"]))
        if rel and rel < need and s["kind"] != "debut" and not s.get("sprint"):
            rel_utc, d = _new_release(s, need)
            item["moved"] = {"from": rel.isoformat(), "to": d.isoformat()}
            moved.append((s, rel, d))
            if apply:
                _postpone(s, rel_utc, d, f"Suno のダウンロード枠（{c.month}/{c.day} から使える）に合わせて")
        item["release"] = (item["moved"] or {}).get("to") or (rel.isoformat() if rel else "いちばん早い日")
        out[s["id"]] = item
    if apply:
        write_json(PLAN, {"made_at": datetime.now().isoformat(timespec="seconds"), "songs": out})
    if not quiet:
        print(f"▶ Suno のダウンロード枠：今の月（{cur.month}/{cur.day}〜）の残り {left(today, st)} 曲・"
              f"次のリセット {next_cycle(cur, st).isoformat()}・予備 {st['reserve']} 曲")
        for s, a, b in moved:
            print(f"   ↷ {s['artist']}「{s['title'] or s['id']}」の配信日を {a} → {b} にずらしました" + ("" if apply else "（試し・書き換えていません）"))
        print(f"【結果】作曲待ち {len(out)} 曲を枠に割り当てました（配信日をずらした曲 {len(moved)}）")
    return out


# ---------------------------------------------------------------------------
# 3 制作を絞る
_MEMO: dict[tuple[str, date], tuple[bool, str]] = {}
_PROJ: dict[tuple[date, str | None], int] = {}


def _counts(st: dict, today: date) -> dict[tuple[date, str | None], int]:
    """月ごと（と組ごと）の使った・割り当て済みの曲数"""
    cnt: dict[tuple[date, str | None], int] = {}
    def add(c, slug):
        cnt[(c, None)] = cnt.get((c, None), 0) + 1
        cnt[(c, slug)] = cnt.get((c, slug), 0) + 1
    for x in _log():
        add(cycle_of(datetime.fromisoformat(x["at"]).date(), st), x.get("slug"))
    pl = (read_json(PLAN) if PLAN.exists() else {}).get("songs") or {}
    pend = {s["id"]: s for s in _pending()}
    for sid, s in pend.items():
        c = as_date((pl.get(sid) or {}).get("cycle")) or cycle_of(today, st)
        add(c, s["slug"])
    return cnt


def allow_production(artist: dict, production_monday: date, today: date | None = None) -> tuple[bool, str]:
    """この制作週に、この組の曲を作ってよいか（枠を使っていないときは常に作る）"""
    st = settings()
    if not st.get("on"):
        return True, ""
    slug = artist.get("slug", "")
    key = (slug, production_monday)
    if key in _MEMO:
        return _MEMO[key]
    if (OUT / "briefs" / f"{production_monday.isoformat()}_{slug}.json").exists():
        _MEMO[key] = (True, "")   # もう作った週（作曲待ちとして数えてある）
        return _MEMO[key]
    today = today or date.today()
    if not _PROJ:
        _PROJ.update(_counts(st, today))
    nat = cycle_of(max(today, production_monday + timedelta(days=3)), st)
    cur = cycle_of(today, st)
    # 今の月の制作だけ、次の月の枠を先に使える（いま作ってダウンロードは次の月に。後ろ倒し）。
    # 先の月は自分の月の枠だけ（作曲からダウンロードまでの待ちが、月ごとに積み上がらないように）
    for c in ((nat, next_cycle(nat, st)) if nat == cur else (nat,)):
        total_cap = (len(used(c, st)) + room(c, st, today)) if c == cur else room(c, st, today)
        if _PROJ.get((c, None), 0) < total_cap and _PROJ.get((c, slug), 0) < share(slug, c, st):
            _PROJ[(c, None)] = _PROJ.get((c, None), 0) + 1
            _PROJ[(c, slug)] = _PROJ.get((c, slug), 0) + 1
            _MEMO[key] = (True, "")
            return _MEMO[key]
    _MEMO[key] = (False, f"Suno のダウンロード枠がいっぱい（{nat.month}/{nat.day} からの月に、この組は {share(slug, nat, st)} 曲まで）")
    return _MEMO[key]


# ---------------------------------------------------------------------------
def status(today: date | None = None) -> dict:
    st = settings()
    today = today or date.today()
    cur = cycle_of(today, st)
    pl = (read_json(PLAN) if PLAN.exists() else {}).get("songs") or {}
    by_cycle: dict[str, int] = {}
    for v in pl.values():
        by_cycle[v["cycle"]] = by_cycle.get(v["cycle"], 0) + 1
    return {"on": bool(st.get("on")), "plan": st.get("plan"), "limit": int(st["limit"]), "reserve": int(st["reserve"]),
            "left": left(today, st), "used": len(used(cur, st)), "cycle": cur.isoformat(), "reset": next_cycle(cur, st).isoformat(),
            "manual": (st.get("left_set") or {}).get("cycle") == cur.isoformat(), "waiting": len(pl), "by_cycle": dict(sorted(by_cycle.items())),
            "moved": sum(1 for x in _pending() if x.get("brief") and read_json(Path(x["brief"])).get("release_postponed"))}


def show() -> None:
    s = status()
    if not s["on"]:
        print("【結果】Suno のダウンロード枠には合わせていません（python3 scripts/suno_quota.py on で戻す）")
        return
    print(f"■ Suno の{s['plan']}：月 {s['limit']} 曲（予備 {s['reserve']} 曲）・今の月 {s['cycle']}〜 の残り {s['left']} 曲"
          f"{'（Gon さんが入れた数から）' if s['manual'] else ''}・次のリセット {s['reset']}")
    for c, n in s["by_cycle"].items():
        print(f"   {c} からの月：作曲待ち {n} 曲を割り当て")
    for a in _acts():
        c = date.fromisoformat(s["cycle"])
        print(f"   {a.get('name')}：この月 {share(a['slug'], c)} 曲・次の月 {share(a['slug'], next_cycle(c))} 曲まで")


def stop_daily() -> None:
    """枠を使うあいだは毎日配信を止める（決定 105）"""
    try:
        import daily
        if daily.window():
            daily.cmd_stop()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Suno のダウンロードの枠に、制作と配信日を合わせる")
    ap.add_argument("cmd", nargs="?", default="show", choices=["show", "plan", "left", "set", "on", "off"])
    ap.add_argument("value", nargs="?")
    ap.add_argument("--dry", action="store_true", help="plan：書き換えずに表示だけ")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--reset-day", type=int)
    ap.add_argument("--reserve", type=int)
    ap.add_argument("--plan-name")
    a = ap.parse_args()
    st = settings()
    if a.cmd == "show":
        show()
    elif a.cmd == "plan":
        if st.get("on"):
            stop_daily()
        plan(apply=not a.dry, quiet=a.quiet)
    elif a.cmd == "left":
        if a.value is None or not a.value.isdigit():
            sys.exit("[停止] 残りの曲数を数字で入れてください（例：python3 scripts/suno_quota.py left 0）")
        cur = cycle_of(date.today(), st)
        st["left_set"] = {"left": int(a.value), "at": datetime.now().isoformat(timespec="seconds"), "cycle": cur.isoformat()}
        write_json(SETTINGS, st)
        print(f"【結果】今の月の残りを {a.value} 曲にしました（次のリセット {next_cycle(cur, st)}）")
        plan(quiet=True)
    elif a.cmd == "set":
        for k, v in (("limit", a.limit), ("reset_day", a.reset_day), ("reserve", a.reserve), ("plan", a.plan_name)):
            if v is not None:
                st[k] = v
        write_json(SETTINGS, st)
        print(f"【結果】枠：月 {st['limit']} 曲・更新日 毎月 {st['reset_day']} 日・予備 {st['reserve']} 曲（{st['plan']}）")
        plan(quiet=True)
    elif a.cmd in ("on", "off"):
        st["on"] = a.cmd == "on"
        write_json(SETTINGS, st)
        print("【結果】" + ("Suno のダウンロード枠に合わせます" if st["on"] else "Suno のダウンロード枠に合わせるのをやめました（ずらした配信日は元に戻しません）"))
        if st["on"]:
            stop_daily()
            plan(quiet=True)


if __name__ == "__main__":
    main()

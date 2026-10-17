"""
ダッシュボードのガントチャート用に、工程ごとの開始日・終了日・状態を作る部品（webapp.py・dashboard.py が使う）

2 つの流れ：
  デビュー   … デビュー曲（debut_kit の Suno の曲）がまだ配信されていない組。
               名前の確認 → 写真・ジャケット → Suno で作曲 → 仕上げ（取り込み・音量・ジャケット）→ DistroKid に登録 → 配信待ち → ◆ デビュー
  定期配信   … デビュー曲が配信された組（デビュー曲の作業セットが無い組も）。毎週（隔週）の 1 曲ごとに
               ブリーフ → Suno で作曲 → 仕上げ → DistroKid に登録 → 配信待ち → ◆ 配信。これから作る週は「予定」として並べる
  デビュー曲が配信されたら（Spotify で確かめたら）、その組は自動で定期配信のほうに移る。

工程の状態：done（済み）・run（自動で進行中）・wait（だれかの番）・ng（止まっている・予定より遅れている）・plan（予定）
日付は、終わった工程は実際の日（取り込み・登録・配信の記録）、これからの工程は予定の日。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from _common import LEAD_WEEKS, OUT, all_artists, as_date, load_label, monday_of, production_status, read_json, release_at_utc

import _songs
import _status

ORDER = ["", "imported", "waiting_ffmpeg", "waiting_cover", "ready", "registered", "live"]
DEBUT_DAYS = {"suno": 2, "finish": 1, "register": 1, "wait": 3}   # 予定の日数（デビューは「準備ができたらすぐ」）


def _d(x) -> date | None:
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    try:
        return date.fromisoformat(str(x)[:10]) if x else None
    except ValueError:
        return None


def _ph(name: str, start: date, end: date, state: str, who: str = "", detail: str = "", action: str = "", milestone=False) -> dict:
    if end < start:
        end = start
    return {"name": name, "start": start.isoformat(), "end": end.isoformat(), "state": state, "who": who,
            "detail": detail, "action": action, "milestone": milestone}


def _rank(st: dict) -> int:
    s = st.get("status", "")
    return ORDER.index(s) if s in ORDER else 0


def _current(song: dict, phase_name: str) -> dict:
    """いまの工程の状態・担当・説明（_status の判定をそのまま使う）"""
    s = _status.song_step(song, phase_name)
    return {"ok": "done", "run": "run", "wait": "wait", "ng": "ng"}.get(s["state"], "wait"), s


def song_phases(song: dict, planned: dict[str, tuple[date, date]], release: date | None) -> list[dict]:
    """1 曲の工程。planned は工程ごとの予定（開始, 終了）。実際の記録があればそちらを使う"""
    st = _songs.state(song)
    r, today = _rank(st), date.today()
    imported, ready = _d(st.get("imported_at")), _d(st.get("ready_at"))
    registered, released = _d(st.get("registered_at")), _d(st.get("released"))
    rel = released or _d(st.get("release_date")) or release
    out = []

    def add(key, name, done_at, began, who, detail_done):
        ps, pe = planned[key]
        start = began or ps
        if done_at is not None:                               # 済み
            out.append(_ph(name, start, done_at, "done", "", detail_done))
        elif next_open[0]:                                    # いまの工程
            state, s = _current(song, name)
            late = today > pe
            end = max(today, pe)
            out.append(_ph(name, min(start, today), end, "ng" if (late and state == "wait") else state,
                           s["who"] or who, s["detail"] + (f"（予定は {pe.month}/{pe.day} まで）" if late else ""), s["action"]))
            next_open[0] = False
            shift[0] = max(0, (today - pe).days)
        else:                                                 # これから
            d = timedelta(days=shift[0])
            out.append(_ph(name, ps + d, pe + d, "plan", who, "予定"))

    next_open, shift = [True], [0]
    if song["kind"] == "weekly":
        add("brief", "ブリーフ", _d(planned["brief"][1]) if song.get("style") else None, None, "自動", "Suno の指示文と歌詞ができた")
    add("suno", "Suno で作曲", imported if r >= 1 else None, None, "あなた", "いいねした 1 本を取り込んだ")
    add("finish", "仕上げ（音量・ジャケット）", ready if r >= 4 else None, imported, "自動", "音量・ジャケット・下書き・公開セットができた")
    add("register", "DistroKid に登録", registered if r >= 5 else None, ready, "あなた＋Claude", "DistroKid に登録した")
    add("wait", "配信待ち", released if r >= 6 else None, registered, "自動", "配信が始まった")
    mdate = rel or _d(out[-1]["end"])
    out.append(_ph("配信" if song["kind"] == "weekly" else "デビュー", mdate, mdate, "done" if r >= 6 else "plan", "",
                   f"配信日 {mdate}" + (f"・ISRC {st.get('isrc')}" if st.get("isrc") else ""), st.get("url", ""), milestone=True))
    return out


# ---------------------------------------------------------------------------
def in_regular(slug: str) -> bool:
    """デビューを終えて定期配信の流れにいるか（デビュー曲が配信中、またはデビュー曲の作業セットが無い組）"""
    ds = [s for s in _songs.debut_songs() if s["slug"] == slug]
    return not ds or any(_songs.state(s).get("status") == "live" for s in ds)


def debut_rows() -> list[dict]:
    today = date.today()
    rows = []
    for a in all_artists():
        slug = a.get("slug")
        if not slug or in_regular(slug):
            continue
        song = next(s for s in _songs.debut_songs() if s["slug"] == slug)
        kit_day = _d(song["week"]) or today
        steps = {s["name"]: s for s in _status.artist_steps(a)}
        phases = []
        # 名前の確認・写真とジャケット（曲の工程と並行）
        n = steps["名前の確認"]
        nd = _d((a.get("name_check") or {}).get("checked_at")) or kit_day
        phases.append(_ph("名前の確認", nd, nd, "done", detail=n["detail"]) if n["state"] == "ok"
                      else _ph("名前の確認", kit_day, max(today, kit_day), "ng", n["who"], n["detail"], n["action"]))
        p = steps["写真・ジャケット"]
        phases.append(_ph("写真・ジャケット", kit_day, kit_day + timedelta(days=1), "done", detail=p["detail"]) if p["state"] == "ok"
                      else _ph("写真・ジャケット", kit_day, max(today, kit_day + timedelta(days=1)), "ng" if p["state"] == "ng" else "wait",
                               p["who"], p["detail"], p["action"]))
        # 曲の工程：作業セットができた日から、準備ができたらすぐ
        start = kit_day
        plan = {}
        for key in ("suno", "finish", "register", "wait"):
            end = start + timedelta(days=DEBUT_DAYS[key] - 1)
            plan[key] = (start, end)
            start = end + timedelta(days=1)
        phases += song_phases(song, plan, None)
        b = _status.blocker(list(steps.values()))
        rows.append({"slug": slug, "artist": a.get("name", slug), "label": _label(a), "title": song["title"], "id": song["id"],
                     "row": f"{a.get('name', slug)}", "sub": f"デビュー曲「{song['title']}」", "phases": phases,
                     "blocker": _blk(b)})
    return rows


def regular_rows(weeks_ahead: int = 4) -> list[dict]:
    today = date.today()
    this_monday = monday_of(today)
    rows = []
    for a in all_artists():
        slug = a.get("slug")
        if not slug or not in_regular(slug):
            continue
        songs = sorted([s for s in _songs.weekly_songs() if s["slug"] == slug], key=lambda s: s["week"])
        weeks_with_song = {s["week"] for s in songs}
        for s in songs:
            st = _songs.state(s)
            rel = _d(s.get("release_at"))
            if st.get("status") == "live" and (today - (_d(st.get("released")) or rel or today)).days > 21:
                continue   # 3 週間より前に配信した曲は表に出さない
            rows.append(_weekly_row(a, s, rel))
        # これから作る週（予定）
        for k in range(0, weeks_ahead + 1):
            w = this_monday + timedelta(weeks=k)
            if w.isoformat() in weeks_with_song:
                continue
            ok, _ = production_status(a, w)
            if not ok:
                continue
            lab = load_label(a.get("label_slug"))
            rel = release_at_utc(w, lab.get("release_weekday", "wednesday"), lab.get("release_hour_et", 17)).date()
            fake = {"kind": "weekly", "key": f"{w}_{slug}", "id": f"{w}_{slug}", "slug": slug, "week": w.isoformat(),
                    "title": "", "style": "", "artist": a.get("name", slug), "release_at": rel.isoformat()}
            row = _weekly_row(a, fake, rel, planned_only=True)
            rows.append(row)
    rows.sort(key=lambda r: (r["artist"], r["week"]))
    return rows


def _weekly_row(a: dict, s: dict, rel: date | None, planned_only: bool = False) -> dict:
    w = _d(s["week"])
    rel = rel or (w + timedelta(days=7 * LEAD_WEEKS + 2))
    plan = {"brief": (w + timedelta(days=1), w + timedelta(days=1)), "suno": (w + timedelta(days=1), w + timedelta(days=3)),
            "finish": (w + timedelta(days=3), w + timedelta(days=4)), "register": (w + timedelta(days=4), rel - timedelta(days=7)),
            "wait": (rel - timedelta(days=6), rel - timedelta(days=1))}
    if planned_only:
        phases = [_ph(n, plan[k][0], plan[k][1], "plan", who, "予定") for k, n, who in
                  (("brief", "ブリーフ", "自動"), ("suno", "Suno で作曲", "あなた"), ("finish", "仕上げ（音量・ジャケット）", "自動"),
                   ("register", "DistroKid に登録", "あなた＋Claude"), ("wait", "配信待ち", "自動"))]
        phases.append(_ph("配信", rel, rel, "plan", detail=f"配信予定日 {rel}", milestone=True))
        blk = None
    else:
        phases = song_phases(s, plan, rel)
        cur = next((p for p in phases if p["state"] in ("ng", "wait", "run")), None)
        blk = {"state": cur["state"], "who": cur["who"], "detail": cur["detail"], "name": cur["name"]} if cur else None
    title = s.get("title") or "（曲名未定）"
    return {"slug": a["slug"], "artist": a.get("name", a["slug"]), "label": _label(a), "week": s["week"], "id": s["id"],
            "title": s.get("title", ""), "row": a.get("name", a["slug"]),
            "sub": (f"{w.month}/{w.day} の週" + ("（予定）" if planned_only else f"「{title}」")), "phases": phases,
            "planned": planned_only, "blocker": blk}


def _label(a: dict) -> str:
    lab = a.get("label_slug")
    return load_label(lab if lab not in (None, "", "drive") else None).get("name", "Postdog Records")


def _blk(b: dict | None) -> dict | None:
    return {"state": b["state"], "who": b["who"], "detail": b["detail"], "name": b["name"]} if b else None


def timeline() -> dict:
    deb, reg = debut_rows(), regular_rows()
    today = date.today()
    starts = [_d(p["start"]) for r in deb + reg for p in r["phases"]] or [today]
    ends = [_d(p["end"]) for r in deb + reg for p in r["phases"]] or [today]

    def rng(rows, back, fwd):
        if not rows:
            return {"from": (today - timedelta(days=back)).isoformat(), "to": (today + timedelta(days=fwd)).isoformat()}
        s = min(_d(p["start"]) for r in rows for p in r["phases"])
        e = max(_d(p["end"]) for r in rows for p in r["phases"])
        s = max(min(s, today - timedelta(days=3)), today - timedelta(days=back))
        e = min(max(e, today + timedelta(days=14)), today + timedelta(days=fwd))
        return {"from": monday_of(s).isoformat(), "to": e.isoformat()}
    return {"today": today.isoformat(), "debut": {"rows": deb, "range": rng(deb, 21, 28)},
            "regular": {"rows": reg, "range": rng(reg, 14, 42)}, "span": [min(starts).isoformat(), max(ends).isoformat()]}

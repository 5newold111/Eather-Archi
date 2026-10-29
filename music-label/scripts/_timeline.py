"""
ダッシュボードのガントチャート用に、工程ごとの開始日・終了日・状態を作る部品（webapp.py・dashboard.py が使う）

3 つの流れ：
  デビュー   … デビュー曲（debut_kit の Suno の曲）がまだ配信されていない組。
               名前の確認 → 写真・ジャケット → Suno で作曲 → 仕上げ（取り込み・音量・ジャケット）→ DistroKid に登録 → 配信待ち → ◆ デビュー
  定期配信   … デビュー曲が配信された組（デビュー曲の作業セットが無い組も）。毎週（隔週）の 1 曲ごとに
               ブリーフ → Suno で作曲 → 仕上げ → DistroKid に登録 → 配信待ち → ◆ 配信。これから作る週は「予定」として並べる
  不定期配信 … デビュー曲が配信中の組の、毎週とは別の 1 曲。作曲へ進めた日から
               曲の案 → Suno で作曲 → 仕上げ → DistroKid に登録 → 配信待ち → ◆ 配信（irregular.py。決定 72）
  デビュー曲が配信されたら（Spotify で確かめたら）、その組の「これから作る週（予定）」は定期配信のほうに並ぶ。
  工程が進んだ曲は表から消さない（オーナーの指示 2026-10-24。決定 54）：
    ・デビューの表は、デビュー曲が配信されたあとも ◆ デビューまで済んだ行として残す
    ・定期配信の表は、毎週の曲（集中配信の曲も）を、デビュー曲の配信を待たずに、配信が終わった曲も含めて全部並べる

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
    add("register", "DistroKid に登録", registered if r >= 5 else None, ready, "あなた", "DistroKid に登録した")
    add("wait", "配信待ち", released if r >= 6 else None, registered, "自動", "配信が始まった")
    mdate = rel or _d(out[-1]["end"])
    out.append(_ph("配信" if song["kind"] == "weekly" else "デビュー", mdate, mdate, "done" if r >= 6 else "plan", "",
                   f"配信日 {mdate}" + (f"・ISRC {st.get('isrc')}" if st.get("isrc") else ""), st.get("url", ""), milestone=True))
    return out


# ---------------------------------------------------------------------------
def debut_done(slug: str) -> bool:
    """デビューが終わったか：デビューのタブの配信確認で、アーティストのページ（Spotify・YouTube Music・Apple Music。
    Instagram・Facebook は任意）を記録したとき（決定 139・141・145）。そろうまではデビューの流れのまま（デビュー曲が配信中でも）。
    終わった組はデビューのタブに出さない（決定 120）"""
    try:
        import publish
        return not publish.artist_pages_missing(slug)
    except Exception:  # noqa: BLE001
        return False


def in_regular(slug: str) -> bool:
    """デビューを終えて定期配信の流れにいるか（デビューが終わった組、またはデビュー曲の作業セットが無い組）"""
    return not any(s["slug"] == slug for s in _songs.debut_songs()) or debut_done(slug)


def debut_rows() -> list[dict]:
    today = date.today()
    rows = []
    for a in all_artists():
        slug = a.get("slug")
        song = next((s for s in _songs.debut_songs() if s["slug"] == slug), None) if slug else None
        if song is None:
            continue   # デビュー曲の作業セットが無い組（最初から定期配信）
        if debut_done(slug):
            continue   # デビューが終わった組は出さない（定期配信のタブへ。決定 120）
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
        # 最後：配信確認。アーティストのページを記録する（必要な 3 つがそろうとデビュー完了。決定 139・141・145）
        sp_day = max([_d(ph["end"]) for ph in phases] + [today])
        live = _songs.state(song).get("status") in ("registered", "live")
        try:
            import publish
            left = publish.artist_pages_missing(slug)
            published = bool(publish.spotify_artist_id(slug))   # もう配信されている（Spotify にページがある）組
        except Exception:  # noqa: BLE001
            left, published = [], False
        last = _ph("配信確認（アーティストのページ）", sp_day, sp_day, "wait" if (live or published) else "plan", "あなた",
                   f"デビュー曲が配信されたら、アーティストのページを記録する（あと {len(left)} つ：{'・'.join(left)}。そろうとデビュー完了。Instagram・Facebook は任意）")
        b = _status.blocker(list(steps.values()))
        if published:   # もう配信されている組：前の工程は全部済み、「配信待ち」を今日まで続く進行中の帯にし、
            # その先の配信確認を Gon さんの番にする（デビューのタブとガントチャートに配信待ちで並ぶ。決定 142・143）
            for ph in phases:
                ph["state"], ph["who"] = "done", ""
                if _d(ph["start"]) > today:   # 済んだ工程は今日より後ろに出さない（予定の日付のままだと未来に描かれる）
                    ph["start"] = today.isoformat()
                if _d(ph["end"]) > today:
                    ph["end"] = today.isoformat()
                if ph["name"] == "配信待ち":   # 今日まで続く帯
                    ph["state"], ph["end"] = "run", today.isoformat()
                    ph["start"] = min(_d(ph["start"]), today - timedelta(days=3)).isoformat()
                    ph["detail"] = "配信されています。アーティストのページ（Spotify・YouTube Music・Apple Music）がそろうまで、配信待ち（配信確認中）のままにします"
            last["start"] = last["end"] = today.isoformat()
            b = {"state": "wait", "who": "あなた", "detail": last["detail"], "name": last["name"]}
        phases.append(last)
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
        if not slug:
            continue
        songs = sorted([s for s in _songs.weekly_songs() if s["slug"] == slug], key=lambda s: s["week"])
        weeks_with_song = {s["week"] for s in songs}
        songs = [s for s in songs if not s.get("irregular") and not s.get("daily")]   # 不定期・毎日配信の曲は別の表に並べる（決定 72・94）
        for s in songs:   # 作った曲は、デビュー曲の配信を待たずに、配信が終わったあとも並べる（消さない）
            rows.append(_weekly_row(a, s, _d(s.get("release_at"))))
        if not in_regular(slug):
            continue   # これから作る週（予定）は、デビューを終えた組だけ
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
                   ("register", "DistroKid に登録", "あなた"), ("wait", "配信待ち", "自動"))]
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


def irregular_rows() -> list[dict]:
    """不定期配信の曲（作曲へ進めた案）。予定は作曲へ進めた日から：作曲 2 日 → 仕上げ 1 日 → 登録 1 日 → 配信待ち（決定 72）"""
    rows = []
    arts = {a.get("slug"): a for a in all_artists()}
    for s in _songs.weekly_songs():
        ir = s.get("irregular") or {}
        dl = s.get("daily") or {}   # 毎日配信の曲（決定 94）も同じ表に、決まった配信日で並べる
        a = arts.get(s["slug"])
        if not (ir or dl) or a is None:
            continue
        st = _songs.state(s)
        start = _d(ir.get("accepted_at")) or _d(dl.get("marked_at")) or date.today()
        rel = _d(st.get("released")) or _d(st.get("release_date")) or _d(dl.get("date")) or start + timedelta(days=7)
        if dl and not ir:   # 決まった配信日から逆算：登録は配信の 7 日前まで（Spotify への推薦のため）
            start = min(start, rel - timedelta(days=10))
        plan = {"brief": (start, start), "suno": (start, start + timedelta(days=1)), "finish": (start + timedelta(days=2), start + timedelta(days=2)),
                "register": (start + timedelta(days=3), start + timedelta(days=3)), "wait": (start + timedelta(days=4), max(rel - timedelta(days=1), start + timedelta(days=4)))}
        phases = song_phases(s, plan, rel)
        cur = next((p for p in phases if p["state"] in ("ng", "wait", "run")), None)
        title = s.get("title") or "（曲名未定）"
        rows.append({"slug": s["slug"], "artist": a.get("name", s["slug"]), "label": _label(a), "week": s["week"], "id": s["id"],
                     "title": s.get("title", ""), "row": a.get("name", s["slug"]),
                     "sub": (f"{rel.month}/{rel.day} 毎日配信「{title}」" if dl and not ir else f"{start.month}/{start.day} 不定期「{title}」"),
                     "phases": phases, "planned": False, "started": start.isoformat(),
                     "blocker": {"state": cur["state"], "who": cur["who"], "detail": cur["detail"], "name": cur["name"]} if cur else None})
    rows.sort(key=lambda r: (r["artist"], r["started"]))
    return rows


def _label(a: dict) -> str:
    lab = a.get("label_slug")
    return load_label(lab if lab not in (None, "", "drive") else None).get("name", "Postdog Records")


def _blk(b: dict | None) -> dict | None:
    return {"state": b["state"], "who": b["who"], "detail": b["detail"], "name": b["name"]} if b else None


def timeline() -> dict:
    deb, reg, irr = debut_rows(), regular_rows(), irregular_rows()
    today = date.today()
    starts = [_d(p["start"]) for r in deb + reg + irr for p in r["phases"]] or [today]
    ends = [_d(p["end"]) for r in deb + reg + irr for p in r["phases"]] or [today]

    def rng(rows, back, fwd):
        if not rows:
            return {"from": (today - timedelta(days=back)).isoformat(), "to": (today + timedelta(days=fwd)).isoformat()}
        s = min(_d(p["start"]) for r in rows for p in r["phases"])
        e = max(_d(p["end"]) for r in rows for p in r["phases"])
        s = max(min(s, today - timedelta(days=3)), today - timedelta(days=back))
        e = min(max(e, today + timedelta(days=14)), today + timedelta(days=fwd))
        return {"from": monday_of(s).isoformat(), "to": e.isoformat()}
    return {"today": today.isoformat(), "debut": {"rows": deb, "range": rng(deb, 21, 28)},
            "regular": {"rows": reg, "range": rng(reg, 14, 42)}, "irregular": {"rows": irr, "range": rng(irr, 14, 42)}, "span": [min(starts).isoformat(), max(ends).isoformat()]}

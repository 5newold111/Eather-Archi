#!/usr/bin/env python3
"""
ダッシュボードのウェブアプリ（Mac の中で動く小さなサーバー。外のサービスにデータを送らない）

ブラウザで http://localhost:8765 を開くと：
  ・アーティストごとの進み具合と詰まり（担当・何日止まっているか・次の一歩）。30 秒ごとに自動で新しくなる
  ・仕組みの詰まり（鍵・ffmpeg・SSD・定期実行）と、管理表の「詰まっているところ」
  ・曲の一覧（公開の段階・ISRC・公開セットのフォルダ）
  ・ボタン：Suno に入力する／DistroKid に入力する（専用のブラウザが開き、欄を自動で埋める。Create と最後の登録ボタンは押さない。browser_fill.py）／
    曲を取り込む／配信を確かめる／SSD に保管する／写真とジャケットを作る／DistroKid に登録したことを記録する／
    公開セットのフォルダを Finder で開く／鍵や部品の設定をターミナルで始める
動きの中身は今までのコマンド（publish.py・archive_sync.py・kit_images.py・browser_fill.py）と同じ。ボタンはそれを代わりに実行するだけ。

使い方：
  python3 scripts/webapp.py                 # この Mac のブラウザだけから見る（http://localhost:8765）
  python3 scripts/webapp.py --lan           # 家の Wi-Fi のスマホからも見る（合鍵つきの URL を表示。合鍵は .env の WEBAPP_KEY）
  python3 scripts/webapp.py --port 8800     # 番号を変える
  python3 scripts/webapp.py --awake         # 動いている間、Mac が勝手に眠らないようにする（外出先から使うとき）
  外出先のスマホから：Tailscale（Mac とスマホだけの専用の通り道）を入れ、Mac で一度だけ  tailscale serve --bg 8765
                      → スマホで https://<この Mac の名前>.ts.net/phone （docs/18_phone.md・決定 28）
  python3 scripts/schedule.py install       # Mac の起動中はいつも動かしておく（仕事の名前 webapp）

安全のため：
  ・ふだんはこの Mac の中からしか開けない（--lan のときだけ家の Wi-Fi から。合鍵が無いと開けない）
  ・ボタンは決まった仕事しか実行できない。ページに埋め込んだ毎回変わる合言葉が無いと受け付けない（ほかのサイトから押させない）
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_dotenv  # noqa: E402

load_dotenv()
import _songs  # noqa: E402
import _status  # noqa: E402

S = Path(__file__).resolve().parent
PY = sys.executable
TOKEN = secrets.token_urlsafe(24)          # ボタン用の合言葉（起動のたびに変わる）
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
STATUS_JA = {"": "Suno で作曲", "imported": "取り込み済み", "waiting_ffmpeg": "音量調整待ち", "waiting_cover": "ジャケット待ち",
             "ready": "登録の準備ができた", "registered": "DistroKid 登録済み", "live": "配信中"}


# ---------------------------------------------------------------------------
# データ
def _iso(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else x


def status_json() -> dict:
    load_dotenv(override=True)   # 「仕組みの詰まり」も、いまの .env で判定する
    arts = []
    for r in _status.artists_overview():
        steps = [{**s, "since": _iso(s["since"]),
                  "days": (date.today() - s["since"]).days if s["since"] else None} for s in r["steps"]]
        b = r["blocker"]
        arts.append({**{k: r[k] for k in ("slug", "name", "label", "days")}, "steps": steps,
                     "blocker": {**b, "since": _iso(b["since"])} if b else None})
    songs = []
    import _archive as archive
    for s in _songs.all_songs():
        if str(s["week"]).startswith("2000-"):
            continue
        st = _songs.state(s)
        folder = archive.local_folder(s["artist"], "music", _songs.folder_name(s)) if (OUT / "masters" / s["key"] / "master.wav").exists() else None
        songs.append({"id": s["id"], "artist": s["artist"], "slug": s["slug"], "title": s["title"], "kind": s["kind"],
                      "status": st.get("status", ""), "status_ja": STATUS_JA.get(st.get("status", ""), st.get("status", "")),
                      "release_date": st.get("released") or st.get("release_date") or (str(s.get("release_at") or "")[:10]),
                      "isrc": st.get("isrc", ""), "url": st.get("url", ""), "folder": str(folder) if folder else "",
                      "draft": (OUT / "publish" / s["key"] / "DistroKid下書き.md").exists(), "flow": _flow(s)})
    rows, src = _status.tracker_rows()
    import _timeline
    try:
        from dashboard import todos
        weekly = [{"sev": a, "what": b, "who": c, "where": d} for a, b, c, d in todos()]
    except Exception:  # noqa: BLE001  毎週の制作のやることが読めなくても画面は出す
        weekly = []
    try:   # アーティスト写真を各サイトに入れる（配信が始まった組から。決定 38）
        import artist_photo
        photo_todos = [{**t, "flow": "regular" if _timeline.in_regular(t["slug"]) else "debut"} for t in artist_photo.todos()]
    except Exception:  # noqa: BLE001
        photo_todos = []
    try:   # 登録した曲の歌詞を DistroKid に出す（決定 52）
        import browser_fill
        lyric_todos = [{"id": x["id"], "artist": x["artist"], "title": x["title"], "flow": _flow(x)} for x in browser_fill.lyrics_todo()]
    except Exception:  # noqa: BLE001
        lyric_todos = []
    tl = _timeline.timeline()
    import _guide
    for key in ("debut", "regular", "irregular"):
        for r in tl[key]["rows"]:
            if r.get("id") and not r.get("planned"):   # 間違えて進めたときに戻すボタン（ポップアップの上に出す。決定 38・41）
                try:
                    song = _songs.by_id(r["id"])
                    r["back"] = _guide.back_actions(song)
                    # もう一度入力する（前の入力が残っていても止めて入り直す。決定 47）
                    stt = _songs.state(song).get("status", "") if song else "live"
                    r["redo"] = ([{"kind": "run", "label": "Suno にもう一度入力する（ブラウザが開きます）", "action": "fill_suno", "id": r["id"]}]
                                 if stt == "" else []) + \
                        ([{"kind": "run", "label": "DistroKid にもう一度入力する（ブラウザが開きます）", "action": "fill_distrokid", "id": r["id"]}]
                         if stt in ("ready", "registered") else [])
                except Exception:  # noqa: BLE001
                    r["back"], r["redo"] = [], []
            for ph in r["phases"]:
                try:
                    ph["guide"] = _guide.explain(r, ph)
                except Exception as e:  # noqa: BLE001  説明が作れなくても画面は出す
                    ph["guide"] = {"cause": ph.get("detail", ""), "steps": [], "actions": [], "error": str(e)}
    try:   # 不定期配信：選べる組・曲の案（作曲前）・場面の選択肢（決定 72）
        import irregular
        from _common import SCENE_TYPES
        irr = {"artists": irregular.eligible(), "scenes": [{"key": k, "name": v["name"]} for k, v in SCENE_TYPES.items()],
               "drafts": [irregular.summary(d) for d in irregular.drafts()
                          if (d.get("irregular") or {}).get("status") in ("making", "proposed", "failed")],
               "editable": irregular.EDITABLE, "limits": irregular.LIMITS}
        import collab_fit   # コラボの相手（相性の高い順。デビュー曲が配信中の組だけ選べる。決定 97）
        live = {x["slug"] for x in irr["artists"]}
        irr["collab"] = {x["slug"]: [{"slug": f["slug"], "name": f["name"], "score": f.get("score"), "why": f.get("why", ""),
                                      "brings": f.get("partner_brings", ""), "ok": f["slug"] in live}
                                     for f in collab_fit.partners_of(x["slug"])] for x in irr["artists"]}
    except Exception as e:  # noqa: BLE001
        irr = {"artists": [], "scenes": [], "drafts": [], "error": str(e)}
    try:   # 既存楽曲で、まだ Claude に調べてもらっていない曲の数と料金の目安（決定 85）
        import reference_db
        n_en = len(reference_db.enrich_pending())
        ref_enrich = {"pending": n_en, "yen": [n_en * reference_db.YEN_PER_SONG[0], n_en * reference_db.YEN_PER_SONG[1]],
                      "lyrics": len(reference_db.lyrics_pending()), "whisper": reference_db.whisper_ready(),
                      "melody": len(reference_db.melody_pending()), "demucs": __import__("melody").demucs_ready(),
                      "infl": reference_db.influence_pending(),   # 影響を受けたアーティストの曲で、まだ済んでいない数（決定 91）
                      "infl_picked": sum(len(v.get("picks", [])) for v in __import__("influences").load().values() if (v or {}).get("applied"))}
    except Exception:  # noqa: BLE001
        ref_enrich = {"pending": 0, "yen": [0, 0], "lyrics": 0, "whisper": True}
    try:   # 毎日配信（5 組の持ち回り。決定 94）と SNS の毎日の投稿
        import daily
        import post_social
        dly = daily.summary()
        ss = post_social.daily_settings()
        dly["social"] = {"on": bool(ss.get("on")), "per_day": ss.get("per_day"), "bg_dir": str(post_social.bg_root()),
                         "planned": len(list(post_social.DAILY_DIR.rglob("plan.json"))) if post_social.DAILY_DIR.exists() else 0}
    except Exception as e:  # noqa: BLE001
        dly = {"active": False, "error": str(e)}
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "artists": arts, "timeline": tl, "irregular": irr, "ref_enrich": ref_enrich,
            "daily": dly,
            "weekly_todos": weekly, "photo_todos": photo_todos, "lyric_todos": lyric_todos,
            "system": [{"sev": a, "what": b, "how": c, "acts": _fix_buttons(c)} for a, b, c in _status.system_blockers()],
            "tracker": {"rows": rows, "src": src}, "songs": songs,
            "jobs": [{k: j.get(k, "") for k in ("id", "label", "state", "started", "progress", "step")} for j in list(JOBS.values())[-8:]]}


def _flow(s: dict) -> str:
    """どのタブに並べるか：デビュー曲はデビュー、不定期の曲は不定期配信、毎週の曲は定期配信（決定 54・72）"""
    return "debut" if s.get("kind") == "debut" else ("irregular" if s.get("irregular") or s.get("daily") else "regular")


# ---------------------------------------------------------------------------
# ボタンで動かす仕事（決まったものだけ）
def _slugs() -> set[str]:
    return {a["slug"] for a in all_artists() if a.get("slug")}


def command_for(action: str, p: dict) -> tuple[list[str], str] | None:
    if action == "scan":
        return [PY, str(S / "publish.py"), "scan"], "曲の取り込み"
    if action == "track":
        return [PY, str(S / "publish.py"), "track"], "配信の確認"
    if action == "ref_audio":   # SSD の「既存楽曲」の音源を全部解析して、参考曲に登録する（決定 76）
        return [PY, str(S / "reference_db.py"), "--audio-limit", "0"], "参考曲の解析（既存楽曲の WAV・MP3・M4A）"
    if action == "ack_job" and p.get("job") in ("intake", "release", "backup", "auto", "brief"):   # 直したので表示を消す（決定 89）
        return [PY, str(S / "schedule.py"), "ack", p["job"]], f"定期実行のエラーの表示を消す（{p['job']}）"
    if action == "rerun_job" and p.get("job") in ("intake", "release", "backup", "auto", "brief"):   # 定期実行をいま動かし直す（決定 81）
        return [PY, str(S / "schedule.py"), "run", p["job"]], f"定期実行をもう一度（{p['job']}）"
    if action == "ref_melody":   # 既存楽曲のメロディーを起こして MIDI に残す（無料。決定 90）
        return [PY, str(S / "reference_db.py"), "--audio-melody", "15"], "既存楽曲のメロディーを起こす"
    if action == "ref_lyrics":   # 既存楽曲の歌詞を Mac の中で聞き取る（無料。決定 86）
        return [PY, str(S / "reference_db.py"), "--audio-lyrics", "30"], "既存楽曲の歌詞を聞き取る"
    if action == "ref_enrich":   # 音源から測れない項目を Claude に Web で調べてもらう（決定 85。料金がかかるので押す前に確認）
        return [PY, str(S / "reference_db.py"), "--audio-enrich", "50"], "参考曲の足りない項目を Claude に調べてもらう"
    if action == "influences":   # SSDDB の中から、組が影響を受けたアーティストを選んで「影響」に足し、その曲を先に解析する（決定 91）
        return [PY, str(S / "influences.py"), "--apply"], "影響を受けたアーティストを選ぶ（SSDDB から）"
    if action == "influences_analyze":   # 影響を受けたアーティストの曲の歌詞・メロディーを先に起こす（無料）
        return [PY, str(S / "influences.py"), "--analyze"], "影響を受けたアーティストの曲を解析する"
    if action == "collab_fit":   # まだ吟味していない組み合わせのコラボの相性（決定 97）
        return [PY, str(S / "collab_fit.py")], "コラボの相性を吟味する"
    if action == "explore_report":   # 曲ごとの方向性の伸びと、くじの確率（決定 96）
        return [PY, str(S / "explore.py"), "report"], "方向性の探り具合"
    if action == "ref_ensure":   # SSD の解析シートを探して読み込み、無い曲は全部解析して SSD に保存（決定 102）
        return [PY, str(S / "reference_db.py"), "--ensure-sheets"], "解析シートを SSD に作る（既存楽曲を全部）"
    if action == "ref_health":   # 参考曲の取り込みの健康診断（読むだけ・無料。決定 101）
        return [PY, str(S / "reference_db.py"), "--health"], "参考曲の取り込みの診断"
    if action == "ref_audio_report":   # 解析できていない曲を調べる（決定 83）
        return [PY, str(S / "reference_db.py"), "--audio-report"], "解析できていない曲を調べる（既存楽曲）"
    if action == "sync":
        return [PY, str(S / "archive_sync.py")], "保管（ローカルと SSD）"
    if action == "dashboard":
        return [PY, str(S / "dashboard.py")], "ダッシュボードの書き出し"
    if action == "images" and p.get("slug") in _slugs():
        return [PY, str(S / "kit_images.py"), "--only", p["slug"]], f"写真とジャケットを作る（{p['slug']}）"
    if action == "mark_live":   # 配信が始まったことを手で記録する（決定 55）
        song = _songs.by_id(str(p.get("id", "")))
        ok = lambda v: not v or re.fullmatch(r"https://open\.spotify\.com/[\w/?=&.-]{10,200}", v)  # noqa: E731
        tr, ar = str(p.get("track") or "").strip(), str(p.get("artist") or "").strip()
        if song and ok(tr) and ok(ar):
            return [PY, str(S / "publish.py"), "live", song["id"], *(["--track", tr] if tr else []), *(["--artist", ar] if ar else [])], \
                f"配信中の記録（{song['artist']}「{song['title']}」）"
    if action == "artist_page" and p.get("slug") in _slugs():   # Spotify のアーティストのページを手で記録する（決定 53）
        page = str(p.get("page") or "").strip()
        if re.fullmatch(r"[\w:/.?=&-]{22,200}", page):
            return [PY, str(S / "publish.py"), "artist-page", p["slug"], page], f"Spotify のページの記録（{p['slug']}）"
    if action == "registered":
        song = _songs.by_id(str(p.get("id", "")))
        day = str(p.get("date") or date.today().isoformat())
        if song and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return [PY, str(S / "publish.py"), "registered", song["id"], "--date", day], f"登録の記録（{song['artist']}「{song['title']}」）"
    if action == "open_folder":
        song = _songs.by_id(str(p.get("id", "")))
        if song:
            import _archive as archive
            folder = archive.local_folder(song["artist"], "music", _songs.folder_name(song))
            if not any(folder.iterdir()):   # ローカルから片付けたあと：SSD の公開セット
                sf, _ = archive.artist_folder(song["artist"], "music", _songs.folder_name(song))
                folder = sf or folder
            return ["open", str(folder)], f"フォルダを開く（{song['title']}）"
    slug = p.get("slug") if p.get("slug") in _slugs() else None
    if action == "cover" and p.get("id"):   # その曲のジャケット：作る・候補から選ぶ・作り直す（決定 73）
        song = _songs.by_id(str(p.get("id")))
        no = str(p.get("no") or "")
        if song and (not no or no.isdigit()):
            return [PY, str(S / "publish.py"), "cover", song["id"], *(["--choose", no] if no else []), *(["--redo"] if p.get("redo") else [])], \
                f"ジャケット（{song['artist']}「{song['title']}」" + (f"・#{no} を選ぶ）" if no else "・作り直す）" if p.get("redo") else "）")
    if action == "cover" and slug:
        return [PY, str(S / "kit_images.py"), "--only", slug, "--kind", "cover"], f"ジャケットを作る（{slug}）"
    if action == "check_name" and slug:
        return [PY, str(S / "check_names.py"), "--artist", slug, "--apply"], f"名前の確認（{slug}）"
    if action == "refresh_photo" and slug:
        return [PY, str(S / "refresh_photos.py"), "--only", slug, "--force"], f"写真の刷新（{slug}）"
    if action == "open_artist" and slug:
        import _archive as archive
        name = next((a.get("name") for a in all_artists() if a.get("slug") == slug), slug)
        return ["open", str(archive.local_folder(name))], f"フォルダを開く（{name}）"
    if action == "brief":
        week = str(p.get("week") or "this")
        if week == "this" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", week):
            return [PY, str(S / "run_week.py"), "brief", "--week", week], f"ブリーフを作る（{week}）"
    if action == "daily_start":   # 毎日配信を始める（既定：2 週間後の月曜から 30 日・配信中の組の持ち回り。決定 94）
        return [PY, str(S / "daily.py"), "start"], "毎日配信を始める（5 組の持ち回り）"
    if action == "daily_make":   # 毎日配信の 21 日先までの曲を作る
        return [PY, str(S / "daily.py"), "make"], "毎日配信の曲を作る（21 日先まで）"
    if action == "social_daily_on":   # SNS の毎日の投稿を始めて、3 日先までの案を作る（決定 94）
        n = str(p.get("per_day") or 2) if str(p.get("per_day") or 2) in ("1", "2", "3") else "2"
        return [PY, str(S / "post_social.py"), "daily", "--on", "--per-day", n], f"SNS の毎日の投稿を始める（1 日 {n} 本）"
    if action == "social_daily_off":
        return [PY, str(S / "post_social.py"), "daily", "--off"], "SNS の毎日の投稿を止める"
    if action == "social_daily_plan":
        return [PY, str(S / "post_social.py"), "daily", "--days", "3"], "SNS の投稿の案を作る（3 日先まで）"
    if action == "daily_stop":
        return [PY, str(S / "daily.py"), "stop"], "毎日配信を止める"
    if action == "irregular_propose":   # 不定期配信の曲の案を作る・却下して作り直す（決定 72）
        import irregular
        from _common import SCENE_TYPES
        slug = str(p.get("slug") or "")
        name = next((a["name"] for a in irregular.eligible() if a["slug"] == slug), None)
        scene = str(p.get("scene") or "")
        redo = str(p.get("redo") or "")
        collab = str(p.get("collab") or "")
        live = {a["slug"] for a in irregular.eligible()}
        if collab and (collab not in live or collab == slug):
            collab = ""
        if name and (not scene or scene in SCENE_TYPES) and (not redo or irregular.load_draft(redo)):
            memo, reason = str(p.get("memo") or "")[:1000], str(p.get("reason") or "")[:1000]
            return [PY, str(S / "irregular.py"), "propose", slug, *(["--scene", scene] if scene else []),
                    *(["--memo", memo] if memo else []), *(["--redo", redo] if redo else []), *(["--reason", reason] if reason else []),
                    *(["--collab", collab] if collab else [])], \
                f"曲の案を{'作り直す' if redo else '作る'}（{name}{'・コラボ' if collab else ''}）"
    if action == "fill_suno":
        song = _songs.by_id(str(p.get("id", ""))) if p.get("id") else None
        if p.get("id") and not song:
            return None
        return [PY, str(S / "browser_fill.py"), "suno", *([song["id"]] if song else [])], \
            "ブラウザで入力（Suno" + (f"：{song['artist']}「{song['title']}」）" if song else "：作曲待ちの全曲）")
    if action == "suno_retake":   # Suno で聴いて 2 曲とも良くない：理由のとおりに入力を直す（決定 100）
        import take_check
        song = _songs.by_id(str(p.get("id", "")))
        why = [w for w in str(p.get("why") or "").split(",") if w in take_check.MANUAL]
        if song and why:
            return [PY, str(S / "take_check.py"), song["id"], "--redo", ",".join(why)], f"Suno の入力を直す（{song['artist']}「{song['title']}」）"
        return None
    if action == "fill_suno_batch":   # タブを分けて 3 曲までまとめて入力（決定 98）
        return [PY, str(S / "browser_fill.py"), "suno", "--batch", "3"], "ブラウザで入力（Suno：まとめて 3 曲まで）"
    if action == "fill_lyrics":   # 登録した曲の歌詞を DistroKid の歌詞の画面に入れる（決定 52）
        song = _songs.by_id(str(p.get("id", ""))) if p.get("id") else None
        return [PY, str(S / "browser_fill.py"), "lyrics", song["id"] if song else "all"], \
            "ブラウザで入力（歌詞" + (f"：{song['artist']}「{song['title']}」）" if song else "：まだ出していない曲を続けて）")
    if action == "fill_distrokid_all":   # 残りの曲を続けて入れる（決定 51）
        return [PY, str(S / "browser_fill.py"), "distrokid", "all"], "ブラウザで入力（DistroKid：残りの曲を続けて）"
    if action == "fill_distrokid":
        song = _songs.by_id(str(p.get("id", "")))
        if song:
            return [PY, str(S / "browser_fill.py"), "distrokid", song["id"]], f"ブラウザで入力（DistroKid：{song['artist']}「{song['title']}」）"
    if action == "back":   # 間違えて進めた工程を戻す（決定 38）
        import publish
        song = _songs.by_id(str(p.get("id", "")))
        to = str(p.get("to") or "")
        if song and (not to or to in publish.BACK_TO):
            return [PY, str(S / "publish.py"), "back", song["id"], *(["--to", to] if to else [])], \
                f"戻す（{song['artist']}「{song['title']}」→ {publish.BACK_TO.get(to, '1 つ前')}）"
    if action == "photo_undo" and p.get("slug") in _slugs() and p.get("service") in ("spotify", "apple", "amazon"):
        return [PY, str(S / "artist_photo.py"), "undo", p["slug"], p["service"]], f"写真の記録を取り消す（{p['slug']} → {p['service']}）"
    if action == "fill_photo" and p.get("slug") in _slugs() and p.get("service") in ("spotify", "apple", "amazon"):
        return [PY, str(S / "browser_fill.py"), "photo", p["slug"], p["service"]], f"ブラウザで入力（写真：{p['slug']} → {p['service']}）"
    if action == "browser_login" and p.get("site") in ("suno", "distrokid", "spotify", "apple", "amazon"):
        return [PY, str(S / "browser_fill.py"), "login", p["site"]], f"ブラウザで入力（{p['site']} にログイン）"
    if action == "browser_parts" and p.get("site") in ("suno", "distrokid"):
        return [PY, str(S / "browser_fill.py"), "parts", p["site"]], f"ブラウザで入力（{p['site']} の画面の部品を記録）"
    if action == "browser_reset":
        return [PY, str(S / "browser_fill.py"), "reset"], "ブラウザで入力（専用の Chrome を作り直す）"
    if action == "terminal" and p.get("key") in TERMINAL:
        t = TERMINAL[p["key"]]
        cmd = f"cd {shlex.quote(str(ROOT))} && " + (t if isinstance(t, str) else " ".join(shlex.quote(x) for x in t))
        esc = cmd.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", 'tell application "Terminal" to activate', "-e", f'tell application "Terminal" to do script "{esc}"'], \
            f"ターミナルを開く（{p['key']}）"
    return None


# ターミナルで始める作業（鍵の入力など、Gon さんが打ち込む必要があるもの）
TERMINAL = {"openai": [PY, "scripts/setup_keys.py", "--only", "openai"], "anthropic": [PY, "scripts/setup_keys.py", "--only", "anthropic"], "spotify": [PY, "scripts/setup_keys.py", "--only", "spotify"],
            "owner": [PY, "scripts/setup_keys.py", "--only", "owner"], "ffmpeg": ["brew", "install", "ffmpeg"],
            # ブラウザで自動入力する部品（Playwright）と、Chrome が無いとき用のブラウザ
            # Homebrew の Python は --user だけだと断られるので、そのときは --break-system-packages を付けて入れ直す
            "playwright": f"{{ {shlex.quote(PY)} -m pip install --user playwright || {shlex.quote(PY)} -m pip install --user --break-system-packages playwright; }}"
                          f" && {shlex.quote(PY)} -m playwright install chromium",
            "schedule": [PY, "scripts/schedule.py", "install"],
            # 歌詞を聞き取る部品（決定 86）。最初の聞き取りのときに、モデル（約 500 MB）を自動でダウンロードする
            "whisper": f"{{ {shlex.quote(PY)} -m pip install --user faster-whisper || {shlex.quote(PY)} -m pip install --user --break-system-packages faster-whisper; }}",
            # メロディーを起こすときに歌を正確に取り出す部品（任意。約 2 GB。決定 90）
            "demucs": f"{{ {shlex.quote(PY)} -m pip install --user demucs || {shlex.quote(PY)} -m pip install --user --break-system-packages demucs; }}"}


def _fix_buttons(how: str) -> list[dict]:
    """「仕組みの詰まり」の直し方を、ワンクリックのボタンにする"""
    m = re.search(r"schedule\.py run (\w+)", how)
    if m:   # 定期実行のエラー：直したあとに、いま動かし直す
        warn = "（ブリーフは Claude に全組ぶんを書かせるので、料金がかかります）" if m.group(1) == "brief" else ""
        return [{"kind": "run", "label": "直したので表示を消す", "action": "ack_job", "job": m.group(1),
                 "confirm": "このエラーの表示を消しますか？（原因を直したとき。次の定期実行は、いつもどおり動きます）"},
                {"kind": "run", "label": "もう一度動かす", "action": "rerun_job", "job": m.group(1), "confirm": f"定期実行「{m.group(1)}」をいま動かします{warn}。よいですか？"}]
    for key, needle in (("openai", "--only openai"), ("anthropic", "--only anthropic"), ("spotify", "--only spotify"), ("owner", "--only owner"),
                        ("ffmpeg", "brew install ffmpeg"), ("playwright", "playwright"), ("schedule", "schedule.py install")):
        if needle in how:
            return [{"kind": "terminal", "key": key, "label": "直す（ターミナルが開きます）"}]
    return []


PROCS: dict[str, subprocess.Popen] = {}   # 動いている仕事（止めるため）


def stop_job(jid: str) -> bool:
    """動いている仕事を止める（ブラウザの入力を入り直すときなど）"""
    p = PROCS.get(jid)
    if p is None or p.poll() is not None:
        return False
    if jid in JOBS:
        JOBS[jid]["stopped"] = True   # 止める前に印を付ける（仕事の終わりの判定がこの印を見る）
    p.terminate()
    try:
        p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        p.kill()
    return True


def start_job(cmd: list[str], label: str) -> str:
    jid = secrets.token_hex(4)
    job = {"id": jid, "label": label, "state": "running", "started": datetime.now().strftime("%H:%M:%S"), "out": ""}
    with LOCK:
        JOBS[jid] = job

    if label.startswith("ブラウザで入力") and cmd and cmd[0] == PY:   # Playwright が入っている Python で動かす（決定 59）
        import _common
        _common._PY_WITH.pop("playwright", None)   # 入れ直した直後でも見つけられるように、毎回探す
        cmd = [_common.python_with("playwright") or PY, *cmd[1:]]

    def run():
        try:
            load_dotenv(override=True)   # あとで直した .env（本名・鍵など）を、ダッシュボードを動かしたままでも使う
            # 出力を 1 行ずつ読む：途中の様子（何曲中の何曲目・いまの工程）をダッシュボードにすぐ出すため（決定 65）
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                 env={**os.environ, "PYTHONUNBUFFERED": "1",
                                      "PATH": os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"})
            PROCS[jid] = p
            killer = threading.Timer(3600, p.kill)   # 1 時間で止める（これまでと同じ）
            killer.start()
            lines: list[str] = []
            for ln in p.stdout:
                ln = ln.rstrip("\n")
                lines.append(ln)
                if ln.startswith("【進み具合】"):   # 「何曲中の何曲目」：2/5 曲目：HOLLOWPLAN「…」
                    job["progress"], job["step"] = ln[len("【進み具合】"):], ""
                elif ln.startswith("【工程】"):     # その曲の中の工程：3/7：丸を選んでいます
                    job["step"] = ln[len("【工程】"):]
                job["out"] = "\n".join(lines)[-20000:]
            p.wait()
            killer.cancel()
            PROCS.pop(jid, None)
            r = subprocess.CompletedProcess(cmd, p.returncode, "\n".join(lines), "")
            job["out"] = r.stdout[-20000:]
            if job.get("stopped"):   # 入り直すために止めた
                job["state"], job["summary"] = "incomplete", "止めました（もう一度入力するため）"
                return
            # 0：済んだ／2：まだ終わっていない（ログインが要る・入っていない欄があるなど）／ほか：エラー
            job["state"] = {0: "done", 2: "incomplete"}.get(r.returncode, "error")
            # 部品が「【結果】…」と書いていれば、それを結果の一文として出す（確かめた結果だけを「済んだ」と言う）
            res = [ln[len("【結果】"):] for ln in r.stdout.splitlines() if ln.startswith("【結果】")]
            if res:
                job["summary"] = res[-1]
        except Exception as e:  # noqa: BLE001
            job["out"], job["state"] = str(e), "error"
    threading.Thread(target=run, daemon=True).start()
    return jid


# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Label Dashboard</title><style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#22201c;--sub:#6f6a60;--muted:#9a948a;--line:#e2ddd2;--grid:#efebe3;--wk:#f1ede5;--accent:#a07a3c;
--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--good-t:rgba(12,163,12,.16);--warn-t:rgba(250,178,25,.22);--crit-t:rgba(208,59,59,.16);--plan-t:rgba(110,106,96,.07)}
@media (prefers-color-scheme:dark){:root{--bg:#181715;--card:#22211e;--ink:#ece8df;--sub:#a49e92;--muted:#7d786f;--line:#34322d;--grid:#2a2926;--wk:#1f1e1b;--accent:#d2ad6b;
--good-t:rgba(12,163,12,.26);--warn-t:rgba(250,178,25,.22);--crit-t:rgba(208,59,59,.30);--plan-t:rgba(236,232,223,.05)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
.bar{max-width:1280px;margin:0 auto;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.bar h1{font-size:17px;font-weight:500;letter-spacing:.04em;margin:0 auto 0 0}.bar a{color:var(--ink);text-decoration:none;font-size:14px}
main{max-width:1280px;margin:0 auto;padding:12px 16px 64px}
h2{font-weight:500;font-size:16px;margin:24px 0 10px}.sub{color:var(--sub);font-size:12px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:16px 0 12px}
.tab{background:none;color:var(--sub);border:1px solid transparent;border-bottom:0;border-radius:8px 8px 0 0;padding:8px 16px;font:inherit;cursor:pointer}
.tab[aria-selected=true]{background:var(--card);color:var(--ink);border-color:var(--line);margin-bottom:-1px;font-weight:600}
.tab .n{font-size:12px;color:var(--sub);margin-left:4px;font-weight:400}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--sub);margin:0 0 8px}.legend span{display:inline-flex;align-items:center;gap:5px}
.sw{display:inline-block;width:22px;height:12px;border-radius:3px;border:1.5px solid}
.gwrap{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:auto;max-height:70vh}
.g{display:grid;position:relative}.gl{position:sticky;left:0;z-index:2;background:var(--card);border-right:1px solid var(--line);padding:6px 10px;border-bottom:1px solid var(--grid);min-width:0}
.gl b{font-weight:600;font-size:13px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.gl small{color:var(--sub);font-size:11px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gl.first{border-top:1px solid var(--line)}.gh{position:sticky;top:0;z-index:3;background:var(--card);border-bottom:1px solid var(--line)}
.gt{position:relative;border-bottom:1px solid var(--grid)}.day{position:absolute;top:0;bottom:0;border-left:1px solid var(--grid)}.day.we{background:var(--wk)}
.hd{position:absolute;top:0;font-size:11px;color:var(--sub);padding:3px 0 0 3px;white-space:nowrap}.hd.mon{color:var(--ink);font-weight:600}
.today{position:absolute;top:0;bottom:0;width:2px;background:var(--accent);z-index:1}.todaylab{position:absolute;top:0;font-size:10px;color:#fff;background:var(--accent);padding:0 4px;border-radius:3px;transform:translateX(-50%);z-index:4}
.b{position:absolute;height:20px;border-radius:4px;border:1.5px solid;font-size:11px;line-height:17px;padding:0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer;color:var(--ink);z-index:2}
.b:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.b.done{background:var(--good-t);border-color:var(--good)}.b.wait{background:var(--warn-t);border-color:var(--warn)}.b.ng{background:var(--crit-t);border-color:var(--crit);font-weight:600}
.b.run{background:transparent;border-color:var(--ink);border-style:dashed}.b.plan{background:var(--plan-t);border-color:var(--muted);border-style:dotted;color:var(--sub)}
.m{position:absolute;width:14px;height:14px;transform:rotate(45deg);border:2px solid;cursor:pointer;z-index:2}.m.done{background:var(--good);border-color:var(--good)}.m.plan{background:var(--card);border-color:var(--muted)}
#tip{position:fixed;z-index:20;max-width:320px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.15);pointer-events:none;display:none}
.panel{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:12px 14px;margin:12px 0}
.panel.ng{border-left-color:var(--crit)}.panel.done{border-left-color:var(--good)}
code{font-size:12px;color:var(--sub);word-break:break-all}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}
button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}button:disabled{opacity:.5;cursor:default}
.tools{display:flex;gap:8px;flex-wrap:wrap}ul.list{list-style:none;padding:0;margin:0}ul.list li{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px;padding:8px 12px;margin:6px 0;font-size:14px}
ul.list li.ng{border-left-color:var(--crit)}ul.list li.run{border-left-color:var(--muted)}.who{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 6px;margin-right:6px;color:var(--sub)}
.days{color:var(--crit);font-size:12px;margin-left:4px}.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--sub);font-weight:500;white-space:nowrap}tr:last-child td{border-bottom:none}.empty{color:var(--sub)}
details{margin:10px 0}summary{cursor:pointer;color:var(--ink)}pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px;font-size:12px;max-height:320px;overflow:auto}
input[type=date]{font:inherit;font-size:13px;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.static .act-only{display:none!important}
.board{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow-x:auto}
.board table{min-width:760px;font-size:13px;table-layout:fixed}.board th:first-child{width:220px}.board th{text-align:center;white-space:nowrap;padding:8px 4px;font-size:12px}.board th:first-child{text-align:left;padding-left:12px}
.board td{padding:6px 4px;text-align:center;vertical-align:middle}.board td.an{text-align:left;padding:8px 12px;min-width:170px;position:sticky;left:0;background:var(--card);z-index:1;border-right:1px solid var(--line)}
.board td.an b{display:block;font-size:14px}.board td.an small{display:block;color:var(--sub);font-size:12px;line-height:1.4}
.cell{width:100%;min-height:44px;border-radius:8px;border:1.5px solid var(--line);background:transparent;color:var(--sub);font:inherit;font-size:12px;cursor:pointer;padding:4px;line-height:1.35}
.cell:hover{outline:2px solid var(--accent);outline-offset:1px}.cell.done{background:var(--good-t);border-color:var(--good);color:var(--ink)}
.cell.wait{background:var(--warn-t);border-color:var(--warn);color:var(--ink);font-weight:600}.cell.ng{background:var(--crit-t);border-color:var(--crit);color:var(--ink);font-weight:700}
.backbox{border:1.5px dashed var(--muted);border-radius:10px;padding:8px 12px;margin:10px 0}.backbox .acts{margin-top:6px}
.fwdbox{border:1.5px solid var(--good);background:var(--good-t);border-radius:10px;padding:8px 12px;margin:10px 0}.fwdbox .acts{margin-top:6px}
.cell.run{border-style:dashed;border-color:var(--ink);color:var(--ink)}.cell.plan{border-style:dotted;border-color:var(--muted)}
.cell .d{display:block;font-weight:400;font-size:11px;color:var(--crit)}
dialog{border:1px solid var(--line);border-radius:14px;padding:0;max-width:min(640px,calc(100vw - 32px));width:100%;background:var(--card);color:var(--ink);box-shadow:0 10px 40px rgba(0,0,0,.25)}
dialog::backdrop{background:rgba(0,0,0,.35)}.dlg-in{padding:18px 20px 20px}.dlg-in h3{margin:2px 0 8px;font-size:18px;font-weight:600}
.dlg-in h4{margin:16px 0 6px;font-size:14px;color:var(--sub);font-weight:600}.dlg-in ol{margin:0;padding-left:1.3em}.dlg-in ol li{margin:4px 0}
.x{position:absolute;right:10px;top:8px;background:transparent;color:var(--sub);font-size:22px;padding:2px 10px}
.badge{display:inline-block;font-size:12px;border-radius:999px;padding:2px 10px;border:1.5px solid}.badge.done{border-color:var(--good)}.badge.wait{border-color:var(--warn)}.badge.ng{border-color:var(--crit)}.badge.run{border-style:dashed}.badge.plan{border-style:dotted;border-color:var(--muted)}
.acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center}a.btn{display:inline-block;text-decoration:none;border-radius:6px;padding:6px 12px;font-size:13px;border:1px solid var(--line);color:var(--ink)}
#dlg-job{margin:8px 0 4px;font-size:14px;color:var(--sub);white-space:pre-line}#dlg-job:not(:empty){border:1.5px solid var(--line);border-radius:8px;padding:6px 10px}#dlg-job.running{border-color:var(--warn);background:var(--warn-t);color:var(--ink)}.job.done{color:var(--good)}.job.incomplete{color:#b07a00}.job.error{color:var(--crit)}#dlg-job.job{font-weight:600}ul.list li.click{cursor:pointer}ul.list li.click:hover{outline:2px solid var(--accent)}
.irr-f label{display:block;margin:10px 0 3px;font-size:13px;color:var(--sub)}.irr-f input[type=text],.irr-f textarea,.irr-f select{width:100%;font:inherit;font-size:14px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.irr-f textarea{min-height:5em;resize:vertical;line-height:1.5}.irr-f textarea.lyr{min-height:16em;font-size:13px}.irr-row{display:flex;gap:12px;flex-wrap:wrap}.irr-f label.chk{display:flex;gap:8px;align-items:center;margin:10px 0 2px;font-weight:600}.irr-f label.chk input{width:auto;margin:0}.irr-row>div{flex:1 1 220px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}.chips button{font-size:12px;padding:2px 10px}.irr-note{background:var(--bg);border-radius:8px;padding:8px 10px;font-size:13px;white-space:pre-wrap}
.redo{border:1.5px dashed var(--crit);border-radius:10px;padding:8px 12px;margin:14px 0 4px}
</style></head><body>
<header><div class="bar"><h1>Postdog Records</h1><a href="/suno" class="act-only">Suno に貼る内容</a> <a href="/check" class="act-only">公開の準備</a> <a href="/phone" class="act-only">スマホで進める</a> <a href="/photos" class="act-only">アーティスト写真</a><span class="sub" id="upd"></span></div></header>
<main>
<div class="tools act-only">
 <button data-act="fill_suno">作曲待ちの曲を Suno に入力する</button><button data-act="fill_suno_batch" class="ghost" data-confirm="作曲待ちの曲を、タブを分けて 3 曲までまとめて Suno に入力します。&#10;入れ終わったらシステムは Chrome から離れるので、各タブで Gon さんが Create を押してください（タブの間は 10〜20 秒あける）。&#10;タブ同士で中身が書き換わったら、自動で 1 曲だけに戻します。始めますか？">まとめて Suno に入力する（3 曲まで）</button><button data-act="scan" class="ghost">曲を取り込む</button><button data-act="track" class="ghost">配信を確かめる</button>
 <button data-act="sync" class="ghost">保管する（ローカル・SSD）</button><button data-act="ref_audio" class="ghost" data-confirm="SSD の「既存楽曲」の WAV・MP3・M4A を解析して、参考曲に登録します（1 曲 10〜30 秒。解析済みの曲は飛ばします）。始めますか？">既存楽曲を解析する（参考曲に登録）</button><button data-act="ref_health" class="ghost">参考曲の取り込みを診断する</button><button data-act="ref_ensure" class="ghost" data-confirm="SSD の中の解析シートを探して Mac に読み込み、解析シートが無い既存楽曲を全部解析して、SSD の「既存楽曲_解析シート」に保存します（1 曲 10〜30 秒・料金なし。曲が多いと時間がかかります）。始めますか？">解析シートを SSD に作る（無い曲は全部解析）</button><button data-act="ref_audio_report" class="ghost">解析できていない曲を調べる</button><button data-act="ref_lyrics" class="ghost" id="ref-lyrics" hidden>歌詞を聞き取る</button><button data-act="terminal" data-key="whisper" class="ghost" id="ref-whisper" hidden>歌詞を聞き取る部品を入れる（ターミナルが開きます）</button><button data-act="ref_melody" class="ghost" id="ref-melody" hidden>メロディーを起こす</button><button data-act="terminal" data-key="demucs" class="ghost" id="ref-demucs" hidden>メロディーを正確にする部品を入れる（任意・約 2 GB・ターミナルが開きます）</button><button data-act="ref_enrich" class="ghost" id="ref-enrich" hidden>足りない項目を Claude に調べてもらう</button><button data-act="influences" class="ghost" id="ref-infl" data-confirm="SSD のデータベース（SSDDB）にいるアーティストの中から、デビューしている組ごとに、影響を受けたアーティストを 3 組まで選びます。&#10;管理表の「影響」タブに『採用』で足して設定書を作り直し、その人たちの曲の歌詞・メロディーを先に起こして、今後の曲の参考に優先して使います。&#10;SSD をつないでおいてください。料金の目安：Claude に選んでもらう分 5 組で 20〜60 円。&#10;始めますか？">影響を受けたアーティストを選ぶ（SSDDB から）</button><button data-act="influences_analyze" class="ghost" id="ref-infl-an" hidden>影響を受けたアーティストの曲を解析する</button><button data-act="explore_report" class="ghost">方向性の探り具合を見る</button><button data-act="collab_fit" class="ghost" data-confirm="まだ吟味していない組の組み合わせのコラボの相性を、Claude が吟味して管理表の「コラボの相性」タブに書きます（1 組み合わせ 約 5〜10 円）。SSD をつないでおいてください。始めますか？">コラボの相性を吟味する</button>
</div>
<div id="jobs"></div>
<div class="tabs" role="tablist">
 <button class="tab" role="tab" id="tab-regular" aria-controls="pane-regular">定期配信<span class="n" id="n-regular"></span></button>
 <button class="tab" role="tab" id="tab-irregular" aria-controls="pane-irregular">不定期・毎日配信<span class="n" id="n-irregular"></span></button>
 <button class="tab" role="tab" id="tab-debut" aria-controls="pane-debut">デビュー<span class="n" id="n-debut"></span></button>
</div>
<div class="legend" aria-label="凡例">
 <span><i class="sw" style="background:var(--good-t);border-color:var(--good)"></i>✓ 済み</span>
 <span><i class="sw" style="background:var(--warn-t);border-color:var(--warn)"></i>● Gon さんの番</span>
 <span><i class="sw" style="background:var(--crit-t);border-color:var(--crit)"></i>！ 止まっている・遅れ</span>
 <span><i class="sw" style="border-style:dashed;border-color:var(--ink)"></i>… 自動で進行中</span>
 <span><i class="sw" style="border-style:dotted;border-color:var(--muted)"></i>○ 予定</span>
 <span>◆ 配信日（日程）</span>
</div>
<section id="pane-regular" role="tabpanel" aria-labelledby="tab-regular">
 <p class="sub">デビュー曲が配信された組の、いま作っている曲がどのフェーズにいるか。マスを押すと、止まっている原因と Gon さんがやること・ボタンが出ます。</p>
 <div class="board" id="b-regular"></div>
 <h2>今やること（定期配信）</h2><div id="todo-regular"></div>
 <details><summary>日程を見る（ガントチャート）</summary><div class="gwrap"><div class="g" id="g-regular"></div></div></details>
 <details><summary>曲の一覧</summary><div id="songs-regular"></div></details>
</section>
<section id="pane-irregular" role="tabpanel" aria-labelledby="tab-irregular" hidden>
 <p class="sub">毎日配信：期間を決めて、5 組の持ち回りでレーベル全体で毎日 1 曲を出します（配信日は決まった日）。不定期配信：デビュー曲が配信中の組で、毎週の曲とは別に 1 曲を出します。組を選んで「曲の案を作る」を押すと、曲名・イメージ・歌詞などの案ができ、ポップアップで直せます。好みでなければ却下して作り直し、よければ作曲（Suno）へ進めます。不定期の曲の配信日はいつもいちばん早い日です。</p>
 <div class="panel act-only" id="daily-box"></div>
 <div class="panel irr-f act-only" id="irr-form"></div>
 <h2>曲の案（作曲の前）</h2><div id="irr-drafts"></div>
 <h2>作曲へ進めた曲</h2>
 <div class="board" id="b-irregular"></div>
 <h2>今やること（不定期配信）</h2><div id="todo-irregular"></div>
 <details><summary>日程を見る（ガントチャート）</summary><div class="gwrap"><div class="g" id="g-irregular"></div></div></details>
 <details><summary>曲の一覧</summary><div id="songs-irregular"></div></details>
</section>
<section id="pane-debut" role="tabpanel" aria-labelledby="tab-debut" hidden>
 <p class="sub">デビュー前の組。デビュー曲が配信されると、自動で「定期配信」に移ります。マスを押すと、止まっている原因と Gon さんがやること・ボタンが出ます。</p>
 <div class="board" id="b-debut"></div>
 <h2>今やること（デビュー）</h2><div id="todo-debut"></div>
 <details><summary>日程を見る（ガントチャート）</summary><div class="gwrap"><div class="g" id="g-debut"></div></div></details>
 <details><summary>曲の一覧</summary><div id="songs-debut"></div></details>
</section>
<h2>仕組みの詰まり</h2><div id="system"></div>
<details><summary>管理表の「詰まっているところ」</summary><div id="tracker"></div></details>
</main>
<div id="tip" role="tooltip"></div>
<dialog id="dlg" aria-labelledby="dlg-title"><form method="dialog"><button class="x" aria-label="閉じる">×</button></form><div class="dlg-in" id="dlg-body"></div></dialog>
<script>
const TOKEN="__TOKEN__";const STATIC=__STATIC__;
if(STATIC)document.body.classList.add("static");
const E=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ICON={done:"✓",wait:"●",ng:"！",run:"…",plan:"○"},STATE={done:"済み",wait:"だれかの番",ng:"止まっている・遅れ",run:"自動で進行中",plan:"予定"};
const DAY=86400000,D=s=>new Date(s+"T00:00:00"),md=d=>`${d.getMonth()+1}/${d.getDate()}`,WD="日月火水木金土";
let DATA=null,TAB="regular";
const TABS=["regular","irregular","debut"];
const EMPTY={debut:"デビュー前の組はありません。全組が定期配信に移りました。",regular:"定期配信の組はまだありません。デビュー曲が配信されると、ここに並びます（いまは「デビュー」のタブ）。",
  irregular:"作曲へ進めた不定期の曲・毎日配信の曲はまだありません。上で曲の案を作り「この案で作曲へ進む」を押すか、毎日配信を始めると、ここに並びます。"};
function dailyBox(){const el=document.getElementById("daily-box"),D=DATA.daily||{};   // 毎日配信（決定 94）
  const so=D.social||{},sns=`<hr><b>SNS の毎日の投稿：${so.on?`オン（1 日 ${so.per_day} 本）`:"オフ"}</b><p class="sub">配信日の曲は 17:00 ET、ほかは配信中の曲から選んで 12:00・20:00 ET に、YouTube ショート・インスタ・TikTok へ。説明文は「この曲が生まれた日」（曲のエピソード）。背景の映像は ${E(so.bg_dir||"")} の組ごとのフォルダに Blender の書き出しを入れると使います（無ければジャケット）。背景はゆったり動き、映像が 2 本以上あれば拍に合わせて切り替えます。新曲の日の投稿には CapCut 用の素材（capcut フォルダ）も作り、そこに「完成.mp4」を置くと、その動画を投稿します。</p>`
    +(so.on?`<button data-act="social_daily_plan">投稿の案を作る（3 日先まで）</button> <button class="ghost" data-act="social_daily_off" data-confirm="SNS の毎日の投稿を止めます（作った案は投稿しません）。止めますか？">SNS の毎日の投稿を止める</button>`
      :`<button data-act="social_daily_on" data-per_day="2" data-confirm="SNS の毎日の投稿を始めます（1 日 2 本）。3 日先までの投稿の案（縦動画・説明文）を作り、投稿時刻を過ぎると定期実行が投稿します。\n鍵（YouTube・インスタ・TikTok）が無いサービスは飛ばします。始めますか？">SNS の毎日の投稿を始める（1 日 2 本）</button>`);
  if(!D.active){el.innerHTML=`<b>毎日配信（5 組の持ち回り）</b><p class="sub">レーベル全体で毎日 1 曲（1 組あたり週 1〜2 曲）。配信日は決まった日の 17:00 ET。始める日は ${E(D.earliest||"")} から 30 日（曲づくり・登録・Spotify への推薦が間に合うよう 2 週間あけます）。</p>`
    +`<button data-act="daily_start" data-confirm="毎日配信を ${E(D.earliest||"")} から 30 日、デビュー曲が配信中の組の持ち回りで始めます。\nこの期間に配信日が入るふだんの毎週の制作は止め、毎日配信の曲が代わりになります。\n曲は 21 日先までを先に作ります（毎週火曜の定期実行でも続きを作ります）。\n始めますか？">毎日配信を始める</button>`+sns;return}
  const nx=D.next?`次は ${E(D.next.date)}（${E(D.next.slug)}）`:"";
  el.innerHTML=`<b>毎日配信：${E(D.start)}〜${E(D.end)}（${D.days} 日）</b><p class="sub">曲がある日 ${D.filled}/${D.days}。持ち回り：${(D.order||[]).map(E).join(" → ")}。${nx}</p>`
    +`<button data-act="daily_make" data-confirm="21 日先までの、曲がまだ無い日の曲を作ります（参考曲の割り当て → Claude が書く。1 曲 約 30 円）。始めますか？">毎日配信の曲を作る（21 日先まで）</button> `
    +`<button class="ghost" data-act="daily_stop" data-confirm="毎日配信を止めて、ふだんの毎週の制作に戻します（作った曲と配信日はそのまま）。止めますか？">毎日配信を止める</button>`+sns}
function selectTab(t){TAB=t;for(const k of TABS){document.getElementById("tab-"+k).setAttribute("aria-selected",k===t);document.getElementById("pane-"+k).hidden=k!==t}
  if(!location.hash.startsWith("#"+t))history.replaceState(null,"","#"+t)}
for(const k of TABS)document.getElementById("tab-"+k).onclick=()=>selectTab(k);
selectTab(TABS.find(k=>location.hash.startsWith("#"+k))||"regular");
const OPEN={};
function gantt(key){
  const sec=DATA.timeline[key],el=document.getElementById("g-"+key);
  const small=window.innerWidth<640,from=D(sec.range.from),to=D(sec.range.to),n=Math.round((to-from)/DAY)+1,W=small?22:30,LW=small?150:240,today=D(DATA.timeline.today);
  el.style.gridTemplateColumns=`${LW}px ${n*W}px`;
  if(!sec.rows.length){el.style.gridTemplateColumns="1fr";el.innerHTML=`<div class="gl" style="position:static"><span class="empty">${EMPTY[key]}</span></div>`;return}
  const x=d=>Math.round((D(d)-from)/DAY)*W,tx=Math.round((today-from)/DAY)*W+W/2,showToday=today>=from&&today<=to;
  let days="";for(let i=0;i<n;i++){const d=new Date(+from+i*DAY);days+=`<div class="day ${d.getDay()%6===0?"we":""}" style="left:${i*W}px;width:${W}px"></div>`}
  const tline=showToday?`<div class="today" style="left:${tx-1}px"></div>`:"";
  let html=`<div class="gl gh" style="height:30px"><small>${key==="debut"?"組・デビュー曲／工程":"組・曲（制作週）／工程"}</small></div><div class="gt gh" style="height:30px">`;
  for(let i=0;i<n;i++){const d=new Date(+from+i*DAY);if(d.getDay()===1||i===0)html+=`<span class="hd mon" style="left:${i*W}px">${md(d)}（${WD[d.getDay()]}）</span>`}
  if(showToday)html+=`<span class="todaylab" style="left:${tx}px;top:14px">今日</span>`;
  html+=`</div>`;
  let prev="";
  sec.rows.forEach((r,ri)=>{
    const gid=key+":"+ri,steps=r.phases.filter(p=>!p.milestone),ms=r.phases.find(p=>p.milestone);
    const open=!r.planned&&(OPEN[gid]??(!!r.blocker||key==="debut"));
    const first=r.artist!==prev;prev=r.artist;
    const blk=r.blocker?`<small>${ICON[r.blocker.state]||""} ${E(r.blocker.who||"")}${r.blocker.who?"：":""}${E(r.blocker.name)}</small>`:(r.planned?"":`<small>${ICON.done} 進行中の工程なし</small>`);
    // 見出しの行（曲ごと）：全体の期間の細い線と ◆ 配信日
    const s0=Math.max(0,x(steps.reduce((a,p)=>p.start<a?p.start:a,steps[0].start))),e0=Math.min(n*W,x((ms||steps[steps.length-1]).end)+W);
    const hh=r.planned?32:60;
    html+=`<div class="gl ${first&&ri?"first":""}" style="height:${hh}px;cursor:${r.planned?"default":"pointer"}" ${r.planned?"":`data-g="${gid}"`}>`
      +`<b>${first?E(r.artist):""}${r.planned?"":` <span style="font-weight:400;color:var(--sub)">${open?"▾":"▸"}</span>`}</b><small>${E(r.sub)}</small>${blk}</div>`;
    html+=`<div class="gt ${first&&ri?"first":""}" style="height:${hh}px">${days}${tline}`;
    if(e0>s0)html+=`<div class="b ${r.planned?"plan":"run"}" style="left:${s0+1}px;top:${r.planned?7:20}px;width:${Math.max(8,e0-s0-2)}px;height:16px;line-height:13px;border-style:${r.planned?"dotted":"solid"};border-color:var(--muted);background:var(--plan-t);color:var(--sub)" data-p="${gid}:${r.phases.indexOf(ms||steps[0])}">${r.planned?"○ 予定：ブリーフ → 配信":""}</div>`;
    if(ms){const mx=x(ms.start)+W/2-7;if(mx>=0&&mx<=n*W)html+=`<div class="m ${ms.state==="done"?"done":"plan"}" tabindex="0" data-p="${gid}:${r.phases.indexOf(ms)}" style="left:${mx}px;top:${r.planned?9:21}px" aria-label="${E(ms.name)} ${E(ms.start)}"></div>`}
    html+=`</div>`;
    if(!open)return;
    steps.forEach(p=>{
      const id=`${gid}:${r.phases.indexOf(p)}`,s=Math.max(0,x(p.start)),e=Math.min(n*W,x(p.end)+W),d1=D(p.start),d2=D(p.end);
      const range=p.start===p.end?md(d1):`${md(d1)}〜${md(d2)}`;
      html+=`<div class="gl" style="height:28px;padding:4px 10px 4px 22px"><small style="color:var(--ink);font-size:12px">${ICON[p.state]} ${E(p.name)}${p.who?` <span style="color:var(--sub)">・${E(p.who)}</span>`:""}</small></div>`;
      html+=`<div class="gt" style="height:28px">${days}${tline}`;
      if(e>0&&s<n*W){const w=Math.max(10,e-s-2);html+=`<div class="b ${p.state}" tabindex="0" data-p="${id}" style="left:${s+1}px;top:4px;width:${w}px" aria-label="${E(p.name)}：${E(STATE[p.state])} ${range}">${w>70?`${ICON[p.state]} ${range}`:ICON[p.state]}</div>`}
      html+=`</div>`});
  });
  el.innerHTML=html;
  el.parentElement.scrollLeft=Math.max(0,tx-W*6);
}
document.addEventListener("click",e=>{const g=e.target.closest("[data-g]");if(!g)return;const[k,ri]=g.dataset.g.split(":");const r=DATA.timeline[k].rows[+ri];
  OPEN[g.dataset.g]=!(OPEN[g.dataset.g]??(!!r.blocker||k==="debut"));gantt(k)});
function phaseOf(id){const[k,ri,pi]=id.split(":");const r=DATA.timeline[k].rows[+ri];return[k,r,r.phases[+pi]]}
function tipHTML(r,p){const d1=D(p.start),d2=D(p.end);return `<b>${E(r.artist)}</b> ${E(r.sub)}<br><b>${ICON[p.state]} ${E(p.name)}</b>（${E(STATE[p.state])}）<br>${md(d1)}${p.start!==p.end?"〜"+md(d2):""}${p.who?`　担当：${E(p.who)}`:""}<br>${E(p.detail)}`}
const tip=document.getElementById("tip");
document.addEventListener("mouseover",e=>{const b=e.target.closest("[data-p]");if(!b){tip.style.display="none";return}const[,r,p]=phaseOf(b.dataset.p);tip.innerHTML=tipHTML(r,p);tip.style.display="block"});
document.addEventListener("mousemove",e=>{if(tip.style.display==="block"){tip.style.left=Math.min(e.clientX+14,innerWidth-330)+"px";tip.style.top=(e.clientY+14)+"px"}});
const WHO=w=>String(w||"").replace(/あなた/g,"Gon さん");
const LABEL={done:"済み",wait:"Gon さんの番",ng:"止まっている・遅れ",run:"自動で進行中",plan:"まだ先"};
const daysSince=s=>Math.max(0,Math.round((D(DATA.timeline.today)-D(s))/DAY));
let ACTS=[];
function actHTML(a,i,r){const id=E(a.id||r.id||""),slug=E(a.slug||r.slug||"");
  switch(a.kind){
   case "run":return `<button class="act-only ${a.primary?"":"ghost"}" data-act="${E(a.action)}" ${a.slug?`data-slug="${slug}"`:""} ${a.id?`data-id="${id}"`:""} ${a.week?`data-week="${E(a.week)}"`:""} ${a.site?`data-site="${E(a.site)}"`:""} ${a.to?`data-to="${E(a.to)}"`:""} ${a.service?`data-service="${E(a.service)}"`:""} ${a.redo?`data-redo="1"`:""} ${a.job?`data-job="${E(a.job)}"`:""} ${a.confirm?`data-confirm="${E(a.confirm)}"`:""}>${E(a.label)}</button>`;
   case "cover_pick":return `<span class="act-only" style="display:inline-block;width:150px;vertical-align:top;font-size:12px"><img src="${E(a.img)}" alt="ジャケットの候補 ${E(a.no)}" loading="lazy" style="width:150px;height:150px;object-fit:cover;border-radius:6px;display:block;margin-bottom:4px;border:1px solid var(--line)"><button data-act="cover" data-id="${id}" data-no="${E(a.no)}">${E(a.label)}</button>${a.note?`<span class="sub" style="display:block">${E(a.note)}</span>`:""}</span>`;
   case "copy":return `<button class="ghost" data-copy="${i}">${E(a.label)}</button>`;
   case "link":return `<a class="btn" href="${E(a.url)}" target="_blank" rel="noopener">${E(a.label)} ↗</a>`;
   case "terminal":return `<button class="ghost act-only" data-act="terminal" data-key="${E(a.key)}">${E(a.label)}</button>`;
   case "folder":return `<button class="ghost act-only" data-act="open_folder" data-id="${id}">${E(a.label)}</button>`;
   case "artist_folder":return `<button class="ghost act-only" data-act="open_artist" data-slug="${slug}">${E(a.label)}</button>`;
   case "mark_live":return `<span class="act-only" style="display:block"><input type="text" id="lt-${id}" placeholder="Spotify の曲の住所（https://open.spotify.com/track/…・なくても可）" style="min-width:22em;margin:2px 0"><br><input type="text" id="la-${id}" placeholder="Spotify のアーティストの住所（https://open.spotify.com/artist/…）" style="min-width:22em;margin:2px 0"><br><button data-act="mark_live" data-id="${id}">${E(a.label)}</button></span>`;
   case "artist_page":return `<span class="act-only"><input type="text" id="ap-${slug}" placeholder="spotify:artist:… か https://open.spotify.com/artist/…" style="min-width:18em"> <button data-act="artist_page" data-slug="${slug}">${E(a.label)}</button></span>`;
   case "retake":return `<span class="act-only rt" style="display:block;margin:6px 0"><b>Suno で聴いて 2 曲とも良くないとき</b>：まず同じ入力のまま Create をもう一度（料金 0 円・Claude も使わない）。それでもだめなら、理由を選んで直す（1 回目は機械の直しで 0 円、2 回目は Claude の小さな書き直しで数円）<br>${(a.reasons||[]).map(x=>`<label style="display:inline-block;margin:2px 8px 2px 0;font-weight:400"><input type="checkbox" value="${E(x)}" style="width:auto"> ${E(x)}</label>`).join("")}<br><button class="ghost" data-act="suno_retake" data-id="${id}">理由のとおりに直す（このあと「Suno に入力する」）</button></span>`;
   case "registered":return `<span class="act-only"><input type="date" id="d-${id}" value="${DATA.timeline.today}"> <button data-act="registered" data-id="${id}">${E(a.label)}</button></span>`;
  }return""}
function openGuide(gid){const[k,ri,pi]=gid.split(":");const r=DATA.timeline[k].rows[+ri],p=r.phases[+pi],g=p.guide||{cause:p.detail,steps:[],actions:[]};
  ACTS=g.actions||[];const cur=["wait","ng","run"].includes(p.state)&&!p.milestone;const d=cur?daysSince(p.start):0;
  const dates=p.start===p.end?md(D(p.start)):`${md(D(p.start))}〜${md(D(p.end))}`;
  document.getElementById("dlg-body").innerHTML=`<p class="sub">${E(r.artist)}・${E(r.sub)}</p>
   <h3 id="dlg-title">${ICON[p.state]} ${E(p.name)}</h3>
   <span class="badge ${p.state}">${E(LABEL[p.state])}${cur&&p.who?`・${E(WHO(p.who))}`:""}${d?`・${d} 日`:""}</span> <span class="sub">${p.state==="plan"?"予定 ":""}${dates}</span>
   ${(g.forward||[]).length?`<div class="fwdbox"><b>→ 次に進める（うまくいったとき）</b><span class="sub">　表が進んでいないときに押すと、記録して次の工程へ進めます</span><div class="acts">${g.forward.map((a,i)=>actHTML(a,i,r)).join("")}</div></div>`:""}
   ${(r.redo||[]).length?`<div class="backbox"><b>↻ もう一度入力する</b><span class="sub">　前の入力が残っていても、止めてから入り直します</span><div class="acts">${r.redo.map((a,i)=>actHTML(a,i,r)).join("")}</div></div>`:""}
   ${(r.back||[]).length?`<div class="backbox"><b>← 戻る（間違えて進めたとき）</b><span class="sub">　前の工程に戻します。消さずに「戻したもの」に移すので、あとから取り出せます</span><div class="acts">${r.back.map((a,i)=>actHTML(a,i,r)).join("")}</div></div>`:""}
   <h4>${p.state==="ng"?"止まっている原因":"いまの状態"}</h4><p>${E(WHO(g.cause))}</p>
   ${(g.steps||[]).length?`<h4>Gon さんがやること</h4><ol>${g.steps.map(x=>`<li>${E(WHO(x))}</li>`).join("")}</ol>`:""}
   ${ACTS.length?`<div class="acts">${ACTS.map((a,i)=>actHTML(a,i,r)).join("")}</div>`:""}`;
  // 動いている仕事（「ブラウザで入力（…）実行中…」など）は、ポップアップの上部（見出しのすぐ下）に出す（決定 64）
  const badge=document.querySelector("#dlg-body .badge");const dj=document.createElement("div");dj.id="dlg-job";
  (badge?badge.parentNode.insertBefore(dj,badge.nextElementSibling&&badge.nextElementSibling.nextElementSibling||null):document.getElementById("dlg-body").prepend(dj));
  const rj=(DATA.jobs||[]).filter(j=>j.state==="running").reverse().find(j=>String(j.label).includes(r.artist))||(DATA.jobs||[]).filter(j=>j.state==="running").pop();
  if(rj){dj.className="job running";dj.textContent=`${rj.label}　実行中…${PROGL(rj)}`;if(!watching[rj.id])watch(rj.id)}
  const dlg=document.getElementById("dlg");if(!dlg.open)dlg.showModal()}
document.addEventListener("click",e=>{const c=e.target.closest("[data-copy]");if(!c)return;const t=(ACTS[+c.dataset.copy]||{}).text||"";
  const done=()=>{const o=c.textContent;c.textContent="コピーしました";setTimeout(()=>c.textContent=o,1600)};
  if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(t).then(done);else{const ta=document.createElement("textarea");ta.value=t;document.body.appendChild(ta);ta.select();document.execCommand("copy");ta.remove();done()}});
document.addEventListener("click",e=>{const b=e.target.closest("[data-p],[data-g2]");if(b)openGuide(b.dataset.p||b.dataset.g2)});
document.addEventListener("keydown",e=>{const b=e.target.closest&&e.target.closest("[data-p],li[data-g2]");if(b&&(e.key==="Enter"||e.key===" ")){e.preventDefault();openGuide(b.dataset.p||b.dataset.g2)}});
const COLS={irregular:[["ブリーフ","曲の案"],["Suno で作曲","作曲（Suno）"],["仕上げ（音量・ジャケット）","仕上げ"],["DistroKid に登録","DistroKid 登録"],["配信待ち","配信待ち"],["配信","配信中"]],
  regular:[["ブリーフ","ブリーフ"],["Suno で作曲","作曲（Suno）"],["仕上げ（音量・ジャケット）","仕上げ"],["DistroKid に登録","DistroKid 登録"],["配信待ち","配信待ち"],["配信","配信中"]],
  debut:[["名前の確認","名前の確認"],["写真・ジャケット","写真・ジャケット"],["Suno で作曲","作曲（Suno）"],["仕上げ（音量・ジャケット）","仕上げ"],["DistroKid に登録","DistroKid 登録"],["配信待ち","配信待ち"],["デビュー","デビュー"]]};
function boardRows(key){const rows=DATA.timeline[key].rows,by={};const order=[];
  rows.forEach((r,ri)=>{if(!by[r.slug]){by[r.slug]=[];order.push(r.slug)}by[r.slug].push(ri)});
  const out=[];order.forEach(slug=>{const idx=by[slug];
    const inflight=idx.filter(ri=>!rows[ri].planned&&rows[ri].phases.some(p=>!p.milestone&&p.state!=="done"));
    if(inflight.length){inflight.forEach((ri,n)=>out.push([ri,n===0]));return}
    const next=idx.find(ri=>rows[ri].planned);out.push([next!==undefined?next:idx[idx.length-1],true])});return out}
function board(key){const el=document.getElementById("b-"+key),rows=DATA.timeline[key].rows;
  if(!rows.length){el.innerHTML=`<p class="empty" style="padding:12px">${EMPTY[key]}</p>`;return}
  const cols=COLS[key];
  let h=`<table><tr><th>アーティスト・曲</th>${cols.map(c=>`<th>${E(c[1])}</th>`).join("")}</tr>`;
  boardRows(key).forEach(([ri,first])=>{const r=rows[ri];
    const c=r.phases.find(p=>["ng","wait","run"].includes(p.state)&&!p.milestone);
    const now=c?`いま：${ICON[c.state]} ${E(c.name)}（${E(WHO(c.who)||STATE[c.state])}）`:(r.planned?"次の制作の予定":"✓ 進行中の工程なし");
    h+=`<tr><td class="an">${first?`<b>${E(r.artist)}</b>`:""}<small>${E(r.sub)}</small><small>${now}</small></td>`;
    cols.forEach(([name])=>{const pi=r.phases.findIndex(p=>p.name===name);
      if(pi<0){h+=`<td><span class="sub">—</span></td>`;return}
      const p=r.phases[pi],cur=["wait","ng","run"].includes(p.state)&&!p.milestone,d=cur?daysSince(p.start):0;
      let txt=p.state==="done"?"✓":p.state==="plan"?"○":`${ICON[p.state]} ${E(WHO(p.who)||STATE[p.state])}`;
      if(p.milestone&&p.state!=="done")txt=`○ ${md(D(p.start))}`;
      h+=`<td><button class="cell ${p.state}" data-g2="${key}:${ri}:${pi}" aria-label="${E(p.name)}：${E(LABEL[p.state])}">${txt}${d?`<span class="d">${d} 日</span>`:""}</button></td>`});
    h+="</tr>"});
  el.innerHTML=h+"</table>"}
function todoList(key){
  const rows=DATA.timeline[key].rows;const items=[];
  rows.forEach((r,ri)=>{if(r.planned)return;r.phases.forEach((p,pi)=>{if(["wait","ng","run"].includes(p.state)&&!p.milestone)items.push([r,p,`${key}:${ri}:${pi}`])})});
  let html=items.sort((a,b)=>({ng:0,wait:1,run:2}[a[1].state]-{ng:0,wait:1,run:2}[b[1].state])).map(([r,p,gid])=>`<li class="${p.state} click" data-g2="${gid}" tabindex="0"><span class="who">${E(WHO(p.who)||"—")}</span><b>${E(r.artist)}</b> ${E(r.sub)}　${ICON[p.state]} <b>${E(p.name)}</b>：${E(WHO((p.guide||{}).cause||p.detail))} <span class="sub">→ 押すと、やることとボタン</span></li>`).join("");
  html+=(DATA.photo_todos||[]).filter(t=>t.flow===key).map(t=>`<li class="wait"><span class="who">あなた</span><b>${E(t.artist)}</b>　● <b>アーティスト写真</b>：配信が始まったので、各サイトのアーティストのページに写真を入れる（${t.services.map(s=>E(s.name)+(s.state==="update"?"・入れ直し":"")).join("、")}）<br>`
    +t.services.map(s=>`<button class="act-only" data-act="fill_photo" data-slug="${E(t.slug)}" data-service="${E(s.service)}">${E(s.name)} に写真を入れる</button>`).join(" ")
    +` <a class="btn" href="/photos">アーティスト写真のページ ↗</a></li>`).join("");
  const lt=(DATA.lyric_todos||[]).filter(t=>t.flow===key);
  html+=lt.map(t=>`<li class="wait"><span class="who">あなた</span><b>${E(t.artist)}</b> 「${E(t.title)}」　● <b>歌詞</b>：DistroKid に登録したので、配信サイトの決まりに合わせた歌詞を出す（入力は自動・「Submit」は Gon さん）<br>`
    +`<button class="act-only" data-act="fill_lyrics" data-id="${E(t.id)}">歌詞を DistroKid に入れる</button></li>`).join("");
  if(lt.length>1)html+=`<li class="wait"><button class="act-only" data-act="fill_lyrics">歌詞がまだの ${lt.length} 曲を続けて入れる</button></li>`;
  if(key==="regular")html+=DATA.weekly_todos.map(t=>`<li class="${t.sev==="ng"?"ng":""}"><b>${E(t.what)}</b>（${t.who.length}）<br>${E(t.who.join("、"))}<br><code>${E(t.where)}</code></li>`).join("");
  document.getElementById("todo-"+key).innerHTML=html?`<ul class="list">${html}</ul>`:`<p class="empty">いまやることはありません</p>`;
}
function songsTable(key){const ss=DATA.songs.filter(s=>s.flow===key&&(key==="debut"?s.kind==="debut":s.kind==="weekly"));
  document.getElementById("songs-"+key).innerHTML=ss.length?`<div class="tbl"><table><tr><th>曲 ID</th><th>アーティスト</th><th>曲名</th><th>段階</th><th>配信日</th><th>ISRC</th><th class="act-only"></th></tr>${ss.map(s=>{
    let ops="";if(s.folder)ops+=`<button class="ghost" data-act="open_folder" data-id="${E(s.id)}">フォルダを開く</button> `;if(s.url)ops+=`<a href="${E(s.url)}" target="_blank" rel="noopener">Spotify</a>`;
    return `<tr><td>${E(s.id)}</td><td>${E(s.artist)}</td><td>${E(s.title||"（曲名未定）")}</td><td>${E(s.status_ja)}</td><td>${E(s.release_date||"—")}</td><td>${E(s.isrc||"—")}</td><td class="act-only">${ops}</td></tr>`}).join("")}</table></div>`:`<p class="empty">曲はまだありません</p>`}
async function act(action,params={},btn){if(STATIC)return;if(btn)btn.disabled=true;
  try{const r=await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json","X-Label-Token":TOKEN},body:JSON.stringify({action,...params})});
    const j=await r.json();if(!r.ok){alert(j.error||"実行できませんでした");return}if(j.job)watch(j.job)}finally{if(btn)setTimeout(()=>btn.disabled=false,1500)}}
const watching={};
// 実行中の仕事の進み具合（決定 65）：「2/5 曲目：…」と、その曲の中の「工程 3/7：…」
const PROG=j=>j.progress?`（${j.progress}${j.step?`・工程 ${j.step}`:""}）`:(j.step?`（工程 ${j.step}）`:"");
const PROGL=j=>(j.progress?`\n▶ ${j.progress}`:"")+(j.step?`\n　工程 ${j.step}`:"");   // ポップアップ用：1 行ずつ
async function watch(id){watching[id]=true;const box=document.getElementById("jobs");
  for(;;){const r=await fetch("/api/jobs/"+id);const j=await r.json();let el=document.getElementById("job-"+id);
    if(!el){el=document.createElement("details");el.id="job-"+id;el.open=true;box.prepend(el)}
    const st=({running:"実行中…",done:"終わりました ✓",incomplete:"⚠ まだ終わっていません",error:"✕ エラーがありました"}[j.state]||j.state)+(j.state==="running"?PROG(j):"");
    const sm=j.summary?`：${j.summary}`:"";
    el.className="job "+j.state;
    el.innerHTML=`<summary>${E(j.label)}　${E(st)}${E(sm)}（${E(j.started)}）</summary><pre>${E(j.out||"")}</pre>`;
    const dj=document.getElementById("dlg-job");if(dj&&document.getElementById("dlg").open){dj.className="job "+j.state;dj.textContent=`${j.label}　${j.state==="running"?"実行中…"+PROGL(j):st}${sm||(j.state==="error"?"（画面の下のログを確認）":"")}`}
    if(j.state!=="running"){load().then(()=>{if(/^曲の案を/.test(j.label)&&j.state==="done")openNewestDraft(j.label)});if(j.state==="done"&&String(j.label).startsWith("戻す")){const dl=document.getElementById("dlg");if(dl.open)dl.close()}break}await new Promise(r=>setTimeout(r,2000))}}
document.addEventListener("click",e=>{const b=e.target.closest("button[data-act]");if(!b)return;
  const p={};if(b.dataset.slug)p.slug=b.dataset.slug;if(b.dataset.id)p.id=b.dataset.id;
  if(b.dataset.act==="registered"){p.date=document.getElementById("d-"+b.dataset.id).value;if(!confirm(`DistroKid に登録した（配信日 ${p.date}）と記録しますか？`))return}
  if(b.dataset.act==="mark_live"){p.track=(document.getElementById("lt-"+b.dataset.id)||{}).value||"";p.artist=(document.getElementById("la-"+b.dataset.id)||{}).value||"";
    if(!confirm("配信が始まったと記録しますか？"+(p.artist?"":"\n（アーティストの住所が無いと、2 曲目からの登録は待ちのままです）")))return}
  if(b.dataset.act==="suno_retake"){p.why=[...b.closest(".rt").querySelectorAll("input:checked")].map(x=>x.value).join(",");if(!p.why){alert("良くなかったところを 1 つ以上選んでください");return}}
  if(b.dataset.act==="artist_page"){p.page=(document.getElementById("ap-"+b.dataset.slug)||{}).value||"";if(!p.page){alert("Spotify の URI か住所を貼ってください");return}}
  if(b.dataset.week)p.week=b.dataset.week;if(b.dataset.key)p.key=b.dataset.key;if(b.dataset.site)p.site=b.dataset.site;if(b.dataset.to)p.to=b.dataset.to;if(b.dataset.service)p.service=b.dataset.service;if(b.dataset.no)p.no=b.dataset.no;if(b.dataset.redo)p.redo=1;if(b.dataset.job)p.job=b.dataset.job;
  if(b.dataset.confirm&&!confirm(b.dataset.confirm))return;
  act(b.dataset.act,p,b)});
// ---- 不定期配信（決定 72）：組を選んで曲の案を作る → ポップアップで直す・却下して作り直す・作曲へ進める
function irrForm(){const el=document.getElementById("irr-form"),I=DATA.irregular||{},arts=I.artists||[];
  const sig=JSON.stringify([arts,I.scenes,I.collab]);if(el.dataset.sig===sig)return;   // 入力中の内容を消さないよう、組の一覧が変わったときだけ作り直す
  const keep={a:(document.getElementById("irr-artist")||{}).value,s:(document.getElementById("irr-scene")||{}).value,m:(document.getElementById("irr-memo")||{}).value,
    c:(document.getElementById("irr-collab-on")||{}).checked,cs:(document.getElementById("irr-collab")||{}).value};
  el.dataset.sig=sig;
  if(!arts.length){el.innerHTML=`<p class="empty">選べる組がまだありません（デビュー曲が配信中の組だけ選べます。配信を見つけたら、デビューのタブで「配信中にする」を押してください）</p>`;return}
  el.innerHTML=`<b>新しい曲の案を作る</b>
   <div class="irr-row"><div><label for="irr-artist">アーティスト</label><select id="irr-artist">${arts.map(a=>`<option value="${E(a.slug)}">${E(a.name)}</option>`).join("")}</select></div>
   <div><label for="irr-scene">聴かれる場面（任意）</label><select id="irr-scene"><option value="">おまかせ（組の個性から決める）</option>${(I.scenes||[]).map(x=>`<option value="${E(x.key)}">${E(x.name)}</option>`).join("")}</select></div></div>
   <label class="chk"><input type="checkbox" id="irr-collab-on"> コラボする（相性のよい組が客演。相手の強みを 3〜5 割、曲の構成を崩さずに取り込む）</label>
   <div id="irr-collab-box" hidden><label for="irr-collab">コラボの相手（相性の高い順）</label><select id="irr-collab"></select><p class="sub" id="irr-collab-why"></p></div>
   <label for="irr-memo">作りたい曲のメモ（任意。例：雨の夜に帰り道を歩く・別れたあとの静かな朝）</label><textarea id="irr-memo" maxlength="1000" placeholder="空のままなら、システムが組の個性と近況から決めます"></textarea>
   <div class="acts"><button id="irr-go">曲の案を作る（Claude が書きます・1〜2 分）</button><span class="sub">できたらポップアップが開きます</span></div>`;
  if(keep.a&&arts.some(a=>a.slug===keep.a))document.getElementById("irr-artist").value=keep.a;if(keep.s)document.getElementById("irr-scene").value=keep.s;if(keep.m)document.getElementById("irr-memo").value=keep.m;
  const cOn=document.getElementById("irr-collab-on"),cBox=document.getElementById("irr-collab-box"),cSel=document.getElementById("irr-collab"),cWhy=document.getElementById("irr-collab-why"),aSel=document.getElementById("irr-artist");
  const fillCollab=()=>{const ps=((I.collab||{})[aSel.value]||[]);   // コラボの相手（決定 97）
    cSel.innerHTML=ps.length?ps.map(p=>`<option value="${E(p.slug)}" ${p.ok?"":"disabled"}>${E(p.name)}　相性 ${"★".repeat(p.score||0)}${"☆".repeat(5-(p.score||0))}${p.ok?"":"（デビュー前）"}</option>`).join(""):`<option value="">（相性をまだ吟味していません。管理表を読み込むと吟味します）</option>`;
    const first=ps.find(p=>p.ok);if(first)cSel.value=first.slug;showWhy()};
  const showWhy=()=>{const p=((I.collab||{})[aSel.value]||[]).find(x=>x.slug===cSel.value);cWhy.textContent=p?`${p.why}${p.brings?"　取り込む強み："+p.brings:""}`:""};
  cOn.onchange=()=>{cBox.hidden=!cOn.checked;if(cOn.checked)fillCollab()};aSel.addEventListener("change",()=>{if(cOn.checked)fillCollab()});cSel.onchange=showWhy;
  if(keep.c){cOn.checked=true;cBox.hidden=false;fillCollab();if(keep.cs&&[...cSel.options].some(o=>o.value===keep.cs&&!o.disabled)){cSel.value=keep.cs;showWhy()}}
  document.getElementById("irr-go").onclick=e=>act("irregular_propose",{slug:aSel.value,scene:document.getElementById("irr-scene").value,memo:document.getElementById("irr-memo").value,collab:cOn.checked?cSel.value:""},e.target)}
function irrDrafts(){const ds=(DATA.irregular||{}).drafts||[],el=document.getElementById("irr-drafts");
  if(!ds.length){el.innerHTML=`<p class="empty">いま確認を待っている案はありません</p>`;return}
  el.innerHTML=`<ul class="list">${ds.map(d=>{const head=`<b>${E(d.artist)}</b>　${d.round>1?`${d.round} 案目・`:""}${E(d.scene||"場面おまかせ")}${d.memo?`・メモ「${E(d.memo.slice(0,40))}」`:""}　<span class="sub">${E(d.created_at)}</span><br>`;
    if(d.status==="making")return `<li class="run"><span class="who">自動</span>${head}… 案を作っています（Claude が書いています）</li>`;
    if(d.status==="failed")return `<li class="ng"><span class="who">あなた</span>${head}！ 作れませんでした：${E(d.error)}<div class="acts act-only">${/ANTHROPIC/.test(d.error||"")?`<a class="btn" href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">Claude の鍵のページを開く ↗</a><button data-act="terminal" data-key="anthropic">鍵を入れる（ターミナルが開きます）</button>`:""}<button data-irr="redo" data-id="${E(d.id)}" data-slug="${E(d.slug)}">もう一度作る</button><button class="ghost" data-irr="discard" data-id="${E(d.id)}">この案をやめる</button></div></li>`;
    return `<li class="wait click" data-draft="${E(d.id)}" tabindex="0"><span class="who">あなた</span>${head}● 確認待ち：「<b>${E(d.title)}</b>」${d.theme?`（${E(d.theme)}）`:""} <span class="sub">→ 押すと、中身を見て直せます</span></li>`}).join("")}</ul>`}
function openNewestDraft(label){const d=((DATA.irregular||{}).drafts||[]).find(x=>x.status==="proposed"&&String(label).includes(x.artist));if(d)openDraft(d.id)}
function openDraft(id){const d=((DATA.irregular||{}).drafts||[]).find(x=>x.id===id);if(!d)return;const L=(DATA.irregular||{}).limits||{};
  const f=(k,lab,v,big)=>`<label for="irr-f-${k}">${E(lab)}</label>${big?`<textarea id="irr-f-${k}" class="${k==="lyrics"?"lyr":""}" maxlength="${L[k]||5000}">${E(v)}</textarea>`:`<input type="text" id="irr-f-${k}" maxlength="${L[k]||300}" value="${E(v)}">`}`;
  const hist=(d.history||[]).length?`<details><summary>却下した案（${d.history.length}）と理由</summary><ul>${d.history.map(h=>`<li>「${E(h.title)}」${h.theme?`（${E(h.theme)}）`:""}${h.reason?` → 理由：${E(h.reason)}`:"（理由なし）"}</li>`).join("")}</ul></details>`:"";
  document.getElementById("dlg-body").innerHTML=`<p class="sub">${E(d.artist)}・不定期配信の曲の案${d.round>1?`（${d.round} 案目）`:""}・${E(d.scene||"場面おまかせ")}・${E(d.created_at)}${d.collab?`・コラボ：${E(d.collab)}（相性 ${"★".repeat(d.collab_score||0)}）`:""}</p>
   <h3 id="dlg-title">「${E(d.title)}」</h3><span class="badge wait">確認待ち・Gon さん</span>${d.edited_at?` <span class="sub">直した日時 ${E(d.edited_at)}</span>`:""}
   <div id="dlg-job"></div>
   ${d.memo?`<p class="sub">メモ：${E(d.memo)}</p>`:""}${hist}
   <div class="irr-f">
    ${f("title","曲名",d.title)}${(d.candidates||[]).length>1?`<div class="chips act-only">${d.candidates.map(c=>`<button class="ghost" data-pick="${E(c)}">${E(c)}</button>`).join("")}</div>`:""}
    ${f("theme","曲のテーマ",d.theme)}
    ${f("story","曲のイメージ（エピソード：いつ・どこで・何を見て・感じたか）",d.story,true)}
    ${d.when_where||d.realized?`<p class="sub">${d.when_where?`いつ・どこで：${E(d.when_where)}　`:""}${d.realized?`たどり着いた思い（サビの芯）：${E(d.realized)}`:""}</p>`:""}
    ${f("lyrics",d.instrumental?"歌詞（インストの組なので、声の断片だけ）":"歌詞（Suno に入れる。[Verse] などの印つき）",d.lyrics,true)}
    ${f("style","Suno のスタイル（英語。ジャンル・テンポ・楽器・声の質感）",d.style,true)}
    ${f("exclude","入れない要素（英語・任意）",d.exclude)}
    ${f("cover","ジャケットのイメージ（英語。色・光・構図）",d.cover,true)}
    ${d.notes?`<label>この曲の狙い（Claude のメモ）</label><div class="irr-note">${E(d.notes)}</div>`:""}
   </div>
   <div class="acts act-only"><button data-irr="accept" data-id="${E(d.id)}">✓ この案で作曲へ進む（Suno に自動で入力）</button><button class="ghost" data-irr="save" data-id="${E(d.id)}">直したところを保存する</button></div>
   <div class="redo act-only"><b>✕ 好みでないので却下して作り直す</b><span class="sub">　この案は残し、次の案では同じ方向をくり返しません</span>
    <label for="irr-reason" class="sub" style="display:block;margin-top:6px">どこが好みでなかったか（任意。例：もっと明るく・歌詞が説明的すぎる・テンポを遅く）</label>
    <textarea id="irr-reason" maxlength="1000" style="width:100%;min-height:3em;font:inherit;font-size:14px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)"></textarea>
    <div class="acts"><button data-irr="redo" data-id="${E(d.id)}" data-slug="${E(d.slug)}">却下して作り直す</button><button class="ghost" data-irr="discard" data-id="${E(d.id)}">作り直さずにやめる</button></div></div>`;
  const dlg=document.getElementById("dlg");if(!dlg.open)dlg.showModal()}
function irrFields(){const out={};for(const k of Object.keys((DATA.irregular||{}).editable||{})){const e=document.getElementById("irr-f-"+k);if(e)out[k]=e.value}return out}
async function irrPost(op,id,fields,btn){if(STATIC)return;if(btn)btn.disabled=true;const dj=document.getElementById("dlg-job");
  try{const r=await fetch("/api/irregular",{method:"POST",headers:{"Content-Type":"application/json","X-Label-Token":TOKEN},body:JSON.stringify({op,id,fields})});
    const j=await r.json();if(!r.ok){alert(j.error||"できませんでした");return}
    if(dj){dj.className="job done";dj.textContent=j.message||"済みました"}if(j.job)watch(j.job);load()}finally{if(btn)setTimeout(()=>btn.disabled=false,1500)}}
document.addEventListener("click",e=>{const pk=e.target.closest("[data-pick]");if(pk){const t=document.getElementById("irr-f-title");if(t)t.value=pk.dataset.pick;return}
  const li=e.target.closest("li[data-draft]");if(li&&!e.target.closest("button")){openDraft(li.dataset.draft);return}
  const b=e.target.closest("button[data-irr]");if(!b)return;const op=b.dataset.irr,id=b.dataset.id;
  if(op==="save")irrPost("save",id,irrFields(),b);
  if(op==="accept"){if(confirm("この案で作曲へ進みますか？\n直したところも保存して、Suno に自動で入力します（専用のブラウザが開きます。Create は Gon さんが押します）"))irrPost("accept",id,irrFields(),b)}
  if(op==="discard"){if(confirm("この案をやめますか？（記録は残ります）")){irrPost("discard",id,{},b);const dl=document.getElementById("dlg");if(dl.open)dl.close()}}
  if(op==="redo"){const reason=(document.getElementById("irr-reason")||{}).value||"";if(!confirm("この案を却下して、新しい案を作りますか？"+(reason?"":"\n（理由は空のままでも作り直せます）")))return;
    act("irregular_propose",{slug:b.dataset.slug,redo:id,reason},b);const dj=document.getElementById("dlg-job");if(dj){dj.className="job running";dj.textContent="新しい案を作っています…（できたら、このポップアップが新しい案に変わります）"}}});
document.addEventListener("keydown",e=>{const li=e.target.closest&&e.target.closest("li[data-draft]");if(li&&(e.key==="Enter"||e.key===" ")){e.preventDefault();openDraft(li.dataset.draft)}});
function render(){
  document.getElementById("upd").textContent=DATA.generated+" 時点";
  for(const k of TABS){const rs=DATA.timeline[k].rows;const nArt=new Set(rs.map(r=>r.slug)).size;
    const nd=k==="irregular"?((DATA.irregular||{}).drafts||[]).filter(d=>d.status==="proposed").length:0;
    document.getElementById("n-"+k).textContent=k==="debut"?`${nArt} 組`:k==="irregular"?`${rs.length} 曲${nd?`・案 ${nd}`:""}`:`${nArt} 組・${rs.filter(r=>!r.planned).length} 曲`;board(k);gantt(k);todoList(k);songsTable(k)}
  irrForm();irrDrafts();dailyBox();
  {const re0=DATA.ref_enrich||{},bl=document.getElementById("ref-lyrics"),bw=document.getElementById("ref-whisper");   // 歌詞の聞き取り（決定 86）
   bl.hidden=!(re0.lyrics&&re0.whisper);bw.hidden=!(re0.lyrics&&!re0.whisper);
   if(re0.lyrics){bl.textContent=`歌詞を聞き取る（${re0.lyrics} 曲・無料）`;bw.textContent=`歌詞を聞き取る部品を入れる（${re0.lyrics} 曲が待っています・ターミナルが開きます）`;
    bl.dataset.confirm=`既存楽曲 ${re0.lyrics} 曲のうち ${Math.min(re0.lyrics,30)} 曲の歌詞を、Mac の中で聞き取ります（1 曲 1〜3 分・料金はかかりません）。始めますか？`}}
  {const re1=DATA.ref_enrich||{},bm=document.getElementById("ref-melody"),bd=document.getElementById("ref-demucs");   // メロディー（決定 90）
   bm.hidden=!re1.melody;bd.hidden=!re1.melody||!!re1.demucs;
   if(re1.melody){bm.textContent=`メロディーを起こす（${re1.melody} 曲・無料）`;
    bm.dataset.confirm=`既存楽曲 ${re1.melody} 曲のうち ${Math.min(re1.melody,15)} 曲のメロディーを、Mac の中で起こして MIDI（GarageBand で開けます）に残します（1 曲 2〜5 分・料金はかかりません）。始めますか？`}}
  {const re2=DATA.ref_enrich||{},n=re2.infl||{},b=document.getElementById("ref-infl-an"),m=(n.lyrics||0)+(n.melody||0);   // 影響を受けたアーティスト（決定 91）
   if(re2.infl_picked)document.getElementById("ref-infl").textContent=`影響を受けたアーティストを選び直す（SSDDB から・今 ${re2.infl_picked} 組）`;
   b.hidden=!m;if(m){b.textContent=`影響を受けたアーティストの曲を解析する（歌詞 ${n.lyrics||0}・メロディー ${n.melody||0} 曲・無料）`;
    b.dataset.confirm=`影響を受けたアーティストの曲の歌詞（${n.lyrics||0} 曲）とメロディー（${n.melody||0} 曲）を、Mac の中で先に起こします（1 曲 2〜5 分・料金はかかりません）。始めますか？`}}
  {const b=document.getElementById("ref-enrich"),re=DATA.ref_enrich||{pending:0,yen:[0,0]};b.hidden=!re.pending;   // 参考曲の足りない項目（決定 85）
   if(re.pending){const n=Math.min(re.pending,50),lo=Math.round(re.yen[0]*n/re.pending),hi=Math.round(re.yen[1]*n/re.pending);
    b.textContent=`足りない項目を Claude に調べてもらう（${re.pending} 曲）`;
    b.dataset.confirm=`既存楽曲 ${re.pending} 曲のうち ${n} 曲の、声・楽器・歌詞のテーマ・世界観を、Claude に Web で調べてもらいます。\n料金の目安：${lo.toLocaleString()}〜${hi.toLocaleString()} 円（1 曲 20〜50 円）。\n始めますか？`}}
  document.getElementById("system").innerHTML=DATA.system.length?`<ul class="list">${DATA.system.map(x=>`<li class="${x.sev}"><b>${E(x.what)}</b><br><code>${E(x.how)}</code>${(x.acts||[]).map((a,i)=>" "+actHTML(a,i,{})).join("")}</li>`).join("")}</ul>`:`<p class="empty">仕組みの詰まりはありません</p>`;
  const t=DATA.tracker,cols=["順番","詰まっていること","担当","次の一歩","状態"];
  document.getElementById("tracker").innerHTML=t.rows.length?`<div class="tbl"><table><tr>${cols.map(c=>`<th>${c}</th>`).join("")}</tr>${t.rows.map(r=>`<tr>${cols.map(c=>`<td>${E(r[c])}</td>`).join("")}</tr>`).join("")}</table></div><p class="sub">読んだ場所：${E(t.src)}</p>`:`<p class="empty">完了していない項目はありません（${E(t.src)}）</p>`;
  if(!STATIC)DATA.jobs.filter(j=>j.state==="running"&&!watching[j.id]).forEach(j=>watch(j.id));
}
let opened=false;
function deepLink(){if(opened)return;opened=true;const m=location.hash.match(/open=([a-z]+:\d+:\d+)/);if(m)try{openGuide(m[1])}catch(e){}}
async function load(){if(STATIC){DATA=STATIC;render();deepLink();return}const r=await fetch("/api/status");DATA=await r.json();
  render();deepLink()}
let rz;addEventListener("resize",()=>{clearTimeout(rz);rz=setTimeout(()=>DATA&&render(),200)});
load();if(!STATIC)setInterval(load,30000);
</script></body></html>"""


def render_page(static_data: dict | None = None) -> str:
    """画面の HTML。static_data を渡すと、データを埋め込んだ読むだけの画面（ボタンなし。dashboard.py の書き出し用）"""
    data = json.dumps(static_data, ensure_ascii=False, default=str).replace("</", "<\\/") if static_data else "null"
    return PAGE.replace("__TOKEN__", "" if static_data else TOKEN).replace("__STATIC__", data)


class Handler(BaseHTTPRequestHandler):
    server_version = "LabelDashboard"
    lan_key: str | None = None

    def log_message(self, fmt, *args):   # アクセスの記録は出さない
        pass

    def _allowed(self) -> bool:
        if not self.lan_key:
            return True
        q = parse_qs(urlparse(self.path).query)
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        ok = secrets.compare_digest((q.get("key") or [""])[0], self.lan_key) or \
            ("label_key" in c and secrets.compare_digest(c["label_key"].value, self.lan_key))
        return ok

    def _send(self, code: int, body: str | bytes, ctype: str = "text/html; charset=utf-8", extra: dict | None = None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._allowed():
            return self._send(403, "<p>合鍵つきの URL で開いてください（webapp.py --lan を起動したときに表示されます）</p>")
        path = urlparse(self.path).path
        extra = {}
        if self.lan_key:
            extra["Set-Cookie"] = f"label_key={self.lan_key}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
        if path == "/":
            return self._send(200, render_page(), extra=extra)
        if path == "/suno":
            from publish import suno_page
            page, _ = suno_page()
            back = '<p style="max-width:860px;margin:16px auto 0;padding:0 16px"><a href="/">← ダッシュボードへ</a></p>'
            return self._send(200, page.replace("<main>", back + "<main>", 1), extra=extra)
        if path == "/photos":
            import artist_photo
            return self._send(200, artist_photo.photos_page(TOKEN), extra=extra)
        if path == "/phone":
            import phone
            return self._send(200, phone.phone_index(TOKEN), extra=extra)
        m = re.fullmatch(r"/phone/([\w.\-]+)", path)
        if m:
            import phone
            page = phone.phone_page(m.group(1), TOKEN)
            return self._send(200, page, extra=extra) if page else self._send(404, "<p>その曲は見つかりません</p>")
        if path == "/check":
            import song_check
            return self._send(200, song_check.index_page(TOKEN), extra=extra)
        m = re.fullmatch(r"/song/([\w.\-]+)", path)
        if m:
            import song_check
            page = song_check.song_page(m.group(1), TOKEN)
            return self._send(200, page, extra=extra) if page else self._send(404, "<p>その曲は見つかりません</p>")
        if path == "/file":   # 公開の準備の確認ページで見せる画像・音（決めた置き場所の中だけ）
            import song_check
            f = Path((parse_qs(urlparse(self.path).query).get("p") or [""])[0])
            if not song_check.file_ok(f):
                return self._send(404, "<p>見つかりません</p>")
            import mimetypes
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if (parse_qs(urlparse(self.path).query).get("dl") or [""])[0] == "1":   # スマホに保存する（DistroKid で選ぶため）
                extra["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f.name)}"
            return self._send(200, f.read_bytes(), ctype, extra=extra)
        if path == "/api/status":
            try:
                return self._json(200, status_json())
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": f"状態を読めませんでした：{e}"})
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{8})", path)
        if m and m.group(1) in JOBS:
            return self._json(200, JOBS[m.group(1)])
        return self._send(404, "<p>見つかりません</p>")

    def do_POST(self):
        if not self._allowed() or not secrets.compare_digest(self.headers.get("X-Label-Token", ""), TOKEN):
            return self._json(403, {"error": "このページを開き直してから押してください"})
        if urlparse(self.path).path == "/api/upload":
            return self._upload()
        if urlparse(self.path).path == "/api/irregular":
            return self._irregular()
        if urlparse(self.path).path != "/api/action":
            return self._json(404, {"error": "見つかりません"})
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 10000)
            p = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "内容が読めません"})
        found = command_for(str(p.get("action", "")), p)
        if not found:
            return self._json(400, {"error": "その操作はできません"})
        if not found[1].startswith("ブラウザで入力") and any(j["state"] == "running" and j["label"] == found[1] for j in JOBS.values()):
            return self._json(409, {"error": "同じ仕事がまだ動いています"})
        if found[1].startswith("ブラウザで入力"):   # 前のブラウザの入力が残っていれば止めてから始める（入り直せるように）
            for j in list(JOBS.values()):
                if j["state"] == "running" and j["label"].startswith("ブラウザで入力"):
                    stop_job(j["id"])
        return self._json(200, {"job": start_job(*found)})


UPLOAD_MAX = 400 * 1024 * 1024     # 1 曲の WAV でも足りる大きさ
UPLOAD_EXT = {".wav", ".mp3", ".m4a", ".flac", ".aiff", ".aif"}


def save_upload(song: dict, name: str, stream, size: int) -> Path:
    """スマホから送られた曲を、Mac の「楽曲情報」（publish.py が見張る最初のフォルダ）に「曲名.拡張子」で置く。
    曲名にしておくと、取り込み（publish.py scan）がどの曲か見分けられる"""
    import publish
    ext = Path(name).suffix.lower()
    inbox = publish.inboxes()[0]
    inbox.mkdir(parents=True, exist_ok=True)
    stem = _songs.safe(song["title"])
    dst, n = inbox / f"{stem}{ext}", 2
    while dst.exists():
        dst, n = inbox / f"{stem} ({n}){ext}", n + 1
    part = dst.with_name(f".{dst.name}.part")   # 送り終わるまでは隠しておく（取り込みが途中のファイルを読まない）
    left = size
    with open(part, "wb") as out:
        while left > 0:
            chunk = stream.read(min(1 << 20, left))
            if not chunk:
                break
            out.write(chunk)
            left -= len(chunk)
    if left > 0:
        part.unlink(missing_ok=True)
        raise ValueError("途中で切れました")
    part.rename(dst)
    return dst


def _upload(self):
    """POST /api/upload?id=<曲 ID>：スマホから曲を受け取り、取り込みを始める"""
    song = _songs.by_id((parse_qs(urlparse(self.path).query).get("id") or [""])[0])
    name = unquote(self.headers.get("X-Filename", ""))
    try:
        size = int(self.headers.get("Content-Length", "0"))
    except ValueError:
        size = 0
    if song is None:
        return self._json(404, {"error": "その曲は見つかりません"})
    if Path(name).suffix.lower() not in UPLOAD_EXT:
        return self._json(400, {"error": "曲のファイル（WAV・MP3・M4A・FLAC・AIFF）を選んでください"})
    if not 0 < size <= UPLOAD_MAX:
        return self._json(400, {"error": "ファイルが空か、大きすぎます（400 MB まで）"})
    try:
        dst = save_upload(song, name, self.rfile, size)
    except (OSError, ValueError) as e:
        return self._json(500, {"error": f"Mac に保存できませんでした（{e}）"})
    return self._json(200, {"job": start_job(*command_for("scan", {})), "saved": dst.name})


Handler._upload = _upload


def _irregular(self):
    """POST /api/irregular：不定期配信の曲の案を保存する・作曲へ進める・やめる（決定 72）。
    作曲へ進めたら、続けて Suno への入力（専用のブラウザ）を始める"""
    import irregular
    try:
        n = min(int(self.headers.get("Content-Length", "0")), 200000)   # 歌詞が入るので、ふつうのボタンより大きく受ける
        p = json.loads(self.rfile.read(n) or b"{}")
    except (ValueError, json.JSONDecodeError):
        return self._json(400, {"error": "内容が読めません"})
    op, did = str(p.get("op") or ""), str(p.get("id") or "")
    if op == "save":
        ok, msg = irregular.save(did, p.get("fields") or {})
        return self._json(200 if ok else 400, {"message": msg} if ok else {"error": msg})
    if op == "discard":
        ok, msg = irregular.discard(did)
        return self._json(200 if ok else 400, {"message": msg} if ok else {"error": msg})
    if op == "accept":
        if p.get("fields"):   # 直した欄があれば、先に保存する
            ok, msg = irregular.save(did, p["fields"])
            if not ok:
                return self._json(400, {"error": msg})
        song_id, msg = irregular.accept(did)
        if not song_id:
            return self._json(400, {"error": msg})
        found = command_for("fill_suno", {"id": song_id})
        if not found:
            return self._json(200, {"message": msg + "。Suno への入力は、表のマスから始めてください", "song_id": song_id})
        for j in list(JOBS.values()):   # 前のブラウザの入力が残っていれば止めてから始める
            if j["state"] == "running" and j["label"].startswith("ブラウザで入力"):
                stop_job(j["id"])
        return self._json(200, {"message": msg + "。Suno に入力します（専用のブラウザが開きます）", "song_id": song_id, "job": start_job(*found)})
    return self._json(400, {"error": "その操作はできません"})


Handler._irregular = _irregular


def tailscale_info() -> tuple[str | None, str | None]:
    """Tailscale（Mac とスマホだけの専用の通り道）の、この Mac の住所（100.x.x.x）と名前（xxx.ts.net）。入っていなければ (None, None)"""
    for exe in ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale"):
        try:
            r = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode != 0:
            continue
        try:
            me = json.loads(r.stdout).get("Self") or {}
        except ValueError:
            continue
        ips = [x for x in me.get("TailscaleIPs") or [] if "." in x]
        return (ips[0] if ips else None), (str(me.get("DNSName") or "").rstrip(".") or None)
    return None, None


def keep_awake() -> None:
    """ダッシュボードが動いている間、Mac が勝手に眠らないようにする（画面は消えてよい。macOS の caffeinate）"""
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
            print("   Mac が勝手に眠らないようにしました（ダッシュボードを止めると元に戻ります。ふたを閉じると眠ります）")
        except OSError:
            pass


def lan_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "（この Mac の IP アドレス）"


def lan_key() -> str:
    key = os.environ.get("WEBAPP_KEY", "").strip()
    if key:
        return key
    key = secrets.token_urlsafe(18)
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    env.write_text("\n".join([*lines, f"WEBAPP_KEY={key}"]).rstrip("\n") + "\n", encoding="utf-8")
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description="ダッシュボードのウェブアプリ（Mac の中で動く）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("WEBAPP_PORT", "8765")))
    ap.add_argument("--lan", action="store_true", default=os.environ.get("WEBAPP_LAN", "") == "1",
                    help="家の Wi-Fi・Tailscale のほかの機器（スマホなど）からも見られるようにする（合鍵つき。.env の WEBAPP_LAN=1 でも）")
    ap.add_argument("--awake", action="store_true", default=os.environ.get("WEBAPP_AWAKE", "") == "1",
                    help="動いている間、Mac が勝手に眠らないようにする（外出先から使うとき。.env の WEBAPP_AWAKE=1 でも）")
    a = ap.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    if a.lan:
        Handler.lan_key = lan_key()
    srv = ThreadingHTTPServer((host, a.port), Handler)
    print(f"▶ ダッシュボード：http://localhost:{a.port}")
    if a.lan:
        print(f"   スマホなど（同じ Wi-Fi）：http://{lan_address()}:{a.port}/?key={Handler.lan_key}")
        print("   （この URL は合鍵つき。ほかの人に教えない。1 回開けば、その機器では次から合鍵なしで開けます）")
    ts_ip, ts_name = tailscale_info()
    if ts_name:
        print(f"   外出先のスマホ（Tailscale）：https://{ts_name}/phone　← Mac で一度だけ  tailscale serve --bg {a.port}  を実行しておく")
        if a.lan and ts_ip:
            print(f"   （tailscale serve を使わないとき：http://{ts_ip}:{a.port}/phone?key={Handler.lan_key}）")
    if a.awake:
        keep_awake()
    print("   止めるときは control + C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
配信が始まった曲の再生数を自動で集めて、ダッシュボードの「再生数」のページ（/plays）に出す（決定 172）

  集め方は 3 つ。どれも単独で動き、鍵やログインが無いものは飛ばして「何をすれば集まるか」を出す。
    1 公式の窓口（API）… 毎日・全自動
        ・Spotify：曲と組の「人気度」（0〜100 の目安。再生数ではない）・フォロワー数（配信の確認と同じ SPOTIFY の鍵）
        ・YouTube：YouTube Music で聴かれた回数（＝アーティストの Topic チャンネルの動画の視聴回数）
                    YOUTUBE_API_KEY か、SNS 投稿用の YouTube の許可（YOUTUBE_REFRESH_TOKEN）があるとき
    2 Spotify for Artists（S4A）… Gon さんのログイン済みの専用の Chrome で「楽曲」の表を読む
        ・再生数・リスナー数・保存数（Spotify には、再生数を返す公式の窓口が無いため）
        ・ページを開いて読むだけ。ボタンは押さない。ログインしていなければ「ログインが要ります」と出す
    3 Gon さんがダウンロードしたファイル … ダウンロードのフォルダを見て自動で取り込む
        ・DistroKid の明細（Bank → See excruciating detail → Download）… 全ストアの再生数（2〜3 か月遅れ）
        ・Spotify for Artists の「楽曲」の CSV
        取り込んだファイルは out/plays/imported/ に移す（成長分析の記録 out/metrics/daily.csv にも入れる）

  記録：out/plays/snapshots.json … 1 日 1 つ（同じ日は上書き）。前日・7 日前・28 日前との差を出す。
        out/plays/diag/<日付>_<組>.txt … S4A の画面の並びの記録（数字・曲名・見出しだけ。ほかの文字は長さだけにして残す）

使い方
  python3 scripts/plays.py collect                      # 3 つとも集める（ダッシュボードの「いま集める」）
  python3 scripts/plays.py collect --layers api,files   # 選んで集める
  python3 scripts/plays.py collect --quiet              # 定期実行（毎日）。ほかの仕事の邪魔をしない
  python3 scripts/plays.py status                       # いまの数字と、足りないもの
  python3 scripts/plays.py parse-test <ファイル>         # S4A の画面の文字を読めるか試す（調整用）

鍵（.env。setup_keys.py で入れる。チャットには貼らない）
  SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET … 人気度・フォロワー数
  YOUTUBE_API_KEY（任意）… YouTube Music の再生数。SNS 投稿用の YouTube の許可があれば無くてもよい
  PLAYS_S4A_AUTO=0 … 定期実行のとき、専用の Chrome が開いていなければ S4A を読みに行かない（既定は開いて読んで閉じる）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, all_artists, load_artist, load_dotenv, read_json, ssl_context, step, write_json  # noqa: E402

load_dotenv()
import _songs  # noqa: E402

DIR = OUT / "plays"
KEEP_DAYS = 400
LAYERS = ("api", "s4a", "files")
S4A_BASE = "https://artists.spotify.com"
SONG_FIELDS = ("spotify_streams", "spotify_listeners", "spotify_saves", "spotify_popularity", "youtube_views")
PERIOD_JA = {"all": "累計", "12m": "直近 12 か月", "28d": "直近 28 日", "7d": "直近 7 日", "1d": "直近 24 時間",
             "file": "書き出した期間", "unknown": "期間不明"}


def _api(name: str, default: str) -> str:
    """窓口の住所（試すときだけ、手元の偽サーバーに向ける）"""
    return os.environ.get(name, "").strip().rstrip("/") or default


# ---------------------------------------------------------------------------
# 記録（out/plays/snapshots.json）
def snap_path() -> Path:
    return DIR / "snapshots.json"


def load() -> dict:
    p = snap_path()
    d = read_json(p) if p.exists() else {}
    d.setdefault("days", {})
    d.setdefault("cache", {})
    d.setdefault("last", {})
    return d


def save(d: dict) -> None:
    for old in sorted(d["days"])[:-KEEP_DAYS]:
        d["days"].pop(old, None)
    DIR.mkdir(parents=True, exist_ok=True)
    write_json(snap_path(), d)


def put(d: dict, day: str, kind: str, key: str, values: dict, source: str) -> None:
    """その日の記録に足す（同じ日・同じ項目は新しい値で上書き。値が None の項目は入れない）"""
    rec = d["days"].setdefault(day, {"songs": {}, "artists": {}}).setdefault(kind, {}).setdefault(key, {})
    rec.update({k: v for k, v in values.items() if v is not None})
    src = rec.setdefault("source", [])
    if source not in src:
        src.append(source)
    d["days"][day]["at"] = datetime.now().isoformat(timespec="seconds")


def latest(d: dict, kind: str, key: str, field: str, before: str | None = None):
    """いちばん新しい値と、その日（before を渡すと、その日以前でいちばん新しいもの）"""
    for day in sorted(d["days"], reverse=True):
        if before and day > before:
            continue
        v = ((d["days"][day].get(kind) or {}).get(key) or {}).get(field)
        if v is not None:
            return v, day
    return None, None


def _cumulative(d: dict, sid: str, day: str | None = None) -> bool:
    """Spotify の再生数が「累計」で記録されているか（期間つきの数字は、差を出しても意味が無い）"""
    per, _ = latest(d, "songs", sid, "spotify_period", day)
    return per == "all"


def delta(d: dict, kind: str, key: str, field: str, days: int):
    """いちばん新しい値 −（その days 日前までで、いちばん新しい値）。比べる相手が無ければ None"""
    now, day = latest(d, kind, key, field)
    if now is None:
        return None
    if field == "spotify_streams" and not _cumulative(d, key):
        return None
    ref = (date.fromisoformat(day) - timedelta(days=days)).isoformat()
    old, old_day = latest(d, kind, key, field, ref)
    if old is None or (field == "spotify_streams" and not _cumulative(d, key, old_day)):
        return None
    return now - old


def total_plays(d: dict, sid: str, before: str | None = None):
    """その曲の再生数の合計（Spotify の累計 ＋ YouTube Music）。どちらも無ければ None"""
    sp, sp_day = latest(d, "songs", sid, "spotify_streams", before)
    if sp is not None and not _cumulative(d, sid, sp_day):
        sp = None
    yt, _ = latest(d, "songs", sid, "youtube_views", before)
    return None if sp is None and yt is None else (sp or 0) + (yt or 0)


def series(d: dict, sid: str, points: int = 28) -> list[int]:
    out = []
    for day in sorted(d["days"])[-points:]:
        v = total_plays(d, sid, day)
        if v is not None:
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# 自分たちの曲と組
def _track_id(st: dict) -> str:
    m = re.search(r"track[:/]([A-Za-z0-9]{22})", str(st.get("url") or ""))
    return str(st.get("spotify_track_id") or (m.group(1) if m else ""))


def released_songs() -> list[dict]:
    """配信が始まった曲（配信中の記録がある・Spotify の住所がある・登録して配信日を過ぎた）"""
    today, out = date.today().isoformat(), []
    for s in _songs.all_songs():
        if str(s["week"]).startswith("2000-"):
            continue
        st = _songs.state(s)
        rel = str(st.get("released") or st.get("release_date") or "")[:10]
        if not (st.get("status") == "live" or st.get("url") or (st.get("status") == "registered" and rel and rel <= today)):
            continue
        out.append({"id": s["id"], "key": s["key"], "slug": s["slug"], "artist": s["artist"], "title": s["title"],
                    "release_date": rel, "url": st.get("url", ""), "track_id": _track_id(st), "isrc": str(st.get("isrc") or "").upper()})
    return out


def artists(slugs: set[str]) -> dict[str, dict]:
    """組ごとの、Spotify のアーティストの ID と YouTube Music のチャンネル"""
    links_p = OUT / "knowledge" / "artist_links.json"
    links = read_json(links_p) if links_p.exists() else {}
    out = {}
    known = {a.get("slug"): a for a in all_artists(include_retiring=True)}
    for slug in slugs:
        a = known.get(slug) or load_artist(slug, missing_ok=True) or {}
        dist = a.get("distribution") or {}
        pages = dist.get("artist_pages") or {}
        sp = ((links.get(slug) or {}).get("spotify") or {}).get("id") or ""
        if not sp:
            m = re.search(r"(?:artist[:/])([A-Za-z0-9]{22})", f"{pages.get('spotify', '')} {dist.get('spotify_uri', '')}")
            sp = m.group(1) if m else ""
        out[slug] = {"slug": slug, "name": a.get("name") or slug, "spotify_id": sp, "youtube": str(pages.get("youtube_music") or "")}
    return out


# ---------------------------------------------------------------------------
# 1 公式の窓口（API）
def _http(url: str, headers: dict | None = None, data: bytes | None = None, timeout: int = 30) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context() if url.startswith("https") else None) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return 0, {"error": str(e)}


def spotify_token() -> str | None:
    cid, sec = os.environ.get("SPOTIFY_CLIENT_ID", "").strip(), os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        return None
    if not os.environ.get("PLAYS_SPOTIFY_ACCOUNTS"):   # ふだんは、配信の確認（publish.py）と同じ取り方
        try:
            import publish
            return publish.spotify_token()
        except Exception:  # noqa: BLE001
            return None
    code, body = _http(_api("PLAYS_SPOTIFY_ACCOUNTS", "") + "/api/token", data=b"grant_type=client_credentials",
                       headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode(),
                                "Content-Type": "application/x-www-form-urlencoded"})
    return body.get("access_token") if code == 200 else None


def collect_spotify(d: dict, songs: list[dict], arts: dict, day: str) -> dict:
    """曲の人気度・組のフォロワー数と人気度。返り値：{"state": ok / need / error, "msg": …, "n": 集めた曲数}"""
    if not (os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET")):
        return {"state": "need", "msg": "Spotify の鍵がありません（setup_keys.py --only spotify）"}
    try:
        import publish
        off = publish.spotify_off()
    except Exception:  # noqa: BLE001
        off = ""
    if off:   # Premium なしで使う（決定 180）
        return {"state": "need", "msg": "Spotify の API は Premium が無いので使っていません（再生数は Spotify for Artists・YouTube などから集めます）"}
    tok = spotify_token()
    if not tok:
        return {"state": "error", "msg": "Spotify の鍵で入れませんでした（鍵を入れ直す：setup_keys.py --redo spotify）"}
    base, hdr = _api("PLAYS_SPOTIFY_API", "https://api.spotify.com/v1"), {"Authorization": f"Bearer {tok}"}
    ids = d["cache"].setdefault("track_ids", {})
    n, missing = 0, []
    for s in songs:
        tid = s["track_id"] or ids.get(s["id"], "")
        if not tid and s["isrc"]:   # 住所の記録が無い曲は、ISRC で探す
            code, body = _http(f"{base}/search?q={urllib.parse.quote('isrc:' + s['isrc'])}&type=track&limit=1", hdr)
            items = (body.get("tracks") or {}).get("items") or []
            tid = items[0].get("id", "") if code == 200 and items else ""
        if not tid:
            missing.append(s["title"])
            continue
        ids[s["id"]] = tid
        code, t = _http(f"{base}/tracks/{tid}", hdr)   # 1 曲ずつ聞く（まとめて聞く窓口は、使えないアプリがあるため）
        if code == 429:
            return {"state": "error", "msg": "Spotify が混んでいます（あとで自動でやり直します）", "n": n}
        if code != 200:
            missing.append(s["title"])
            continue
        put(d, day, "songs", s["id"], {"spotify_popularity": t.get("popularity")}, "api")
        n += 1
    na = 0
    for slug, a in arts.items():
        if not a["spotify_id"]:
            continue
        code, b = _http(f"{base}/artists/{a['spotify_id']}", hdr)
        if code == 200:
            put(d, day, "artists", slug, {"followers": (b.get("followers") or {}).get("total"), "popularity": b.get("popularity")}, "api")
            na += 1
    msg = f"Spotify：{n} 曲の人気度・{na} 組のフォロワー数"
    if missing:
        msg += f"（見つからない曲 {len(missing)}：{'・'.join(missing[:3])}）"
    return {"state": "ok", "msg": msg, "n": n}


def youtube_auth() -> tuple[str, dict] | None:
    """YouTube の窓口に入る方法：(URL に足す文字, ヘッダー)。鍵も許可も無ければ None"""
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if key:
        return f"&key={urllib.parse.quote(key)}", {}
    cid, sec, rt = (os.environ.get(k, "").strip() for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))
    if not (cid and sec and rt):
        return None
    form = urllib.parse.urlencode({"client_id": cid, "client_secret": sec, "refresh_token": rt, "grant_type": "refresh_token"}).encode()
    code, body = _http(_api("PLAYS_GOOGLE_TOKEN", "https://oauth2.googleapis.com/token"), data=form,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    return ("", {"Authorization": f"Bearer {body['access_token']}"}) if code == 200 and body.get("access_token") else None


def youtube_ready() -> bool:
    return bool(os.environ.get("YOUTUBE_API_KEY") or all(os.environ.get(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")))


def _channel(raw: str) -> tuple[str, str]:
    """記録したチャンネルの住所 → (チャンネルの ID, @名前)"""
    m = re.search(r"(UC[\w-]{22})", raw)
    h = re.search(r"/(@[\w.\-]+)", raw)
    return (m.group(1) if m else ""), (h.group(1) if h and not m else "")


def collect_youtube(d: dict, songs: list[dict], arts: dict, day: str, max_search: int = 5) -> dict:
    """YouTube Music の再生数（Topic チャンネルの動画の視聴回数）"""
    if not youtube_ready():
        return {"state": "need", "msg": "YouTube の鍵がありません（setup_keys.py --only youtube_key。SNS 投稿用の許可があれば不要）"}
    auth = youtube_auth()
    if auth is None:
        return {"state": "error", "msg": "YouTube の許可で入れませんでした（oauth_youtube.py で取り直す）"}
    q, hdr = auth
    base = _api("PLAYS_YOUTUBE_API", "https://www.googleapis.com/youtube/v3")
    cache = d["cache"]
    vids, chans, tried = cache.setdefault("yt_ids", {}), cache.setdefault("yt_channels", {}), cache.setdefault("yt_searched", {})
    err, searches = "", 0

    def get(path: str) -> dict:
        nonlocal err
        code, body = _http(f"{base}/{path}{q}", hdr)
        if code != 200:
            reason = str(((body.get("error") or {}).get("errors") or [{}])[0].get("reason", "")) if isinstance(body.get("error"), dict) else ""
            err = {"quotaExceeded": "今日の回数の上限に達しました（明日また集めます）", "keyInvalid": "YOUTUBE_API_KEY が違います",
                   "accessNotConfigured": "Google Cloud で YouTube Data API v3 を有効にしてください",
                   }.get(reason, f"YouTube から読めませんでした（{code or body.get('error')}）")
            return {}
        return body

    for slug in sorted({s["slug"] for s in songs}):
        todo = [s for s in songs if s["slug"] == slug and s["id"] not in vids]
        if not todo:
            continue
        cid, handle = _channel(arts.get(slug, {}).get("youtube", ""))
        if not cid and handle:
            cid = chans.get(slug) or ((get(f"channels?part=id&forHandle={urllib.parse.quote(handle)}").get("items") or [{}])[0].get("id", ""))
        if cid:
            chans[slug] = cid
        if cid and tried.get(f"ch:{slug}") != day:   # チャンネルの動画を新しい順に見て、曲名が同じものを探す（1 日 1 回まで）
            tried[f"ch:{slug}"] = day
            token = ""
            for _ in range(2):
                body = get(f"search?part=snippet&channelId={cid}&type=video&maxResults=50&order=date" + (f"&pageToken={token}" if token else ""))
                for it in body.get("items") or []:
                    name = _songs.norm((it.get("snippet") or {}).get("title"))
                    for s in todo:
                        if s["id"] not in vids and name == _songs.norm(s["title"]):
                            vids[s["id"]] = (it.get("id") or {}).get("videoId", "")
                token = body.get("nextPageToken") or ""
                if not token or err:
                    break
        for s in todo:   # 見つからない曲は、曲名と組の名前で探す（1 回に 5 曲まで・同じ曲は週 1 回まで）
            last = tried.get(s["id"], "")
            if vids.get(s["id"]) or err or searches >= max_search or (last and (date.fromisoformat(day) - date.fromisoformat(last)).days < 7):
                continue
            tried[s["id"]] = day
            searches += 1
            term = urllib.parse.quote(f'"{s["title"]}" "{s["artist"]}"')
            for it in get(f"search?part=snippet&q={term}&type=video&maxResults=10").get("items") or []:
                sn = it.get("snippet") or {}
                if _songs.norm(sn.get("title")) == _songs.norm(s["title"]) and _songs.norm(s["artist"]) in _songs.norm(sn.get("channelTitle")):
                    vids[s["id"]] = (it.get("id") or {}).get("videoId", "")
                    break
    have = [(s, vids[s["id"]]) for s in songs if vids.get(s["id"])]
    n = 0
    for i in range(0, len(have), 50):
        body = get("videos?part=statistics&id=" + ",".join(v for _, v in have[i:i + 50]))
        stats = {it.get("id"): it.get("statistics") or {} for it in body.get("items") or []}
        for s, v in have[i:i + 50]:
            if v in stats and stats[v].get("viewCount") is not None:
                put(d, day, "songs", s["id"], {"youtube_views": int(stats[v]["viewCount"]),
                                               "youtube_likes": int(stats[v]["likeCount"]) if stats[v].get("likeCount") else None}, "api")
                n += 1
    if err and not n:
        return {"state": "error", "msg": f"YouTube：{err}"}
    left = len(songs) - len(have)
    return {"state": "ok", "n": n, "msg": f"YouTube Music：{n} 曲の再生数" + (f"（動画がまだ見つからない曲 {left}）" if left else "") + (f"（{err}）" if err else "")}


# ---------------------------------------------------------------------------
# 2 Spotify for Artists の画面の文字を読む（画面の作りが見えないので、並びが変わっても読めるように緩く読む）
_BLANK = {"—", "–", "-", "‐", "ー", "n/a", "na", "--", ""}
_NUM = re.compile(r"^(\d{1,3}(?:[,，]\d{3})+|\d+(?:[.．]\d+)?)\s*(k|m|b|千|万|億)?\+?$", re.I)
_DATE = re.compile(r"(\d{4}\s*[/\-.年]\s*\d{1,2}\s*[/\-.月]\s*\d{1,2})|(\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4})|"
                   r"((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})|"
                   r"(\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+\d{4})", re.I)
_DURATION = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "千": 1e3, "万": 1e4, "億": 1e8}
HEAD = {"streams": r"(streams?|ストリーム数?|再生(回数|数)?|ストリーミング(回数|数)?)",
        "listeners": r"(listeners?|リスナー数?)", "views": r"(views?|視聴(回数|数)?|ビュー数?)",
        "saves": r"(saves?|保存数?)", "playlist_adds": r"(playlist adds?|プレイリスト(への)?追加数?)",
        "release": r"(release date|first released|released|リリース日?|初回リリース日?|配信日)",
        "title": r"(title|song|songs|track|タイトル|曲名?|楽曲)", "rank": r"#"}
_HEAD_RE = {k: re.compile(rf"^{v}$", re.I) for k, v in HEAD.items()}
NUMERIC_COLS = ("streams", "listeners", "views", "saves", "playlist_adds")
DEFAULT_COLS = ("streams", "listeners", "saves")
PERIODS = [("all", r"since\s*2015|all[\s-]*time|全期間|2015\s*年以降|すべての期間"), ("12m", r"12\s*months|(過去|直近)\s*12\s*(か|ヶ|カ|ケ)月|12\s*(か|ヶ|カ|ケ)月間"),
           ("28d", r"28\s*days|(過去|直近)\s*28\s*日|28\s*日間"), ("7d", r"(?<!\d)7\s*days|(過去|直近)\s*7\s*日|(?<!\d)7\s*日間"),
           ("1d", r"24\s*hours|(過去|直近)\s*24\s*時間|24\s*時間以内")]


def parse_count(tok: str):
    """「1,234」「1.2K」「12万」→ 数。読めなければ None（「<1,000」のような目安も None）"""
    t = unicodedata.normalize("NFKC", str(tok or "")).strip().replace(" ", " ").replace(" ", " ")
    if re.fullmatch(r"\d{1,3}( \d{3})+", t):   # 「1 234」
        t = t.replace(" ", "")
    m = _NUM.match(t)
    if not m:
        return None
    body, unit = m.group(1).replace("，", ",").replace("．", "."), (m.group(2) or "").lower()
    if "," in body:
        v = float(body.replace(",", ""))
    elif "." in body and not unit and re.fullmatch(r"\d{1,3}\.\d{3}", body):   # 「1.234」＝ 1234（点で区切る国の書き方）
        v = float(body.replace(".", ""))
    else:
        v = float(body)
    return int(round(v * _MULT.get(unit, 1)))


def _kind(tok: str) -> str:
    t = unicodedata.normalize("NFKC", tok).strip()
    if t.lower() in _BLANK:
        return "blank"
    if _DURATION.match(t):
        return "skip"
    if _DATE.search(t) and len(t) <= 24:
        return "skip"
    return "num" if parse_count(t) is not None else "text"


def _tokens(text: str) -> list[str]:
    """画面の文字を、表のマスの並びにする（タブ区切りの行は空のマスも残す）"""
    out = []
    for line in str(text or "").replace("\r", "").split("\n"):
        if "\t" in line:
            out += [c.strip() for c in line.split("\t")]
        elif line.strip():
            out.append(line.strip())
    return out


def _header(toks: list[str]) -> list[str]:
    """見出しの並び（streams・listeners…）。見つからなければ []"""
    def key(t):
        return next((k for k, r in _HEAD_RE.items() if r.match(unicodedata.normalize("NFKC", t).strip())), None)
    best: list[str] = []
    for i, t in enumerate(toks):
        if key(t) not in ("streams", "listeners"):
            continue
        a = i
        while a > 0 and i - a < 4 and key(toks[a - 1]):
            a -= 1
        b = i
        while b + 1 < len(toks) and b - i < 8 and key(toks[b + 1]):
            b += 1
        cols = [key(x) for x in toks[a:b + 1]]
        nums = [c for c in cols if c in NUMERIC_COLS]
        if len(nums) > len([c for c in best if c in NUMERIC_COLS]):
            best = cols
    return best


def detect_period(text: str, selected: str = "") -> str:
    """表の数字がいつの期間のものか（累計・直近 28 日 …）。
    selected（画面で「選ばれている」印が付いた部品の文字）を先に見て、無ければ画面の文字に 1 つだけ出ている期間。
    期間の言葉がいくつも出ていて選ばれているものが分からないときは unknown（累計と決めつけない）"""
    for src in (selected, text):
        low = unicodedata.normalize("NFKC", str(src or "")).lower()
        hits = [k for k, pat in PERIODS if re.search(pat, low)]
        if len(hits) == 1:
            return hits[0]
    return "unknown"


def parse_s4a_text(text: str, titles: list[str]) -> dict:
    """S4A の「楽曲」の画面の文字から、曲ごとの数字を読む。
    返り値：{曲名: {"streams": …, "listeners": …, "saves": …, "header": 見出しが読めたか}}（読めた曲だけ）"""
    toks = _tokens(text)
    norms = [_songs.norm(t) for t in toks]
    known = {_songs.norm(t): t for t in titles if t}
    cols = [c for c in _header(toks) if c in NUMERIC_COLS]
    has_header = bool(cols)
    cols = cols or list(DEFAULT_COLS)
    out = {}
    for want, title in known.items():
        if not want:
            continue
        places = [i for i, n in enumerate(norms) if n == want] or \
                 [i for i, n in enumerate(norms) if n.startswith(want + " ") and len(n) - len(want) <= 14]
        best: list = []
        for i in places:
            vals, texts, j = [], 0, i + 1
            while j < len(toks) and len(vals) < len(cols):
                k = _kind(toks[j])
                if k == "num":
                    vals.append(parse_count(toks[j]))
                elif k == "blank":
                    if vals or "\t" in text:
                        vals.append(None)
                elif k == "text":
                    if vals or norms[j] in known or texts >= 3:   # 次の曲・次のかたまりに入った
                        break
                    texts += 1                                       # 組の名前・「E」の印などは飛ばす
                j += 1
            if len([v for v in vals if v is not None]) > len([v for v in best if v is not None]):
                best = vals
        if any(v is not None for v in best):
            out[title] = {**{c: v for c, v in zip(cols, best) if v is not None}, "header": has_header}
    return out


def redact_dump(text: str, titles: list[str]) -> str:
    """調整用の記録：数字・曲名・見出し・期間の言葉だけを残し、ほかの文字は長さだけにする（メールや名前を残さない）"""
    known = {_songs.norm(t) for t in titles if t}
    lines = []
    for line in str(text or "").replace("\r", "").split("\n"):
        cells = []
        for c in line.split("\t"):
            t = c.strip()
            keep = (not t or _kind(t) != "text" or _songs.norm(t) in known
                    or any(r.match(unicodedata.normalize("NFKC", t)) for r in _HEAD_RE.values())
                    or (len(t) <= 30 and any(re.search(p, t.lower()) for _, p in PERIODS)))
            cells.append(t if keep else f"〔文字{len(t)}〕")
        lines.append("\t".join(cells))
    return "\n".join(lines)


SELECTED_JS = """() => [...document.querySelectorAll('[aria-selected=true],[aria-checked=true],[aria-pressed=true],[aria-current],option:checked,[data-selected=true]')]
  .map(e => (e.innerText || e.textContent || '').trim()).filter(t => t && t.length < 40).join('\\n')"""
ROWS_JS = """() => [...document.querySelectorAll('tr,[role=row]')].map(r =>
  [...r.querySelectorAll('th,td,[role=cell],[role=gridcell],[role=columnheader]')].map(c => ((c.innerText || '').trim().split(/\\n/)[0] || '').trim()).join('\\t')).join('\\n')"""   # マスの 1 行目だけ（曲名の下の組の名前などは読まない）


def s4a_raw(targets: list[dict], allow_open: bool = True) -> dict:
    """専用の Chrome で、組ごとの S4A の「楽曲」のページを開いて文字を読む（押すものは無い）。
    targets：[{"slug":…, "spotify_id":…, "titles":[…]}]。返り値：{"state": ok / need_login / no_chrome / skipped / error, "pages": {slug: {...}}}"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:   # この Python に部品が無い：入っている Python で読み直す（決定 59 と同じ探し方）
        from _common import python_with
        py = python_with("playwright")
        if not py or py == sys.executable:
            return {"state": "error", "msg": "ブラウザを動かす部品（Playwright）がありません（ダッシュボードの「部品を入れる」）"}
        r = subprocess.run([py, str(Path(__file__).resolve()), "s4a-raw", json.dumps(targets, ensure_ascii=False),
                            *([] if allow_open else ["--no-open"])], capture_output=True, text=True, timeout=600)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"state": "error", "msg": f"S4A を読む部品が止まりました（{(r.stderr or r.stdout)[-200:]}）"}
    import browser_fill as bf
    if not bf.chrome_path():
        return {"state": "no_chrome", "msg": "Google Chrome が見つかりません"}
    port, opened = bf._running_port(), False
    if not port:
        if not allow_open:
            return {"state": "skipped", "msg": "専用の Chrome が開いていないので、今回は S4A を読みませんでした"}
        try:
            port, opened = bf.chrome_port(), True
        except SystemExit:
            return {"state": "error", "msg": "専用の Chrome につなげませんでした（開いている専用の Chrome を ⌘Q で閉じて、もう一度）"}
    pages, state, msg = {}, "ok", ""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            try:
                for t in targets:
                    url = f"{_api('PLAYS_S4A_BASE', S4A_BASE)}/c/artist/{t['spotify_id']}/music/songs?time-filter=all"
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    want = [_songs.norm(x) for x in t.get("titles", []) if x]
                    text, end = "", time.time() + 25
                    while time.time() < end:
                        page.wait_for_timeout(1500)
                        low = page.url.lower()
                        if "accounts.spotify.com" in low or "/login" in low:
                            state, msg = "need_login", "Spotify for Artists にログインしていません"
                            break
                        text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                        if any(w and w in _songs.norm(text) for w in want):
                            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")   # 下のほうの行も出す
                            page.wait_for_timeout(1500)
                            text = page.evaluate("() => document.body ? document.body.innerText : ''") or text
                            break
                    if state != "ok":
                        break
                    try:
                        rows = page.evaluate(ROWS_JS) or ""
                    except Exception:  # noqa: BLE001
                        rows = ""
                    try:
                        selected = page.evaluate(SELECTED_JS) or ""
                    except Exception:  # noqa: BLE001
                        selected = ""
                    pages[t["slug"]] = {"text": text, "rows": rows, "selected": selected}
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        state, msg = "error", f"S4A を読めませんでした（{str(e).splitlines()[0][:160]}）"
    if opened:   # 読むために開いた Chrome は閉じる（ログインは覚えている）
        bf.quit_chrome()
    return {"state": state, "msg": msg, "pages": pages}


def _dashboard_busy() -> bool:
    """ダッシュボードで、ブラウザの自動入力が動いているか（動いていたら、定期実行では Chrome に触らない）"""
    port = os.environ.get("WEBAPP_PORT", "8765")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/busy", timeout=3) as r:
            return any(str(x).startswith("ブラウザで入力") for x in json.loads(r.read()).get("running", []))
    except Exception:  # noqa: BLE001
        return False


def collect_s4a(d: dict, songs: list[dict], arts: dict, day: str, quiet: bool = False, raw=None) -> dict:
    targets = [{"slug": slug, "spotify_id": a["spotify_id"], "titles": [s["title"] for s in songs if s["slug"] == slug]}
               for slug, a in sorted(arts.items()) if a["spotify_id"] and any(s["slug"] == slug for s in songs)]
    if not targets:
        return {"state": "need", "msg": "Spotify のアーティストのページがまだ記録されていません（定期配信のタブの通知ボタンから貼る）"}
    allow_open = True
    if quiet:
        if _dashboard_busy():
            return {"state": "skipped", "msg": "ブラウザの自動入力が動いているので、今回は S4A を読みませんでした"}
        try:
            import browser_fill as bf
            if not bf._running_port() and bf._chrome_pids():
                return {"state": "skipped", "msg": "ログイン用の Chrome が開いているので、今回は S4A を読みませんでした"}
        except Exception:  # noqa: BLE001
            pass
        allow_open = os.environ.get("PLAYS_S4A_AUTO", "1") != "0"
    res = (raw or s4a_raw)(targets, allow_open)
    if res.get("state") != "ok":
        return {"state": res.get("state", "error"), "msg": res.get("msg", "")}
    n, notes = 0, []
    for t in targets:
        pg = (res.get("pages") or {}).get(t["slug"]) or {}
        by_rows, by_text = parse_s4a_text(pg.get("rows", ""), t["titles"]), parse_s4a_text(pg.get("text", ""), t["titles"])
        got = by_rows if len(by_rows) >= len(by_text) and by_rows else by_text
        period = detect_period(pg.get("text", ""), pg.get("selected", ""))
        try:   # 調整用の記録（数字・曲名・見出しだけ）
            (DIR / "diag").mkdir(parents=True, exist_ok=True)
            (DIR / "diag" / f"{day}_{t['slug']}.txt").write_text(
                f"# 期間：{period}／読めた曲 {len(got)}/{len(t['titles'])}\n# --- 選ばれている部品 ---\n{redact_dump(pg.get('selected', ''), t['titles'])}"
                f"\n# --- 画面の文字 ---\n{redact_dump(pg.get('text', ''), t['titles'])}"
                f"\n# --- 表の行 ---\n{redact_dump(pg.get('rows', ''), t['titles'])}\n", encoding="utf-8")
            for old in sorted((DIR / "diag").glob(f"*_{t['slug']}.txt"))[:-5]:
                old.unlink()
        except OSError:
            pass
        for s in songs:
            v = got.get(s["title"]) if s["slug"] == t["slug"] else None
            if not v:
                continue
            put(d, day, "songs", s["id"], {"spotify_streams": v.get("streams"), "spotify_listeners": v.get("listeners"),
                                           "spotify_saves": v.get("saves"), "spotify_period": period,
                                           "spotify_header": bool(v.get("header"))}, "s4a")
            n += 1
        if not got:
            notes.append(f"{arts[t['slug']]['name']}：曲が見つかりません（S4A でこの組のページを受け取っているか確かめる）")
        elif period != "all":
            notes.append(f"{arts[t['slug']]['name']}：数字は「{PERIOD_JA[period]}」のもの")
    return {"state": "ok" if n else "error", "n": n, "msg": f"Spotify for Artists：{n} 曲の再生数" + (f"（{'／'.join(notes)}）" if notes else "")}


# ---------------------------------------------------------------------------
# 3 ダウンロードしたファイル
def file_dirs() -> list[Path]:
    env = os.environ.get("PLAYS_INBOX", "").strip()
    return [Path(x.strip()).expanduser() for x in env.split(";") if x.strip()] if env else [Path.home() / "Downloads"]


def sniff(p: Path) -> str:
    """ファイルの 1 行目から種類を見分ける：distrokid ／ s4a_songs ／ ""（関係ないファイル）"""
    try:
        with p.open(encoding="utf-8-sig", errors="replace") as f:
            head = f.readline().lower()
    except OSError:
        return ""
    cols = [c.strip().strip('"') for c in re.split(r"[\t,]", head)]
    if "sale month" in cols and "store" in cols and "quantity" in cols:
        return "distrokid"
    has_title = any(c in ("song", "track", "title", "song name", "track name", "songs") for c in cols)
    if has_title and "streams" in cols and ("listeners" in cols or "saves" in cols):
        return "s4a_songs"
    return ""


def find_files() -> list[tuple[Path, str]]:
    out, limit = [], time.time() - 60 * 86400
    for d in file_dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            try:
                ok = p.is_file() and p.suffix.lower() in (".csv", ".tsv", ".txt") and not p.name.startswith(".") \
                    and 0 < p.stat().st_size < 50 * 1024 * 1024 and p.stat().st_mtime >= limit
            except OSError:
                ok = False
            kind = sniff(p) if ok else ""
            if kind:
                out.append((p, kind))
    return out


def _metrics():
    """成長分析の記録の部品（collect_metrics.py）。読み込みと表の読み方を借りる"""
    import collect_metrics
    return collect_metrics


def collect_files(d: dict, songs: list[dict], day: str) -> dict:
    """DistroKid の明細・S4A の CSV を取り込む（同じ中身のファイルは 2 度入れない）"""
    files = find_files()
    if not files:
        return {"state": "none", "n": 0, "msg": "ダウンロードのフォルダに、DistroKid の明細や Spotify for Artists の CSV はありません"}
    cm = _metrics()
    by_key = {s["key"]: s for s in songs}
    rels = [{"release_id": s["key"], "artist_slug": s["slug"], "artist": s["artist"], "title": s["title"], "isrc": s["isrc"],
             "release_date": s["release_date"] or day} for s in songs]
    find = cm.matcher(rels)
    seen = d["cache"].setdefault("files", {})
    done_dir = DIR / "imported"
    done_dir.mkdir(parents=True, exist_ok=True)
    n, msgs = 0, []
    for p, kind in files:
        sha = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        dst = done_dir / f"{day}_{p.name}"
        if sha in seen:
            shutil.move(str(p), dst)
            msgs.append(f"{p.name}：前に取り込んだものと同じ")
            continue
        rows_in = cm.read_table(p)
        first = rows_in[0] if rows_in else {}
        got = 0
        if kind == "distrokid":   # 明細は「いままでの全部」が入っているので、足さずに置き換える
            tot: dict[str, dict] = {}
            for r in rows_in:
                if (r.get(cm.col(first, "song/album")) or "Song").lower().startswith("album"):
                    continue
                rel = find(r.get(cm.col(first, "isrc")) or "", r.get(cm.col(first, "artist")) or "", r.get(cm.col(first, "title")) or "")
                if not rel:
                    continue
                q = cm.num(r.get(cm.col(first, "quantity")))
                t = tot.setdefault(rel["release_id"], {"dk_streams": 0, "dk_spotify": 0, "dk_month": ""})
                t["dk_streams"] += q
                if cm.store_name(r.get(cm.col(first, "store")) or "") == "spotify":
                    t["dk_spotify"] += q
                t["dk_month"] = max(t["dk_month"], str(r.get(cm.col(first, "sale month")) or "")[:7])
            for key, t in tot.items():
                put(d, day, "songs", by_key[key]["id"], t, "files")
            got = len(tot)
        else:                     # S4A の「楽曲」の CSV（書き出したときに選んでいた期間の数字）
            c_title = cm.col(first, "song", "track", "title")
            for r in rows_in:
                rel = find("", "", r.get(c_title) or "") if c_title else None
                if not rel:
                    continue
                vals = {"spotify_streams": cm.num(r.get(cm.col(first, "streams"))), "spotify_period": "file", "spotify_header": True}
                for name, field in (("listeners", "spotify_listeners"), ("saves", "spotify_saves")):
                    if cm.col(first, name):
                        vals[field] = cm.num(r.get(cm.col(first, name)))
                put(d, day, "songs", by_key[rel["release_id"]]["id"], vals, "files")
                got += 1
        try:   # 成長分析の記録（out/metrics/daily.csv）にも入れる
            daily = cm.load_daily()
            if kind == "distrokid":
                daily = {k: v for k, v in daily.items() if v.get("kind") != "dsp_monthly"}
            sp = cm.M / "snapshots.json"
            snap = read_json(sp) if sp.exists() else {}
            cm.import_file(p, find, daily, snap, [])
            cm.save_daily(daily)
            write_json(sp, snap)
        except Exception as e:  # noqa: BLE001  こちらが失敗しても、再生数のページの数字は残す
            msgs.append(f"成長分析の記録には入れられませんでした（{str(e)[:80]}）")
        seen[sha] = {"name": p.name, "kind": kind, "day": day, "songs": got}
        shutil.move(str(p), dst)
        n += got
        msgs.append(f"{p.name}：{'DistroKid の明細' if kind == 'distrokid' else 'S4A の CSV'}・{got} 曲")
    return {"state": "ok", "n": n, "msg": "ファイル：" + "／".join(msgs), "moved_to": str(done_dir)}


# ---------------------------------------------------------------------------
def collect(layers=LAYERS, quiet: bool = False) -> dict:
    load_dotenv(override=True)
    day = date.today().isoformat()
    songs = released_songs()
    d = load()
    res: dict[str, dict] = {}
    if not songs:
        msg = "配信が始まった曲がまだありません（配信が確認できると、自動で集め始めます）"
        d["last"] = {"at": datetime.now().isoformat(timespec="seconds"), "layers": {}, "msg": msg}
        save(d)
        print(f"   {msg}\n【結果】{msg}")
        return {"songs": 0, "layers": {}}
    arts = artists({s["slug"] for s in songs})
    step(f"配信が始まった曲 {len(songs)} 曲の再生数を集めます")
    todo = {"api_spotify": lambda: collect_spotify(d, songs, arts, day), "api_youtube": lambda: collect_youtube(d, songs, arts, day),
            "s4a": lambda: collect_s4a(d, songs, arts, day, quiet), "files": lambda: collect_files(d, songs, day)}
    names = {"api_spotify": "Spotify の公式の窓口", "api_youtube": "YouTube の公式の窓口", "s4a": "Spotify for Artists の画面", "files": "ダウンロードしたファイル"}
    for key, fn in todo.items():
        if key.split("_")[0] not in layers:
            continue
        step(f"{names[key]}から集めています")
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001  1 つが失敗しても、ほかは集める
            r = {"state": "error", "msg": f"{names[key]}：途中で止まりました（{str(e)[:140]}）"}
        res[key] = {**r, "at": datetime.now().isoformat(timespec="seconds")}
        mark = {"ok": "○", "need": "－", "none": "－", "skipped": "－", "need_login": "！"}.get(r.get("state"), "✕")
        print(f"   {mark} {r.get('msg', '')}")
    prev = (d.get("last") or {}).get("layers") or {}
    d["last"] = {"at": datetime.now().isoformat(timespec="seconds"), "layers": {**prev, **res}}
    save(d)
    got = sum(r.get("n", 0) for r in res.values())
    total = sum(v for v in (total_plays(d, s["id"]) for s in songs) if v is not None)
    need = [names[k] for k, r in res.items() if r.get("state") in ("need", "need_login")]
    summary = (f"{len(songs)} 曲の数字を集めました（再生数の合計 {total:,} 回）" if got else "新しい数字は集まりませんでした") + \
              (f"。まだ集められないもの：{'・'.join(need)}（再生数のページに、やることを出しています）" if need else "")
    print(f"【結果】{summary}")
    return {"songs": len(songs), "got": got, "layers": res}


def readiness(d: dict | None = None) -> dict:
    """集め方ごとに、使えるか・足りないもの・押すボタン（通信はしない）"""
    d = d or load()
    last = (d.get("last") or {}).get("layers") or {}
    sp = bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))
    s4 = last.get("s4a") or {}
    s4_state = s4.get("state", "")
    return {
        "api_spotify": {"name": "Spotify の公式の窓口（人気度・フォロワー数）", "ready": sp,
                        "need": "" if sp else "Spotify の鍵を入れる（無料）",
                        "action": None if sp else {"action": "terminal", "key": "spotify", "label": "Spotify の鍵を入れる（ターミナルが開きます）"},
                        "last": last.get("api_spotify") or {}},
        "api_youtube": {"name": "YouTube の公式の窓口（YouTube Music の再生数）", "ready": youtube_ready(),
                        "need": "" if youtube_ready() else "YouTube の API キーを入れる（無料）",
                        "action": None if youtube_ready() else {"action": "terminal", "key": "youtube_key", "label": "YouTube の API キーを入れる（ターミナルが開きます）"},
                        "last": last.get("api_youtube") or {}},
        "s4a": {"name": "Spotify for Artists の画面（再生数・リスナー数・保存数）", "ready": s4_state == "ok",
                "need": {"": "まだ読んでいません（「いま集める」を押す。初回は先にログイン）", "ok": "",
                         "need_login": "専用の Chrome で Spotify for Artists にログインする（初回だけ）"}.get(s4_state, s4.get("msg", "")),
                "action": {"action": "browser_login", "site": "spotify", "label": "Spotify for Artists にログイン（初回だけ・Chrome が開きます）"} if s4_state != "ok" else None,
                "last": s4},
        "files": {"name": "ダウンロードしたファイル（DistroKid の明細・S4A の CSV）", "ready": True,
                  "need": "ダウンロードのフォルダに置くだけで、次に集めるときに取り込みます", "action": None, "last": last.get("files") or {}},
    }


def status() -> dict:
    """ダッシュボードに出す要約（通信はしない）"""
    d = load()
    songs = released_songs()
    rows = []
    for s in songs:
        now = total_plays(d, s["id"])
        d7 = [delta(d, "songs", s["id"], f, 7) for f in ("spotify_streams", "youtube_views")]
        rows.append({"id": s["id"], "artist": s["artist"], "title": s["title"], "plays": now,
                     "d7": None if all(x is None for x in d7) else sum(x or 0 for x in d7)})
    have = [r for r in rows if r["plays"] is not None]
    d7s = [r["d7"] for r in rows if r["d7"] is not None]
    followers = [latest(d, "artists", slug, "followers")[0] for slug in {s["slug"] for s in songs}]
    rd = readiness(d)
    return {"last": (d.get("last") or {}).get("at", ""), "songs": len(songs), "with_plays": len(have),
            "total_plays": sum(r["plays"] for r in have) if have else None, "plays_7d": sum(d7s) if d7s else None,
            "followers": sum(f for f in followers if f is not None) if any(f is not None for f in followers) else None,
            "top": sorted(have, key=lambda r: -r["plays"])[:5],
            "layers": {k: {"ready": v["ready"], "need": v["need"]} for k, v in rd.items()}}


# ---------------------------------------------------------------------------
# ページ（/plays）
def _spark(vals: list[int]) -> str:
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = [(2 + i * 76 / (len(vals) - 1), 20 - (v - lo) * 18 / span) for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return (f'<svg viewBox="0 0 80 22" width="80" height="22" role="img" aria-label="再生数の動き" style="color:var(--accent);vertical-align:middle">'
            f'<polyline fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" points="{line}"/>'
            f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2" fill="currentColor"/></svg>')


def _plus(v) -> str:
    return "—" if v is None else (f"+{v:,}" if v > 0 else f"{v:,}")


def page(token: str) -> str:
    import _ui
    E = _ui.E
    d = load()
    songs = released_songs()
    st = status()
    rd = readiness(d)
    arts = artists({s["slug"] for s in songs}) if songs else {}
    last = st["last"].replace("T", " ")[:16] if st["last"] else "まだ"
    body = ['<h1>再生数</h1><p class="sub">配信が始まった曲の数字を、毎日自動で集めます。'
            f'最後に集めた時刻：{E(last)}</p>',
            _ui.kpis([(_ui.num(st["total_plays"]) if st["total_plays"] is not None else "—", "再生数の合計", "Spotify の累計 ＋ YouTube Music"),
                      (_plus(st["plays_7d"]), "直近 7 日の増え方", "7 日前の記録との差"),
                      (_ui.num(st["followers"]) if st["followers"] is not None else "—", "Spotify のフォロワー", "全部の組の合計"),
                      (f'{st["with_plays"]} / {st["songs"]}', "数字がある曲 / 配信中の曲", "")]),
            '<div class="row">' + _ui.btn({"action": "plays_collect", "label": "いま集める"}, primary=True)
            + '<span class="sub">Spotify for Artists を読むときは、専用の Chrome が少しの間だけ開きます（読むだけで、何も押しません）</span></div>']
    if not songs:
        body.append('<section class="card"><p class="empty">配信が始まった曲がまだありません。配信が確認できると、ここに並びます。</p></section>')
    for slug in sorted({s["slug"] for s in songs}, key=lambda x: arts[x]["name"]):
        a = arts[slug]
        fol, pop = latest(d, "artists", slug, "followers")[0], latest(d, "artists", slug, "popularity")[0]
        rows = []
        for s in sorted((x for x in songs if x["slug"] == slug), key=lambda x: x["release_date"], reverse=True):
            g = lambda f, sid=s["id"]: latest(d, "songs", sid, f)[0]  # noqa: E731
            per = g("spotify_period") or ""
            sp = g("spotify_streams")
            sp_txt = "—" if sp is None else _ui.num(sp) + ("" if per == "all" else f'<br><span class="sub">{E(PERIOD_JA.get(per, per))}</span>')
            if sp is not None and g("spotify_header") is False:
                sp_txt += '<br><span class="sub">列の並びは仮</span>'
            dk = g("dk_streams")
            link = f'<a href="{E(s["url"])}" target="_blank" rel="noopener" style="white-space:nowrap">Spotify ↗</a>' if s["url"] else ""
            rows.append([f'{E(s["title"] or "（曲名未定）")}<br><span class="sub" style="white-space:nowrap">{E(s["id"])}</span>',
                         f'<span style="white-space:nowrap">{E(s["release_date"] or "—")}</span>', sp_txt,
                         _plus(delta(d, "songs", s["id"], "spotify_streams", 7)), _ui.num(g("spotify_listeners")), _ui.num(g("spotify_saves")),
                         _ui.num(g("spotify_popularity")), _ui.num(g("youtube_views")), _plus(delta(d, "songs", s["id"], "youtube_views", 7)),
                         "—" if dk is None else f'{_ui.num(dk)}<br><span class="sub">{E(g("dk_month") or "")} まで</span>',
                         _spark(series(d, s["id"])), link])
        s4a = f'<a href="{S4A_BASE}/c/artist/{E(a["spotify_id"])}/music/songs" target="_blank" rel="noopener">Spotify for Artists ↗</a>' if a["spotify_id"] else ""
        body.append(f'<section class="card"><h2>{E(a["name"])} '
                    + (_ui.badge("info", f"フォロワー {_ui.num(fol)}") if fol is not None else "")
                    + (_ui.badge("info", f"人気度 {pop}") if pop is not None else "") + f' <span class="sub">{s4a}</span></h2>'
                    + _ui.table(["曲", "配信日", "Spotify 再生", "7 日", "リスナー", "保存", "人気度", "YouTube Music", "7 日", "DistroKid の明細", "動き", ""],
                                rows, num=(2, 3, 4, 5, 6, 7, 8, 9), raw=(0, 1, 2, 9, 10, 11)) + "</section>")
    ways = []
    for key, v in rd.items():
        lastmsg = (v["last"] or {}).get("msg", "")
        state = (v["last"] or {}).get("state", "")
        bd = _ui.badge("done", "✓ 集められます") if v["ready"] and state in ("ok", "none", "") else \
            _ui.badge("ng", "！ うまくいっていません") if state == "error" else _ui.badge("wait", "○ まだ")
        ways.append(f'<h3>{E(v["name"])} {bd}</h3>'
                    + (f'<p class="sub">{E(v["need"])}</p>' if v["need"] else "")
                    + (f'<p class="sub">前回：{E(lastmsg)}</p>' if lastmsg else "")
                    + (_ui.btn(v["action"]) if v["action"] else ""))
    body.append('<section class="card"><h2>集め方と、いまの状態</h2>' + "".join(ways) + "</section>")
    body.append('<section class="card"><h2>数字の読み方</h2><ul class="sub">'
                "<li><b>Spotify 再生・リスナー・保存</b>：Spotify for Artists の「楽曲」の表の数字。Spotify 側の集計が 1〜2 日遅れます。"
                "「累計」と付いていない数字は、その期間だけの数字です（差は出しません）。</li>"
                "<li><b>人気度</b>：Spotify が付ける 0〜100 の目安。再生数ではありません（出したばかりの曲は 0 が普通）。</li>"
                "<li><b>YouTube Music</b>：アーティストの Topic チャンネルの動画の視聴回数（YouTube Music で聴かれた回数）。</li>"
                "<li><b>DistroKid の明細</b>：全ストアの合計。DistroKid に届くのが 2〜3 か月遅れます。"
                "Bank → See excruciating detail → Download で保存すると、自動で取り込みます。</li>"
                "<li><b>7 日</b>：7 日前までの記録との差。記録が 2 日ぶんたまるまでは「—」。</li>"
                "<li>Apple Music・Amazon Music の再生数は、公式の窓口が無いので DistroKid の明細で見ます。</li></ul></section>")
    return _ui.page("再生数", "".join(body), token, active="/plays")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="配信が始まった曲の再生数を集める（決定 172）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="集める")
    c.add_argument("--layers", default=",".join(LAYERS), help="api,s4a,files から選ぶ")
    c.add_argument("--quiet", action="store_true", help="定期実行（ほかの仕事の邪魔をしない）")
    sub.add_parser("status", help="いまの数字と、足りないもの")
    p = sub.add_parser("parse-test", help="S4A の画面の文字を読めるか試す")
    p.add_argument("file")
    p.add_argument("--titles", default="", help="曲名（カンマ区切り）。無ければ配信中の曲")
    r = sub.add_parser("s4a-raw")   # 内部用：Playwright の入った Python で読み直す
    r.add_argument("targets")
    r.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    if a.cmd == "collect":
        layers = tuple(x.strip() for x in a.layers.split(",") if x.strip() in LAYERS)
        res = collect(layers, a.quiet)
        need = any(r.get("state") in ("need_login", "error") for r in res.get("layers", {}).values())
        sys.exit(2 if (not a.quiet and need and not res.get("got")) else 0)
    if a.cmd == "status":
        st = status()
        print(f"   最後に集めた時刻：{st['last'] or 'まだ'}／配信中 {st['songs']} 曲のうち数字がある曲 {st['with_plays']} 曲")
        print(f"   再生数の合計：{st['total_plays'] if st['total_plays'] is not None else '—'}／直近 7 日：{_plus(st['plays_7d'])}／フォロワー：{st['followers'] if st['followers'] is not None else '—'}")
        for r in st["top"]:
            print(f"     {r['plays']:>8,}  {r['artist']}「{r['title']}」")
        for k, v in readiness().items():
            print(f"   {'○' if v['ready'] else '－'} {v['name']}" + (f"：{v['need']}" if v["need"] else ""))
        return
    if a.cmd == "parse-test":
        text = Path(a.file).read_text(encoding="utf-8", errors="replace")
        titles = [t.strip() for t in a.titles.split(",") if t.strip()] or [s["title"] for s in released_songs()]
        print(f"   期間：{PERIOD_JA[detect_period(text)]}／見出し：{_header(_tokens(text)) or '読めません'}")
        got = parse_s4a_text(text, titles)
        for t in titles:
            print(f"   {'○' if t in got else '✕'} {t}：{got.get(t, '見つかりません')}")
        return
    if a.cmd == "s4a-raw":
        print(json.dumps(s4a_raw(json.loads(a.targets), not a.no_open), ensure_ascii=False))


if __name__ == "__main__":
    main()

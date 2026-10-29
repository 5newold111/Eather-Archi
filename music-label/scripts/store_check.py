#!/usr/bin/env python3
"""
鍵なしで「配信が始まったか」を確かめる（Spotify Premium や Spotify の開発者の鍵が無くても。決定 57）

公開されている窓口だけを使う（どれも登録・鍵は要らない。読むだけで、何も書き換えない）：
  1 Apple Music … iTunes Search API（itunes.apple.com/search）。曲・Apple のアーティストの ID と住所
  2 Deezer      … Deezer の公開 API（api.deezer.com）。曲と ISRC（曲の番号）・UPC（作品の番号）
  3 Spotify     … song.link（Odesli。曲の住所から、ほかのサイトの同じ曲の住所を教えてくれる）で Spotify の曲の住所を知り、
                  Spotify の曲のページ（open.spotify.com/track/…）に書かれているアーティストのページの住所を読む
                  見つからなければ、Claude に「Spotify 曲名 アーティスト名」で Web 検索させる（ANTHROPIC_API_KEY があるとき）
  曲名とアーティスト名がどちらも同じときだけ「見つかった」とする（同じ名前の別の人の曲を拾わないように）

使い方：
  python3 scripts/store_check.py PDR-003        # 1 曲を確かめて結果を出す（記録はしない）
  publish.py track が、Spotify の鍵が無いときにこれを使う（見つかったら配信中にして記録する）
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ssl_context  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) PostdogRecords/1.0 (release check)"}


def _get(url: str, as_json: bool = True, timeout: int = 20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def _norm(s: object) -> str:
    import _songs
    return _songs.norm(s)


def _same(a: object, b: object) -> bool:
    return bool(_norm(a)) and _norm(a) == _norm(b)


def apple(song: dict) -> dict:
    """Apple Music（iTunes Search API）。{url, artist_id, artist_url, released}"""
    q = urllib.parse.urlencode({"term": f"{song['artist']} {song['title']}", "entity": "song", "limit": 25, "country": "US"})
    for t in _get(f"https://itunes.apple.com/search?{q}").get("results", []):
        if _same(t.get("trackName"), song["title"]) and _same(t.get("artistName"), song["artist"]):
            return {"url": t.get("trackViewUrl", ""), "artist_id": str(t.get("artistId") or ""),
                    "artist_url": t.get("artistViewUrl", ""), "released": str(t.get("releaseDate") or "")[:10]}
    return {}


def deezer(song: dict) -> dict:
    """Deezer。{url, isrc, upc, released}"""
    q = urllib.parse.quote(f'artist:"{song["artist"]}" track:"{song["title"]}"')
    for t in _get(f"https://api.deezer.com/search?q={q}&limit=10").get("data", []):
        if _same(t.get("title"), song["title"]) and _same((t.get("artist") or {}).get("name"), song["artist"]):
            out = {"url": t.get("link", "")}
            try:
                d = _get(f"https://api.deezer.com/track/{t['id']}")
                out["isrc"] = d.get("isrc", "")
                out["released"] = d.get("release_date", "")
                alb = _get(f"https://api.deezer.com/album/{(t.get('album') or {}).get('id')}")
                out["upc"] = alb.get("upc", "")
            except Exception:  # noqa: BLE001
                pass
            return out
    return {}


def odesli(url: str) -> dict:
    """song.link：ある曲の住所から、ほかのサイトの同じ曲の住所 {spotify, youtubeMusic, …}"""
    q = urllib.parse.urlencode({"url": url, "userCountry": "US"})
    d = _get(f"https://api.song.link/v1-alpha.1/links?{q}")
    return {k: v.get("url", "") for k, v in (d.get("linksByPlatform") or {}).items()}


def spotify_track_page(url: str, song: dict) -> dict:
    """Spotify の曲のページを読む。曲名が同じなら {url, track_id, artist_id}（ページに書かれたアーティストの住所から）"""
    m = re.search(r"/track/([A-Za-z0-9]{22})", url or "")
    if not m:
        return {}
    html = _get(f"https://open.spotify.com/track/{m.group(1)}", as_json=False)
    title = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    if title and not _same(title.group(1), song["title"]):
        return {}
    artists = re.findall(r'<meta name="music:musician" content="https://open\.spotify\.com/artist/([A-Za-z0-9]{22})"', html)
    out = {"url": f"https://open.spotify.com/track/{m.group(1)}", "track_id": m.group(1)}
    for aid in artists:   # 曲に名前が出ている人のうち、この組の名前のページ（feat. の相手を拾わない）
        try:
            name = _get("https://open.spotify.com/oembed?" + urllib.parse.urlencode({"url": f"https://open.spotify.com/artist/{aid}"})).get("title", "")
        except Exception:  # noqa: BLE001
            name = ""
        if _same(name, song["artist"]) or (len(artists) == 1 and not name):
            out["artist_id"] = aid
            break
    return out


def spotify_by_web_search(song: dict) -> str:
    """Claude に「Spotify 曲名 アーティスト名」で Web 検索させ、Spotify の曲の住所の候補を返す（鍵があるとき）"""
    try:
        import _claude
    except Exception:  # noqa: BLE001
        return ""
    if not _claude.available():
        return ""
    text, urls, _ = _claude.call_research(
        "あなたは音楽の配信を確かめる係です。Web 検索で Spotify の曲のページを探し、見つけた住所だけを 1 行で答えます。見つからなければ「なし」とだけ答えます。",
        f"Spotify で配信されている曲「{song['title']}」（アーティスト名 {song['artist']}）の曲のページ（https://open.spotify.com/track/…）を探してください。"
        "曲名とアーティスト名がどちらも同じものだけ。", max_searches=4, effort="low")
    for u in [*(re.findall(r"https://open\.spotify\.com/(?:intl-\w+/)?track/[A-Za-z0-9]{22}", text or "")), *urls]:
        if "/track/" in u:
            return u
    return ""


def check(song: dict, web_search: bool = True) -> dict:
    """{live: bool, apple: {...}, deezer: {...}, spotify: {...}, youtube_music: 住所, notes: [...]}"""
    out: dict = {"apple": {}, "deezer": {}, "spotify": {}, "notes": []}
    for name, fn in (("apple", apple), ("deezer", deezer)):
        try:
            out[name] = fn(song)
        except Exception as e:  # noqa: BLE001
            out["notes"].append(f"{name} を確かめられませんでした（{str(e)[:80]}）")
    src = out["apple"].get("url") or out["deezer"].get("url")
    sp_url = ""
    if src:
        try:
            links = odesli(src)
            sp_url = links.get("spotify", "")
            out["youtube_music"] = links.get("youtubeMusic", "")
            time.sleep(1)   # song.link は 1 分に 10 回まで
        except Exception as e:  # noqa: BLE001
            out["notes"].append(f"song.link を確かめられませんでした（{str(e)[:80]}）")
    if not sp_url and web_search:
        sp_url = spotify_by_web_search(song)
    if sp_url:
        try:
            out["spotify"] = spotify_track_page(sp_url, song)
        except Exception as e:  # noqa: BLE001
            out["notes"].append(f"Spotify の曲のページを読めませんでした（{str(e)[:80]}）")
    out["live"] = bool(out["spotify"].get("url") or out["apple"].get("url") or out["deezer"].get("url"))
    return out


def main() -> None:
    import _songs
    if len(sys.argv) < 2:
        sys.exit("使い方：python3 scripts/store_check.py <曲 ID>")
    song = _songs.by_id(sys.argv[1])
    if song is None:
        sys.exit(f"[エラー] 曲 {sys.argv[1]} が見つかりません")
    r = check(song)
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

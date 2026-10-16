#!/usr/bin/env python3
"""
公開の流れ：Suno で「いいね」してダウンロードした曲を、DistroKid の登録ボタンを押すだけの状態まで自動で進める

オーナーがやること（Suno と DistroKid は規約上、機械が勝手に操作しない。docs/12_decisions.md の 17）：
  1. `python3 scripts/publish.py suno` で開くページから、曲名・スタイル・歌詞をコピーして Suno に貼り、「Create」を押す
  2. できたテイクを聴き、気に入った方に「いいね」して、その 1 本をダウンロードして
     ダウンロードの「SUNO楽曲」フォルダに入れる（WAV がおすすめ。ファイル名は Suno が付けた曲名のままでよい）
  3. DistroKid の画面で、Claude が入れた下書きを確かめて、最後の登録ボタンを押す

ここから先は自動（Mac の定期実行が「SUNO楽曲」フォルダを見張っていて、曲が入ると数分以内に動く）：
  ・ファイル名（とフォルダ名）から、どの曲かを見分ける（曲名・曲名の別案・アーティスト名）
  ・選んだテイクとして記録し、音量を配信向けに整える（-14 LUFS / -1 dBTP・44.1kHz / 24bit の WAV）
  ・ジャケットを用意する（デビュー曲で無ければ自動で作る）
  ・DistroKid の下書き（入力する項目の一覧）と歌詞を作る
  ・公開セット（ローカル保存（一時保存）/<アーティスト名>/04_楽曲/<曲名>/）にまとめ、SSD があれば SSD にも写す
  ・楽曲管理シートのフェーズを進め、Mac に「登録できます」と知らせる
  ・登録したあと：毎日 Spotify を確かめ、配信が始まったら ISRC・UPC・配信 URL を自動で記録し、フェーズを「8 配信中」にする

使い方：
  python3 scripts/publish.py suno [--open]          # Suno に貼る内容のページを作る（--open でブラウザで開く）
  python3 scripts/publish.py scan                   # 「SUNO楽曲」に入った曲を取り込んで、公開の準備まで進める（定期実行でも動く）
  python3 scripts/publish.py status                 # 曲ごとの進み具合
  python3 scripts/publish.py draft PDR-001          # DistroKid の下書きを表示（Claude がブラウザで入力するときにも使う）
  python3 scripts/publish.py registered PDR-001     # DistroKid に登録した（配信日は --date。省くと今日＝いちばん早い日）
  python3 scripts/publish.py track                  # 登録済みの曲が配信されたかを Spotify で確かめ、ISRC などを記録

鍵（music-label/.env。会話には貼らない）：
  SPOTIFY_CLIENT_ID・SPOTIFY_CLIENT_SECRET … 配信の確認と ISRC の自動記録（Spotify for Developers で無料で作れる）
  OWNER_LEGAL_NAME                          … DistroKid の作詞作曲者の欄に入れる本名（手元だけ。GitHub には入らない）
  SUNO_INBOX                                … 見張るフォルダを変えるとき（既定：ダウンロード/SUNO楽曲）
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_artist, load_dotenv, monday_of, notify, read_json, save_artist, step  # noqa: E402

load_dotenv()
import _archive as archive  # noqa: E402
import _songs  # noqa: E402

S = Path(__file__).resolve().parent
PUB = OUT / "publish"
REGISTRY = PUB / "imported.json"
AUDIO = {".wav", ".mp3", ".m4a", ".flac", ".aif", ".aiff"}
DONE = ("registered", "live")
STATUS_JA = {
    "": "2 Suno で作曲（曲が届くのを待っています）", "imported": "3 取り込み済み", "waiting_ffmpeg": "4 音量調整待ち（ffmpeg が必要）",
    "waiting_cover": "5 ジャケット待ち", "ready": "6 登録の準備ができました（DistroKid へ）",
    "registered": "7 DistroKid 登録済み（配信待ち）", "live": "8 配信中",
}


# ---------------------------------------------------------------------------
# 見張るフォルダ
def inbox() -> Path:
    env = os.environ.get("SUNO_INBOX", "").strip()
    if env:
        return Path(env).expanduser()
    return archive.child(archive._mkdir(Path.home() / "Downloads"), archive.LOCAL_PARENT)


def new_files() -> list[Path]:
    """「SUNO楽曲」の中の、まだ取り込んでいない音源（ローカル保存の中・ダウンロード途中のものは除く）"""
    box = inbox()
    if not box.is_dir():
        return []
    seen = read_json(REGISTRY) if REGISTRY.exists() else {}
    skip_dirs = {_songs.norm(archive.LOCAL_NAME), _songs.norm("取り込み済み")}
    out = []
    try:
        listing = sorted(box.rglob("*"))
    except PermissionError:
        print("   ✕ 「ダウンロード」フォルダを読む許可がありません。システム設定 → プライバシーとセキュリティ → フルディスクアクセス に"
              " python3（/usr/bin/python3 など）を追加してください")
        return []
    for f in listing:
        if not f.is_file() or f.suffix.lower() not in AUDIO or f.name.startswith((".", "~")):
            continue
        if any(_songs.norm(p) in skip_dirs for p in f.relative_to(box).parts[:-1]):
            continue
        st = f.stat()
        if time.time() - st.st_mtime < 15:      # ダウンロードの途中かもしれない
            continue
        if _sig(f) not in seen:
            out.append(f)
    return out


def _sig(f: Path) -> str:
    st = f.stat()
    return f"{f.resolve()}|{st.st_size}|{int(st.st_mtime)}"


def _clean(stem: str) -> str:
    s = re.sub(r"\s*\(\d+\)$", "", stem)                       # 「Side B (1)」
    s = re.sub(r"[\s_-]*(v|ver|version|take)\s*\d+$", "", s, flags=re.I)
    s = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", s)
    s = re.sub(r"\bsuno\b", " ", s, flags=re.I)
    return _songs.norm(s)


def match(f: Path, songs: list[dict]) -> tuple[dict | None, str]:
    """ファイル名（とフォルダ名）から曲を見分ける。(曲, 理由)"""
    name = _clean(f.stem)
    folders = " ".join(_songs.norm(p) for p in f.parts[-3:-1])
    best, best_score, ties = None, 0, []
    for s in songs:
        titles = [t for t in [s["title"], *s["alts"]] if t]
        score = 0
        for t in titles:
            nt = _songs.norm(t)
            if not nt:
                continue
            if name == nt:
                score = max(score, 3)
            elif re.search(rf"(^| ){re.escape(nt)}( |$)", name):
                score = max(score, 2)
        if score == 0:
            continue
        na = _songs.norm(s["artist"])
        if na and (re.search(rf"(^| ){re.escape(na)}( |$)", name) or na in folders):
            score += 1
        if score > best_score:
            best, best_score, ties = s, score, []
        elif score == best_score:
            ties.append(s)
    if best is None:
        return None, "曲名が見つかりません（ファイル名を Suno の曲名のままにするか、曲名を含めてください）"
    if ties:
        names = "・".join(f"{x['artist']}「{x['title']}」" for x in [best, *ties])
        return None, f"どの曲か決められません（{names}）。アーティスト名のフォルダに入れるか、ファイル名にアーティスト名を足してください"
    return best, ""


# ---------------------------------------------------------------------------
# 1 曲を公開の準備まで進める
def take_in(song: dict, f: Path) -> None:
    """いいねしたテイクを選んだテイクとして記録する（同じ曲に新しいファイルが来たら、そちらに差し替える）"""
    tdir = OUT / "takes" / song["key"]
    tdir.mkdir(parents=True, exist_ok=True)
    dst = tdir / f"suno_{_songs.safe(f.stem)}{f.suffix.lower()}"
    shutil.copy2(f, dst)
    sel_p = tdir / "selection.json"
    sel = read_json(sel_p) if sel_p.exists() else {}
    prev = (sel.get("decision") or {}).get("selected")
    sel.update({
        "decision": {"selected": dst.name, "by": "オーナー（Suno でいいね → ダウンロード）", "at": datetime.now().isoformat(timespec="seconds")},
        "takes": [{"take": dst.name, "path": str(dst)}], "source": str(f),
    })
    sel.pop("master", None)
    sel_p.write_text(json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8")
    st = _songs.state(song)
    st.update({"status": "imported", "take": dst.name, "source": str(f), "imported_at": sel["decision"]["at"]})
    _songs.save_state(song, st)
    print(f"   ○ {song['artist']}「{song['title']}」のテイクとして取り込みました：{f.name}" + (f"（前の {prev} から差し替え）" if prev and prev != dst.name else ""))


def advance(song: dict) -> str:
    """取り込んだ曲を、できるところまで進める。戻り値は新しい状態"""
    st = _songs.state(song)
    if st.get("status") in DONE:
        return st["status"]
    tdir = OUT / "takes" / song["key"]
    sel_p = tdir / "selection.json"
    if not sel_p.exists():
        return ""
    sel = read_json(sel_p)
    take = tdir / sel["decision"]["selected"]
    master_p = OUT / "masters" / song["key"] / "master.wav"
    # 音量
    if not (sel.get("master") or {}).get("ok") or not master_p.exists():
        if not shutil.which("ffmpeg"):
            st["status"] = "waiting_ffmpeg"
            _songs.save_state(song, st)
            print("   － 音量を整える道具（ffmpeg）がありません。ターミナルで  brew install ffmpeg  を 1 回実行してください")
            return st["status"]
        from master_track import master
        step(f"{song['artist']}「{song['title']}」の音量を配信向けに整えています（-14 LUFS / -1 dBTP）")
        try:
            rep = master(take, master_p)
        except SystemExit as e:
            print(f"   ✕ 音量の調整に失敗しました：{e}")
            return st.get("status", "imported")
        (master_p.with_suffix(".report.json")).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        sel["master"] = {"path": str(master_p), "ok": rep["ok"], "lufs": rep["after"].get("lufs"),
                         "true_peak_db": rep["after"].get("true_peak_db")}
        sel_p.write_text(json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8")
    # ジャケット
    cover = _songs.cover_source(song)
    if cover is None and song["kind"] == "debut" and os.environ.get("OPENAI_API_KEY"):
        step(f"{song['artist']} のジャケットがまだ無いので作っています（kit_images.py）")
        subprocess.run([sys.executable, str(S / "kit_images.py"), "--kit", str(song["kit"]), "--only", song["slug"], "--kind", "cover"])
        cover = _songs.cover_source(song)
    if cover is None:
        st["status"] = "waiting_cover"
        _songs.save_state(song, st)
        print("   － ジャケットがまだありません（デビュー曲は kit_images.py、毎週の曲は run_week.py の仕上げで作られます）")
        return st["status"]
    # DistroKid の下書きと歌詞
    write_draft(song, st)
    st["status"] = "ready"
    st["ready_at"] = st.get("ready_at") or datetime.now().isoformat(timespec="seconds")
    _songs.save_state(song, st)
    return "ready"


def publish_folder(song: dict) -> tuple[Path, str, str]:
    """公開セットのフォルダ（ローカル保存）と、音源・ジャケットのファイル名"""
    folder = archive.local_folder(song["artist"], "music", _songs.folder_name(song))
    stem = _songs.cover_stem(song)
    cover = _songs.cover_source(song)
    cover_name = archive.numbered(folder, stem, cover) if cover else ""
    return folder, f"{stem}_master.wav", cover_name


def draft_fields(song: dict) -> dict:
    a = load_artist(song["slug"], song.get("label_slug")) or {}
    from _common import load_label
    label = load_label(song.get("label_slug"))
    folder, audio, cover = publish_folder(song)
    if song["kind"] == "debut" or not song.get("release_at"):
        when = "いちばん早い日（登録する日。DistroKid の画面で選べる最短の日付）"
    else:
        when = str(song["release_at"])[:10]
    lang = {"en": "English", "ja": "Japanese", "none": "Instrumental"}.get(song.get("language"), song.get("language") or "English")
    genres = song.get("genre") or []
    return {
        "曲 ID": song["id"], "アカウント": ((label or {}).get("accounts") or {}).get("distrokid_email") or "本体アカウント（EtherArchi）",
        "Artist / Band name": song["artist"], "Spotify のアーティスト": ((a.get("distribution") or {}).get("spotify_uri") or "初回は「新しいアーティスト」"),
        "Featured artist": "なし", "Song title": song["title"], "Release date": when,
        "Language": lang, "Instrumental": "はい" if song.get("instrumental") else "いいえ",
        "Primary genre": genres[0] if genres else "", "Secondary genre": genres[1] if len(genres) > 1 else "",
        "Explicit lyrics": "No（Clean）", "AI の申告": "はい（歌詞・ボーカル・演奏・作曲のうち、使ったものすべて）",
        "Songwriter（本名）": os.environ.get("OWNER_LEGAL_NAME") or "（.env の OWNER_LEGAL_NAME に本名を入れると自動で入ります）",
        "音源ファイル": str(folder / audio), "ジャケット": str(folder / cover) if cover else "（まだ）",
        "歌詞": f"{_songs.cover_stem(song)}_歌詞.txt（同じフォルダ。Lyrics を提出する場合）",
        "ストア": "すべて（DistroKid の初期設定のまま）", "ISRC": "空欄（DistroKid が自動で付ける）",
    }


def write_draft(song: dict, st: dict) -> None:
    d = PUB / song["key"]
    d.mkdir(parents=True, exist_ok=True)
    fields = draft_fields(song)
    (d / "DistroKid下書き.json").write_text(json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# DistroKid の下書き：{song['artist']}「{song['title']}」（{song['id']}）", "",
          "Claude がブラウザでこの内容を入れます。最後の登録ボタンだけ、オーナーが内容を確かめてから押してください。", ""]
    md += [f"- {k}：`{v}`" for k, v in fields.items()]
    md += ["", "## 登録したあと", "", f"- `python3 scripts/publish.py registered {song['id']}`（配信日を変えたときは --date 2026-11-04 のように）",
           "- 配信が始まると、毎日の確認で ISRC・UPC・配信 URL が楽曲管理シートに自動で入ります"]
    (d / "DistroKid下書き.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    if song.get("lyrics"):
        (d / "歌詞.txt").write_text(song["lyrics"].strip() + "\n", encoding="utf-8")


def store_set(song: dict) -> None:
    """公開セットをローカル保存（と、あれば SSD）にまとめる"""
    subprocess.run([sys.executable, str(S / "archive_sync.py"), "--only", song["slug"]], capture_output=True, text=True)
    folder, audio, cover = publish_folder(song)
    print(f"   ○ 公開セット：{archive.local_label(folder)}/")


def cmd_scan(args) -> int:
    songs = [s for s in _songs.all_songs() if s.get("title") and _songs.state(s).get("status") not in DONE]
    files = new_files()
    reg = read_json(REGISTRY) if REGISTRY.exists() else {}
    picked: dict[str, tuple[dict, Path]] = {}
    for f in files:
        song, why = match(f, songs)
        if song is None:
            print(f"   ？ {f.name}：{why}")
            reg[_sig(f)] = {"song": None, "why": why, "at": datetime.now().isoformat(timespec="seconds")}
            continue
        cur = picked.get(song["key"])
        if cur is None or f.stat().st_mtime > cur[1].stat().st_mtime:   # 同じ曲が 2 本あれば新しい方
            picked[song["key"]] = (song, f)
        reg[_sig(f)] = {"song": song["id"], "at": datetime.now().isoformat(timespec="seconds")}
    for song, f in picked.values():
        take_in(song, f)
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    # 取り込んだ曲・待っている曲を進める
    ready = []
    for song in songs:
        st = _songs.state(song)
        if st.get("status") in ("imported", "waiting_ffmpeg", "waiting_cover") or song["key"] in picked:
            now = advance(song)
            store_set(song)   # 音源ができた時点で、公開セットのフォルダにも入れておく（SSD が無くても Mac に残る）
            if now == "ready":
                ready.append(song)
    for song in ready:
        notify("公開の準備ができました", f"{song['artist']}「{song['title']}」を DistroKid に登録できます")
        print(f"▶ {song['artist']}「{song['title']}」は DistroKid に登録できます（python3 scripts/publish.py draft {song['id']}）")
    if picked or ready:
        _book()
    if not files and not args.quiet:
        print(f"－ 「{inbox()}」に新しい曲はありませんでした")
    return 0


def _book() -> None:
    try:
        import song_book
        song_book.update(quiet=True)
    except Exception as e:  # noqa: BLE001  シートが書けなくても公開の準備は止めない
        print(f"   － 楽曲管理シートは更新できませんでした：{e}")


# ---------------------------------------------------------------------------
def cmd_status(args) -> int:
    print(f"見張っているフォルダ：{inbox()}")
    for s in _songs.all_songs():
        if str(s["week"]).startswith("2000-"):
            continue
        st = _songs.state(s)
        extra = f"  ISRC {st['isrc']}" if st.get("isrc") else ""
        print(f"  {s['id']:<26} {s['artist']:<16} {s['title'] or '（曲名未定）':<22} {STATUS_JA.get(st.get('status', ''), st.get('status'))}{extra}")
    return 0


def cmd_draft(args) -> int:
    song = _songs.by_id(args.id)
    if song is None:
        sys.exit(f"[エラー] 曲 {args.id} が見つかりません（publish.py status で曲 ID を確かめてください）")
    p = PUB / song["key"] / "DistroKid下書き.md"
    if not p.exists():
        write_draft(song, _songs.state(song))
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_registered(args) -> int:
    song = _songs.by_id(args.id)
    if song is None:
        sys.exit(f"[エラー] 曲 {args.id} が見つかりません")
    day = date.fromisoformat(args.date) if args.date else date.today()
    st = _songs.state(song)
    st.update({"status": "registered", "registered_at": date.today().isoformat(), "release_date": day.isoformat()})
    _songs.save_state(song, st)
    print(f"○ {song['artist']}「{song['title']}」を DistroKid 登録済みにしました（配信日 {day}）")
    if song["kind"] == "debut":
        a = load_artist(song["slug"])
        a["debut_week"] = monday_of(day).isoformat()   # 毎週の制作は、デビュー曲の配信週から数えて始まる
        save_artist(a)
        print(f"   デビュー週を {a['debut_week']} にしました（2 曲目以降の毎週の制作がここから始まります）")
    _book()
    return 0


# ---------------------------------------------------------------------------
# Spotify で配信を確かめ、ISRC などを記録する
def spotify_token() -> str | None:
    cid, sec = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not sec:
        return None
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials",
                                 headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode(),
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def spotify_get(token: str, path: str) -> dict:
    req = urllib.request.Request("https://api.spotify.com/v1/" + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def find_on_spotify(token: str, song: dict) -> dict | None:
    q = urllib.parse.quote(f'track:"{song["title"]}" artist:"{song["artist"]}"')
    items = spotify_get(token, f"search?q={q}&type=track&limit=20").get("tracks", {}).get("items", [])
    for t in items:
        if _songs.norm(t.get("name")) == _songs.norm(song["title"]) and \
                any(_songs.norm(x.get("name")) == _songs.norm(song["artist"]) for x in t.get("artists", [])):
            found = {"isrc": (t.get("external_ids") or {}).get("isrc", ""), "url": (t.get("external_urls") or {}).get("spotify", ""),
                     "released": (t.get("album") or {}).get("release_date", ""), "spotify_track_id": t.get("id")}
            try:
                alb = spotify_get(token, f"albums/{t['album']['id']}")
                found["upc"] = (alb.get("external_ids") or {}).get("upc", "")
            except Exception:  # noqa: BLE001  アルバムの情報が取れなくても ISRC と URL は残す
                found["upc"] = ""
            return found
    return None


def cmd_track(args) -> int:
    waiting = [s for s in _songs.all_songs() if _songs.state(s).get("status") == "registered"]
    if not waiting:
        if not args.quiet:
            print("－ 配信待ちの曲はありません")
        return 0
    try:
        token = spotify_token()
    except Exception as e:  # noqa: BLE001
        print(f"－ Spotify につながりませんでした：{e}")
        return 0
    if not token:
        print("－ Spotify の鍵（SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET）が .env にないので、配信の確認を飛ばしました")
        return 0
    changed = False
    for song in waiting:
        try:
            found = find_on_spotify(token, song)
        except Exception as e:  # noqa: BLE001
            print(f"   － {song['artist']}「{song['title']}」：確認できませんでした（{e}）")
            continue
        if not found:
            print(f"   ・{song['artist']}「{song['title']}」：まだ Spotify に出ていません")
            continue
        st = _songs.state(song)
        st.update({"status": "live", **found, "live_checked_at": date.today().isoformat()})
        _songs.save_state(song, st)
        changed = True
        print(f"   ○ {song['artist']}「{song['title']}」が配信中になりました。ISRC {found['isrc']}・UPC {found.get('upc') or '—'}")
        notify("配信が始まりました", f"{song['artist']}「{song['title']}」 ISRC {found['isrc']}")
    if changed:
        _book()
    return 0


# ---------------------------------------------------------------------------
# Suno に貼る内容のページ
def suno_page() -> tuple[str, int]:
    """Suno に貼る内容のページ（HTML）と曲数。webapp.py も使う"""
    todo = [s for s in _songs.all_songs() if s.get("title") and s.get("style") and not str(s["week"]).startswith("2000-")
            and not _songs.state(s).get("status")]
    E = html.escape
    cards = []
    for i, s in enumerate(todo):
        fields = [("曲名（Title）", s["title"]), ("スタイル（Style of Music）", s["style"]),
                  ("入れないもの（Exclude Styles）", s.get("exclude", "")), ("歌詞（Lyrics）", "" if s.get("instrumental") else s.get("lyrics", ""))]
        rows = "".join(
            f'<div class="f"><div class="h"><span>{E(k)}</span><button onclick="cp(\'t{i}_{j}\', this)">コピー</button></div>'
            f'<textarea id="t{i}_{j}" readonly rows="{(1, 4, 2, 14)[j]}">{E(v)}</textarea></div>'
            for j, (k, v) in enumerate(fields) if v)
        cards.append(f'<section><h2>{E(s["artist"])}「{E(s["title"])}」<small>{E(s["id"])}</small></h2>{rows}</section>')
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Suno に貼る内容</title><style>
:root{{--bg:#f7f5f1;--fg:#222;--card:#fff;--line:#ddd;--accent:#7a5c2e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1a18;--fg:#eee;--card:#262422;--line:#444;--accent:#d9b27a}}}}
body{{background:var(--bg);color:var(--fg);font-family:-apple-system,"Hiragino Sans",sans-serif;margin:0;padding:16px;line-height:1.6}}
main{{max-width:860px;margin:auto}} section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}}
h2 small{{font-weight:normal;opacity:.6;margin-left:8px;font-size:.7em}} textarea{{width:100%;box-sizing:border-box;background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px;font:inherit}}
.h{{display:flex;justify-content:space-between;align-items:center;margin-top:10px}} button{{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 14px;cursor:pointer}}
ol{{padding-left:1.2em}}</style></head><body><main>
<h1>Suno に貼る内容（{len(todo)} 曲）</h1>
<ol><li>Suno の Create 画面で「Custom」を選び、下の欄を「コピー」して同じ名前の欄に貼る</li>
<li>「Create」を押す（押すのはオーナーだけ）</li>
<li>できた 2 つのテイクを聴き、気に入った方に「いいね」して、その 1 本をダウンロードする（WAV がおすすめ）</li>
<li>ダウンロードしたファイルを「ダウンロード → SUNO楽曲」に入れる。ここから先は自動で公開の準備まで進みます</li></ol>
{''.join(cards) or '<p>いま Suno で作る曲はありません。</p>'}
</main><script>function cp(id,b){{const t=document.getElementById(id);const done=()=>{{b.textContent='コピーしました';setTimeout(()=>b.textContent='コピー',1500)}};
if(navigator.clipboard&&window.isSecureContext){{navigator.clipboard.writeText(t.value).then(done)}}else{{t.removeAttribute('readonly');t.select();document.execCommand('copy');t.setAttribute('readonly','');done()}}}}</script></body></html>"""
    return page, len(todo)


def cmd_suno(args) -> int:
    page, n = suno_page()
    p = PUB / "suno.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(page, encoding="utf-8")
    print(f"▶ Suno に貼る内容のページ：{p}（{n} 曲）")
    if args.open and shutil.which("open"):
        subprocess.run(["open", str(p)])
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Suno の曲を取り込み、DistroKid の登録ボタンを押すだけの状態まで進める")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("scan", help="「SUNO楽曲」の新しい曲を取り込んで公開の準備まで進める")
    p.add_argument("--quiet", action="store_true")
    sub.add_parser("status", help="曲ごとの進み具合")
    p = sub.add_parser("draft", help="DistroKid の下書きを表示")
    p.add_argument("id")
    p = sub.add_parser("registered", help="DistroKid に登録したことを記録")
    p.add_argument("id")
    p.add_argument("--date", help="配信日（YYYY-MM-DD。省くと今日）")
    p = sub.add_parser("track", help="配信が始まったかを Spotify で確かめ、ISRC などを記録")
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("suno", help="Suno に貼る内容のページを作る")
    p.add_argument("--open", action="store_true", help="ブラウザで開く")
    args = ap.parse_args()
    fn = {"scan": cmd_scan, "status": cmd_status, "draft": cmd_draft, "registered": cmd_registered,
          "track": cmd_track, "suno": cmd_suno}.get(args.cmd)
    if fn is None:
        ap.print_help()
        return
    sys.exit(fn(args))


if __name__ == "__main__":
    main()

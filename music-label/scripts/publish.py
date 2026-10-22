#!/usr/bin/env python3
"""
公開の流れ：Suno で「いいね」してダウンロードした曲を、DistroKid の登録ボタンを押すだけの状態まで自動で進める

オーナーがやること（Suno と DistroKid は規約上、機械が勝手に操作しない。docs/12_decisions.md の 17）：
  1. Suno の入力欄（曲名・スタイル・歌詞）は仕組みが入れる：ダッシュボードの「Suno に入力する」（browser_fill.py suno）で
     専用のブラウザが開き、欄を埋めて止まる（決定 22）。内容を確かめて「Create」を押すのはオーナーだけ。
     自分で貼るときは、`publish.py suno` のページのコピーボタンから
  2. できたテイクを聴き、気に入った方に「いいね」して、その 1 本をダウンロードして
     SSD の「Postdog Records/楽曲情報」に入れる（Suno のアプリの保存先をここにする。SSD が無いときは「ダウンロード/SUNO楽曲」）。
     WAV がおすすめ。ファイル名は Suno が付けた曲名のままでよい
  3. ダッシュボードの「DistroKid に入力する」（browser_fill.py distrokid）で下書きが入るので、確かめて最後の登録ボタンを押す

ここから先は自動（Mac の定期実行が「楽曲情報」と「SUNO楽曲」を見張っていて、曲が入ると数分以内に動く）：
  ・ファイル名（とフォルダ名）から、どの曲かを見分ける（曲名・曲名の別案・アーティスト名）
  ・選んだテイクとして記録し、音量を配信向けに整える（-14 LUFS / -1 dBTP・44.1kHz / 24bit の WAV）
  ・ジャケットを用意する（デビュー曲で無ければ自動で作る）
  ・DistroKid の下書き（入力する項目の一覧）と歌詞を作る
  ・公開セット（ローカル保存（一時保存）/<アーティスト名>/04_楽曲/<曲名>/）にまとめ、SSD があれば SSD にも写す
  ・楽曲管理シートのフェーズを進め、Mac に「登録できます」と知らせる
  ・登録したあと：毎日 Spotify を確かめ、配信が始まったら ISRC・UPC・配信 URL を自動で記録し、フェーズを「8 配信中」にする

使い方：
  python3 scripts/publish.py suno [--open]          # Suno に貼る内容のページを作る（--open でブラウザで開く）
  python3 scripts/publish.py suno-request [曲 ID]   # （予備）Claude in Chrome に Suno の入力を頼むときの文。ふだんは browser_fill.py
  python3 scripts/publish.py scan                   # 「楽曲情報」「SUNO楽曲」に入った曲を取り込んで、公開の準備まで進める（定期実行でも動く）
  python3 scripts/publish.py status                 # 曲ごとの進み具合
  python3 scripts/publish.py draft PDR-001          # DistroKid の下書きを表示（browser_fill.py が入力に使う）
  python3 scripts/publish.py registered PDR-001     # DistroKid に登録した（配信日は --date。省くと今日＝いちばん早い日）
  python3 scripts/publish.py track                  # 登録済みの曲が配信されたかを Spotify で確かめ、ISRC などを記録

鍵（music-label/.env。会話には貼らない）：
  SPOTIFY_CLIENT_ID・SPOTIFY_CLIENT_SECRET … 配信の確認と ISRC の自動記録（Spotify for Developers で無料で作れる）
  OWNER_LEGAL_NAME                          … DistroKid の作詞作曲者の欄に入れる本名（手元だけ。GitHub には入らない）
  SUNO_INBOX                                … 見張るフォルダを変えるとき（既定：SSD の Postdog Records/楽曲情報 と ダウンロード/SUNO楽曲）
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
def inboxes() -> list[Path]:
    """見張るフォルダ（上から順に）：
      1. SSD の Postdog Records/楽曲情報（Suno のアプリの保存先。SSD がつながっているとき）
      2. ダウンロード/SUNO楽曲（予備。SSD が無いときにダウンロードしたもの）
    .env の SUNO_INBOX（; で区切って複数可）を書くと、そちらだけを見る"""
    env = os.environ.get("SUNO_INBOX", "").strip()
    if env:
        return [Path(x.strip()).expanduser() for x in env.split(";") if x.strip()]
    out = []
    base, _ = archive.root()
    if base is not None:
        out.append(archive.child(base, archive.INBOX_DIR))
    out.append(archive.child(archive._mkdir(Path.home() / "Downloads"), archive.LOCAL_PARENT))
    return out


def inbox() -> Path:
    """いちばん目の見張るフォルダ（表示用）"""
    return inboxes()[0]


def move_to_ssd() -> list[str]:
    """SSD が外れていた間に予備のフォルダ（ダウンロード/SUNO楽曲）へ入った曲を、SSD の「楽曲情報」へ移す。
    中のフォルダの並びはそのまま。取り込み済みの曲は、移したあとも取り込み済みとして扱う（取り込み直さない）。表示用の行を返す"""
    if os.environ.get("SUNO_INBOX", "").strip():
        return []   # 見張る場所を自分で決めているときは動かさない
    base, _ = archive.root()
    downloads = Path.home() / "Downloads"
    src = archive.find_child(downloads, archive.LOCAL_PARENT) if downloads.is_dir() else None
    if base is None or src is None:
        return []
    dst_root = archive.child(base, archive.INBOX_DIR)
    skip = {_songs.norm(archive.LOCAL_NAME), _songs.norm("取り込み済み")}
    reg = read_json(REGISTRY) if REGISTRY.exists() else {}
    lines = []
    try:
        files = sorted(f for f in src.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO and not f.name.startswith((".", "~")))
    except PermissionError:
        return [f"✕ 「{src}」を読む許可がありません（フルディスクアクセスに python3 を追加）"]
    for f in files:
        rel = f.relative_to(src)
        if any(_songs.norm(p) in skip for p in rel.parts[:-1]) or time.time() - f.stat().st_mtime < 15:
            continue
        folder = dst_root
        for part in rel.parts[:-1]:
            folder = archive.child(folder, part)
        target = folder / f.name
        if target.exists():
            import filecmp
            if filecmp.cmp(f, target, shallow=False):
                f.unlink()   # SSD に同じものがある：予備のほうを消すだけ
                lines.append(f"○ {rel}（SSD に同じものがあるので、予備のほうを片付けました）")
                continue
            n = 2
            while target.exists():
                target = folder / f"{f.stem}_{n}{f.suffix}"
                n += 1
        info = reg.get(_sig(f))
        size = f.stat().st_size
        shutil.move(str(f), str(target))
        if target.stat().st_size != size:
            lines.append(f"✕ {rel} を移せませんでした（大きさが合わない）")
            continue
        if info is not None:
            reg[_sig(target)] = {**info, "moved_from": str(f)}
        for s in _songs.all_songs():   # 取り込み記録の元の場所も書き換える
            st = _songs.state(s)
            if st.get("source") == str(f):
                st["source"] = str(target)
                _songs.save_state(s, st)
        lines.append(f"○ {rel} → SSD の {archive.INBOX_DIR}/{target.relative_to(dst_root)}")
    if lines:
        REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    return lines


def new_files() -> list[Path]:
    """見張るフォルダの中の、まだ取り込んでいない音源（ローカル保存の中・ダウンロード途中のものは除く）"""
    seen = read_json(REGISTRY) if REGISTRY.exists() else {}
    skip_dirs = {_songs.norm(archive.LOCAL_NAME), _songs.norm("取り込み済み")}
    out = []
    for box in inboxes():
        if not box.is_dir():
            continue
        try:
            listing = sorted(box.rglob("*"))
        except PermissionError:
            print(f"   ✕ 「{box}」を読む許可がありません。システム設定 → プライバシーとセキュリティ → フルディスクアクセス に"
                  " python3（/usr/bin/python3 など）を追加してください")
            continue
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
    day = made_on(f)
    stem = _songs.audio_stem(song, day)
    f = rename_in_inbox(f, stem)
    dst = tdir / f"{stem}{f.suffix.lower()}"
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
    st.update({"status": "imported", "take": dst.name, "source": str(f), "imported_at": sel["decision"]["at"], "generated_on": day})
    _songs.save_state(song, st)
    print(f"   ○ {song['artist']}「{song['title']}」のテイクとして取り込みました：{f.name}" + (f"（前の {prev} から差し替え）" if prev and prev != dst.name else ""))


def made_on(f: Path) -> str:
    """ファイルが作られた日（Suno でダウンロードした日＝生成日として使う。Mac は作成日、無ければ更新日）"""
    st = f.stat()
    ts = getattr(st, "st_birthtime", None) or st.st_mtime
    return date.fromtimestamp(ts).isoformat()


def rename_in_inbox(f: Path, stem: str) -> Path:
    """取り込んだファイルの名前を <曲名>_<アーティスト名>_<生成日> にそろえる（同じフォルダの中で名前だけ変える）"""
    new = f.with_name(f"{stem}{f.suffix.lower()}")
    if new == f:
        return f
    n = 2
    while new.exists():
        new = f.with_name(f"{stem}_{n}{f.suffix.lower()}")
        n += 1
    try:
        f.rename(new)
    except OSError:
        return f
    reg = read_json(REGISTRY) if REGISTRY.exists() else {}
    reg[_sig(new)] = {"renamed_from": f.name, "at": datetime.now().isoformat(timespec="seconds")}
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"   ○ 名前をそろえました：{f.name} → {new.name}")
    return new


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
    return folder, f"{_songs.audio_stem(song)}.wav", cover_name


def draft_fields(song: dict) -> dict:
    a = load_artist(song["slug"], song.get("label_slug")) or {}
    from _common import load_label
    label = load_label(song.get("label_slug"))
    folder, audio, cover = publish_folder(song)
    if not (folder / audio).exists():   # ローカル保存から片付けたあと：SSD の公開セットを指す
        sf, _ = archive.artist_folder(song["artist"], "music", _songs.folder_name(song))
        if sf is not None and (sf / audio).exists():
            folder = sf
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
        "サブジャンル（1 つ目のジャンルの細かい分類）": "その組の音に近いものを自動で選ぶ（候補：" + " → ".join(g.title() for g in ((a.get("sound") or {}).get("genres") or [])[:4]) + "。無ければ近いもの）",
        "Artist already in Apple Music / Spotify / YouTube Music / Instagram / Facebook?": (
            "すべて No（デビュー曲：新しいアーティストのページを作る。同じ名前の別人のページに混ざらないように）"
            if not (a.get("distribution") or {}).get("spotify_uri") else
            f"Spotify：Yes（{(a.get('distribution') or {}).get('spotify_uri')}）。ほかはページができていれば Yes とその URL、無ければ No"),
        "Track credits（Apple の決まり）": f"Performer：{song['artist']}（役割 Vocals）／Producer：{os.environ.get('OWNER_LEGAL_NAME') or '本名（.env の OWNER_LEGAL_NAME）'}（役割 Producer）。Producer の名前は Apple Music で公開される",
        "Important checkboxes（必須の宣誓）": "Gon さんが文を読んで、本当に当てはまるものを自分でチェックする（システムは押さない）。権利（Suno の Pro で作った曲か）・サンプル不使用・カバーでない・実在の名前を使っていない など。当てはまらない文があれば登録しない",
        "Extras（有料の追加）": "すべてチェックしない（YouTube Content ID は AI の曲だと申し立てのトラブルになりやすいので付けない。必要になったら Gon さんが選ぶ）",
    }


def write_draft(song: dict, st: dict) -> None:
    d = PUB / song["key"]
    d.mkdir(parents=True, exist_ok=True)
    fields = draft_fields(song)
    (d / "DistroKid下書き.json").write_text(json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# DistroKid の下書き：{song['artist']}「{song['title']}」（{song['id']}）", "",
          "ダッシュボードの「DistroKid に入力する」で、この内容がブラウザに自動で入ります。最後の登録ボタンだけ、オーナーが内容を確かめてから押してください。", ""]
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
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    for song, f in picked.values():
        take_in(song, f)   # ファイルの名前を <曲名>_<アーティスト名>_<生成日> にそろえ、登録簿にも足す
    if picked and not getattr(args, "no_open", False):
        try:
            import song_check
            for song, _f in picked.values():
                song_check.open_window(song)   # 公開の準備の確認ページを開く（進み具合が目で見える）
        except Exception as e:  # noqa: BLE001
            print(f"   － 確認ページを開けませんでした：{e}")
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
        print(f"－ 新しい曲はありませんでした（見ているところ：{'、'.join(str(b) for b in inboxes())}）")
    return 0


def _book() -> None:
    try:
        import song_book
        song_book.update(quiet=True)
    except Exception as e:  # noqa: BLE001  シートが書けなくても公開の準備は止めない
        print(f"   － 楽曲管理シートは更新できませんでした：{e}")


# ---------------------------------------------------------------------------
def cmd_status(args) -> int:
    print("見張っているフォルダ：" + "、".join(str(b) for b in inboxes()))
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
SUNO_RULES = """決まり（必ず守る）：
- 「Create」ボタンは絶対に押さない。押すのは私（オーナー）。入力が終わったら止まって「入力しました。確認して Create を押してください」と伝える
- Suno のほかのボタン（削除・公開・共有・設定・購入・プランの変更）も押さない。曲を聴く・いいね・ダウンロードも私がやる
- 1 曲ずつ進める。私が「次」と言ったら次の曲へ
- 欄が見つからない・画面が想定と違うときは、推測で進めずに止まって私に聞く"""


def suno_request(songs: list[dict]) -> str:
    """Mac の Claude デスクトップアプリ（Claude in Chrome をつないだ会話）に貼る頼み文。決定 19：入力は Claude、Create はオーナー"""
    head = ("Claude in Chrome で、Suno の作曲画面に曲の内容を入力してください。\n\n" + SUNO_RULES + "\n\n手順：\n"
            "1. Chrome で https://suno.com/create を開く（私はログイン済み）\n"
            "2. 画面の上の「高度な」（Advanced）に切り替える\n"
            "3. 下の内容を、同じ名前の欄に入れる（前の内容が残っていれば消してから）。画面の上から 歌詞 → スタイル → 除外するスタイル（「その他のオプション」の中）→ 曲のタイトル\n"
            "4. 入れ終わったら止まって、私に伝える\n")
    parts = []
    for n, s in enumerate(songs, 1):
        parts.append(
            f"\n【曲 {n}／{len(songs)}】{s['artist']}「{s['title']}」（{s['id']}）\n"
            f"- Instrumental：{'オン' if s.get('instrumental') else 'オフ'}\n"
            f"- Title：{s['title']}\n- Style of Music：{s['style']}\n"
            + (f"- Exclude Styles：{s['exclude']}\n" if s.get("exclude") else "")
            + (f"- その他のオプション：{_songs.suno_options_text(s)}\n" if _songs.suno_options_text(s) else "")
            + ("" if s.get("instrumental") else f"- Lyrics（ここから）\n{str(s.get('lyrics', '')).strip()}\n- Lyrics（ここまで）\n"))
    return head + "".join(parts)


def distrokid_request(song: dict) -> str:
    """Mac の Claude デスクトップアプリ（Claude in Chrome をつないだ会話）に貼る、DistroKid の下書きの頼み文。最後の登録ボタンはオーナー"""
    f = draft_fields(song)
    rules = ("決まり（必ず守る）：\n"
             "- 最後の登録ボタン（Upload／Done／Submit など、送信して確定するボタン）は絶対に押さない。押すのは私（オーナー）。"
             "入力と添付が終わったら止まって「入力しました。確認して登録ボタンを押してください」と伝える\n"
             "- 支払い・プランの変更・アカウントの設定・ほかの曲の編集や削除はしない\n"
             "- 欄が見つからない・選択肢に同じ言葉が無い・画面が想定と違うときは、推測で進めずに止まって私に聞く\n"
             "- ファイルの添付ができないときは、止まって「どのファイルをどの欄に入れるか」を私に伝える")
    lines = "\n".join(f"- {k}：{v}" for k, v in f.items() if k not in ("曲 ID",))
    return ("Claude in Chrome で、DistroKid に曲の下書きを入れてください。\n\n" + rules +
            "\n\n手順：\n1. Chrome で " + "https://distrokid.com/new/" + " を開く（私はログイン済み）\n"
            "2. 下の内容を上から順に、同じ意味の欄に入れる。Release date は選べるいちばん早い日（デビュー曲）か、書いてある日\n"
            "3. 音源とジャケットのファイルを添付する（場所は下のとおり）\n4. 入れ終わったら止まって、私に伝える\n\n"
            f"【{song['artist']}「{song['title']}」（{song['id']}）】\n" + lines + "\n")


def suno_todo() -> list[dict]:
    """Suno で作る番の曲（曲名とスタイルがあり、まだ取り込んでいないもの）"""
    return [s for s in _songs.all_songs() if s.get("title") and s.get("style") and not str(s["week"]).startswith("2000-")
            and not _songs.state(s).get("status")]


def suno_page() -> tuple[str, int]:
    """Suno に貼る内容のページ（HTML）と曲数。webapp.py も使う"""
    todo = suno_todo()
    E = html.escape
    cards = []
    if todo:
        cards.append('<section><h2>自動で入力する（おすすめ）</h2>'
                     '<p>ダッシュボード（http://localhost:8765）の「作曲待ちの曲を Suno に入力する」を押すと、専用のブラウザが開き、'
                     '下の内容が 1 曲ずつ Suno の欄に自動で入ります。内容を確かめて「Create」を押してください（押すのは Gon さんだけ）。</p>'
                     '<p>ターミナルからなら：<code>python3 scripts/browser_fill.py suno</code></p></section>')
    import suno_design
    for i, s in enumerate(todo):
        d = suno_design.design(s)   # 曲のイメージを Suno の全部の軸に写した設計図
        # Suno の「高度な」の画面の上から順（歌詞 → スタイル → 除外するスタイル → 曲のタイトル）
        fields = [("歌詞（Lyrics）", d["lyrics"]), ("スタイル（Styles）", d["style"]),
                  ("除外するスタイル（Exclude styles・「その他のオプション」の中）", d["exclude"]), ("曲のタイトル（Song Title）", d["title"])]
        rows = "".join(
            f'<div class="f"><div class="h"><span>{E(k)}</span><button onclick="cp(\'t{i}_{j}\', this)">コピー</button></div>'
            f'<textarea id="t{i}_{j}" readonly rows="{(14, 4, 2, 1)[j]}">{E(v)}</textarea></div>'
            for j, (k, v) in enumerate(fields) if v)
        ok, n = suno_design.score(d)
        mark = {"ok": "✓", "warn": "⚠", "info": "＋"}
        table = "".join(f"<tr><td>{E(k)}</td><td>{E(str(v)[:80] + ('…' if len(str(v)) > 80 else ''))}</td><td>{E(src)}</td></tr>" for k, v, src in d["map"])
        checks = "".join(f"<li>{mark[st]} {E(k)}：{E(t)}</li>" for k, st, t in d["checks"])
        design_html = (f'<details><summary>設計図：イメージの写し具合 {ok}／{n}（押すと、どの軸に何を入れたか）</summary>'
                       f'<table class="map"><tr><th>Suno の軸</th><th>入れる値</th><th>イメージのどこから</th></tr>{table}</table>'
                       f'<ul class="chk">{checks}</ul></details>')
        cards.append(f'<section><h2>{E(s["artist"])}「{E(s["title"])}」<small>{E(s["id"])}</small></h2>{rows}{design_html}</section>')
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Suno に貼る内容</title><style>
:root{{--bg:#f7f5f1;--fg:#222;--card:#fff;--line:#ddd;--accent:#7a5c2e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1a18;--fg:#eee;--card:#262422;--line:#444;--accent:#d9b27a}}}}
body{{background:var(--bg);color:var(--fg);font-family:-apple-system,"Hiragino Sans",sans-serif;margin:0;padding:16px;line-height:1.6}}
main{{max-width:860px;margin:auto}} section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0}}
h2 small{{font-weight:normal;opacity:.6;margin-left:8px;font-size:.7em}} textarea{{width:100%;box-sizing:border-box;background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px;font:inherit}}
.h{{display:flex;justify-content:space-between;align-items:center;margin-top:10px}} button{{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:6px 14px;cursor:pointer}}
ol{{padding-left:1.2em}} table.map{{border-collapse:collapse;width:100%;font-size:.85em;margin:8px 0}} .map td,.map th{{border:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}} ul.chk{{font-size:.85em;padding-left:1.2em}} details summary{{cursor:pointer;margin-top:10px;color:var(--accent)}}</style></head><body><main>
<h1>Suno に貼る内容（{len(todo)} 曲）</h1>
<p><b>入力は仕組みが自動でやります</b>（下の「自動で入力する」）。自分で貼るときは：</p>
<ol><li>Suno の作曲画面の上で「高度な」を選び、下の欄を「コピー」して同じ名前の欄に貼る（上から 歌詞 → スタイル → 除外するスタイル〔「その他のオプション」の中〕→ 曲のタイトル）</li>
<li>「Create」を押す（押すのは Gon さんだけ）</li>
<li>できた 2 つのテイクを聴き、気に入った方に「いいね」して、その 1 本をダウンロードする（WAV がおすすめ）</li>
<li>ダウンロードしたファイルを SSD の「Postdog Records → 楽曲情報」に入れる（Suno のアプリの保存先をここにしておく。SSD が無いときは「ダウンロード → SUNO楽曲」）。ここから先は自動で公開の準備まで進みます</li></ol>
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


def cmd_suno_request(args) -> int:
    songs = [_songs.by_id(args.id)] if args.id else suno_todo()
    if not songs or songs[0] is None:
        print("－ Suno で作る番の曲はありません" if not args.id else f"[エラー] 曲 {args.id} が見つかりません")
        return 0
    print(suno_request(songs))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Suno の曲を取り込み、DistroKid の登録ボタンを押すだけの状態まで進める")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("scan", help="「楽曲情報」「SUNO楽曲」の新しい曲を取り込んで公開の準備まで進める")
    p.add_argument("--quiet", action="store_true")
    sub.add_parser("status", help="曲ごとの進み具合")
    p = sub.add_parser("draft", help="DistroKid の下書きを表示")
    p.add_argument("id")
    p = sub.add_parser("registered", help="DistroKid に登録したことを記録")
    p.add_argument("id")
    p.add_argument("--date", help="配信日（YYYY-MM-DD。省くと今日）")
    p = sub.add_parser("track", help="配信が始まったかを Spotify で確かめ、ISRC などを記録")
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("suno-request", help="Claude（デスクトップアプリ＋Claude in Chrome）に Suno の入力を頼む文を表示")
    p.add_argument("id", nargs="?", help="この曲だけ（省くと Suno で作る番の全曲）")
    p = sub.add_parser("suno", help="Suno に貼る内容のページを作る")
    p.add_argument("--open", action="store_true", help="ブラウザで開く")
    args = ap.parse_args()
    fn = {"scan": cmd_scan, "status": cmd_status, "draft": cmd_draft, "registered": cmd_registered,
          "track": cmd_track, "suno": cmd_suno, "suno-request": cmd_suno_request}.get(args.cmd)
    if fn is None:
        ap.print_help()
        return
    sys.exit(fn(args))


if __name__ == "__main__":
    main()

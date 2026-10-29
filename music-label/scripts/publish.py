#!/usr/bin/env python3
"""
公開の流れ：Suno で「いいね」してダウンロードした曲を、DistroKid の登録ボタンを押すだけの状態まで自動で進める

オーナーがやること（Suno と DistroKid は規約上、機械が勝手に操作しない。docs/12_decisions.md の 17）：
  1. Suno の入力欄（曲名・スタイル・歌詞）は仕組みが入れる：ダッシュボードの「Suno に入力する」（browser_fill.py suno。決定 22・106）で
     専用のブラウザが開き、欄を埋めて止まる。内容を確かめて「Create」を押すのはオーナーだけ。
     ロボット確認が続くときの予備：ダッシュボードの「写して貼る」（/paste。ふだんのブラウザに 1 欄ずつ写す。決定 104）
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
  python3 scripts/publish.py back PDR-001           # 間違えて進めた工程を 1 つ前に戻す（--to suno／master／ready／registered で行き先を選ぶ。決定 38）

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
from _common import OUT, ROOT, all_artists, load_artist, load_dotenv, monday_of, notify, read_json, save_artist, step  # noqa: E402

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
    try:   # Suno のダウンロード 1 曲として数える（同じファイルは数えない。決定 105）
        import suno_quota
        suno_quota.log_download(song, dst)
    except Exception:  # noqa: BLE001
        pass
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
    if cover is None and song["kind"] != "debut":
        cover = ensure_weekly_cover(song)   # 毎週の曲・不定期の曲：取り込んで進めるたびに作る（曜日・週を問わない。決定 112）
    if cover is not None:   # ジャケットには必ずアーティスト名と曲名を入れる（入っていなければ入れる。入れられなければ先に進めない。決定 116）
        ok_text, why = ensure_cover_text(song, cover)
        if not ok_text:
            st["status"] = "waiting_cover"
            _songs.save_state(song, st)
            print(f"   － ジャケットに文字を入れられませんでした：{why}")
            return st["status"]
    if cover is None:
        st["status"] = "waiting_cover"
        _songs.save_state(song, st)
        n = len(cover_candidates(song)) if song["kind"] != "debut" else 0
        print("   － ジャケットがまだありません" + (f"（候補 {n} 枚：ダッシュボードのポップアップで 1 枚選んでください）" if n else
              "（OpenAI の鍵が無いか、作れませんでした。ダッシュボードの「ジャケットを作る」でもう一度）"))
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


def ensure_cover_text(song: dict, cover: Path) -> tuple[bool, str]:
    """ジャケットにアーティスト名と曲名が入っているか。入っていなければ入れる（デビュー曲も。決定 116）。(入っているか, 理由)"""
    if not song.get("title") or not song.get("artist"):
        return False, "曲名かアーティスト名がまだ決まっていません（管理表か曲の案で決めてください）"
    try:
        import cover_text
        if cover_text.text_matches(Path(cover), song["artist"], song["title"]):
            return True, ""
        r = cover_text.apply(Path(cover), song["artist"], song["title"], song["slug"])
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:160]}"
    if r.get("ok"):
        print(f"   ○ ジャケットに文字を入れました：{'・'.join(r.get('shown', []))}（{r.get('font')}・{r.get('place')}）")
        return True, ""
    return False, r.get("reason", "")


def _cover_text_note(song: dict) -> str:
    """ジャケットに入れた文字が、登録する曲名・アーティスト名と同じか（決定 36。違うとストアで差し戻される）"""
    src = _songs.cover_source(song)
    if not src:
        return "（ジャケットがまだ）"
    try:
        import cover_text
        m = cover_text.text_matches(Path(src), song["artist"], song["title"])
    except Exception:  # noqa: BLE001
        return "（確かめられませんでした）"
    if m is None:
        return "⚠ 文字なし（次の取り込みで自動で入れます）"
    return "曲名・アーティスト名と同じ（OK）" if m else "⚠ 曲名かアーティスト名が変わっています。ジャケットを仕上げ直してください（finalize_cover.py）"


# 組の音の言葉 → DistroKid のジャンル名の候補（上から順に。実際に選ぶのは、画面の選択肢にあるものだけ。決定 44）
GENRE_MAP = [
    (r"house|techno|disco|club|edm|garage|dance", ["Dance", "Electronic"]),
    (r"synth|electro|retrowave|synthwave|chillwave|idm|electronica", ["Electronic", "Pop"]),
    (r"lo-?fi|downtempo|ambient|chill|drone", ["Electronic", "New Age"]),
    (r"singer|songwriter", ["Singer/Songwriter", "Folk"]),
    (r"folk|acoustic|americana|bluegrass", ["Folk", "Singer/Songwriter"]),
    (r"rock|jangle|shoegaze|grunge|punk", ["Rock", "Alternative"]),
    (r"indie|alternative|alt-", ["Alternative", "Pop"]),
    (r"r&b|rnb|soul|funk|neo-soul", ["R&B/Soul", "Pop"]),
    (r"hip ?hop|rap|trap|boom ?bap", ["Hip Hop/Rap"]),
    (r"jazz|swing|bossa", ["Jazz"]),
    (r"country", ["Country"]),
    (r"pop|sophisti|night pop|city", ["Pop"]),
]
DK_GENRES = OUT / "knowledge" / "distrokid_genres.json"   # 入力のときに読んだ DistroKid の実際の選択肢（browser_fill.py が書く）
# DistroKid の 1 つ目のジャンルの実際の一覧（2026-10-22 の画面の部品の一覧から）。画面で読み直すまでは、これを使う
DK_GENRE_LIST = ["Afrobeat", "Afropop", "Alternative", "Big Band", "Blues", "Children's Music", "Christian/Gospel", "Classical", "Comedy",
                 "Contemporary Latin", "Country", "Dance", "Electronic", "Fitness & Workout", "Folk", "French Pop", "German Folk", "German Pop",
                 "Hip Hop/Rap", "Holiday", "J-Pop", "Jazz", "K-Pop", "Latin", "Latin House", "Latin Jazz", "Latin Urban", "Maskandi", "Metal",
                 "New Age", "Pop", "Pop in Spanish", "Punk", "R&B/Soul", "Reggae", "Rock", "Rock en Español", "Singer/Songwriter", "Soundtrack",
                 "Spoken Word", "Vocal", "World", "Xigaza"]


def dk_options(key: str) -> list[str]:
    """前に DistroKid の画面で読んだ選択肢（genre1 / genre2 / sub:<ジャンル>）。まだ読んでいなければ空"""
    try:
        return list((read_json(DK_GENRES) if DK_GENRES.exists() else {}).get(key) or [])
    except (OSError, ValueError):
        return []


def genre_prefs(words: list[str]) -> list[str]:
    """ジャンルの言葉の並び → DistroKid のジャンル名の候補（重ならない順）"""
    out = []
    for w in words:
        lw = str(w).lower()
        if any(lw == g.lower() for _, gs in GENRE_MAP for g in gs):   # もう DistroKid の名前
            out.append(next(g for _, gs in GENRE_MAP for g in gs if g.lower() == lw))
            continue
        for rx, gs in GENRE_MAP:
            if re.search(rx, lw):
                out += gs
                break
    return list(dict.fromkeys(out))


def _credit_role(artist: dict) -> str:
    """Apple の Performer の役割（DistroKid の役割の一覧の名前のまま）。歌のある組は「Singing & vocals」（オーナーの指示 2026-10-25。決定 62）。
    歌の無い組（声の切れ端・演奏だけ）は Performer"""
    form = str(artist.get("formation") or "")
    sex = str((artist.get("vocal") or {}).get("sex") or "")
    if form == "producer" or sex == "none":
        return "Performer"
    return "Singing & vocals"


def featured_name(song: dict) -> str:
    """客演の組の名前（ブリーフの featured_artist_slug。決定 97）。無ければ「なし」"""
    try:
        b = read_json(song["brief"]) if song.get("brief") else {}
    except (OSError, ValueError):
        b = {}
    g = b.get("featured_artist_slug")
    return ((load_artist(g, missing_ok=True) or {}).get("name") or g) if g else "なし"


def draft_fields(song: dict) -> dict:
    a = load_artist(song["slug"], song.get("label_slug")) or {}
    from _common import load_label
    label = load_label(song.get("label_slug"))
    folder, audio, cover = publish_folder(song)
    if not (folder / audio).exists():   # ローカル保存から片付けたあと：SSD の公開セットを指す
        sf, _ = archive.artist_folder(song["artist"], "music", _songs.folder_name(song))
        if sf is not None and (sf / audio).exists():
            folder = sf
    fixed = str((song.get("daily") or {}).get("date") or "")
    if fixed and fixed > date.today().isoformat():   # 毎日配信の曲：決まった配信日（決定 94）
        when = fixed
    elif song["kind"] == "debut" or not song.get("release_at") or song.get("sprint") or fixed:   # 集中配信の曲・日を過ぎた毎日配信の曲も、いちばん早い日（決定 53）
        when = "いちばん早い日（登録する日。DistroKid の画面で選べる最短の日付）"
    else:
        when = str(song["release_at"])[:10]
    lang = {"en": "English", "ja": "Japanese", "none": "Instrumental"}.get(song.get("language"), song.get("language") or "English")
    # ジャンルは DistroKid の一覧にある名前で（作業セットのジャンル → 組の音の言葉を置き換えた候補。前に読んだ選択肢があれば、その中のものだけ）
    sound_words = list((a.get("sound") or {}).get("genres") or [])
    cands = genre_prefs(list(song.get("genre") or []) + sound_words)
    known = dk_options("genre1") or DK_GENRE_LIST
    genres = [g for g in cands if not known or g in known] or cands
    return {
        "曲 ID": song["id"], "アカウント": ((label or {}).get("accounts") or {}).get("distrokid_email") or "本体アカウント（EtherArchi）",
        "Artist / Band name": song["artist"],
        "Spotify のアーティスト": ((a.get("distribution") or {}).get("spotify_uri") or (artist_links(song["slug"]).get("spotify") or {}).get("id")
                                  or "初回は「新しいアーティスト」"),
        "Featured artist": featured_name(song), "Song title": song["title"], "Release date": when,
        "Language": lang, "Instrumental": "はい" if song.get("instrumental") else "いいえ",
        "Primary genre": genres[0] if genres else "", "Secondary genre": genres[1] if len(genres) > 1 else "",
        "Explicit lyrics": "No（Clean）", "AI の申告": "はい（歌詞・ボーカル・演奏・作曲のうち、使ったものすべて）",
        "Songwriter（本名）": os.environ.get("OWNER_LEGAL_NAME") or "（.env の OWNER_LEGAL_NAME に本名を入れると自動で入ります）",
        "音源ファイル": str(folder / audio), "ジャケット": str(folder / cover) if cover else "（まだ）",
        "歌詞": "登録のあと、DistroKid の歌詞の画面に 歌詞_配信用.txt（配信サイトの決まりに合わせて作り直したもの）を自動で入れる"
                if not song.get("instrumental") and song.get("lyrics") else "なし（インスト）",
        "ストア": "すべて（DistroKid の初期設定のまま）", "ISRC": "空欄（DistroKid が自動で付ける）",
        "サブジャンル（1 つ目のジャンルの細かい分類）": "その組の音に近いものを自動で選ぶ（候補：" + " → ".join(g.title() for g in ((a.get("sound") or {}).get("genres") or [])[:4]) + "。無ければ近いもの）",
        "Artist already in Apple Music / Spotify / YouTube Music / Instagram / Facebook?": (
            "すべて No（デビュー曲：新しいアーティストのページを作る。同じ名前の別人のページに混ざらないように）"
            if not ((a.get("distribution") or {}).get("spotify_uri") or artist_links(song["slug"]).get("spotify")) else
            f"Spotify：Yes（Artist ID {(a.get('distribution') or {}).get('spotify_uri') or artist_links(song['slug'])['spotify']['id']}）。"
            "ほかの配信先（Apple Music・YouTube Music など）も、デビュー曲で作られたページがあれば Yes とその URL・ID、無ければ No"),
        "Track credits（Apple の決まり）": (f"Performer：本名（インストの組。役割 Performer）" if song.get("instrumental") or (a.get("lyrics") or {}).get("mode") == "instrumental"
                                       else f"Performer：{song['artist']}（役割 {_credit_role(a)}）") + "／Producer：本名（.env の OWNER_LEGAL_NAME。全組共通・役割 Producer）。Apple Music で公開される",
        "Important checkboxes（必須の宣誓）": "Gon さんが文を読んで、本当に当てはまるものを自分でチェックする（システムは押さない）。権利（Suno の Pro で作った曲か）・サンプル不使用・カバーでない・実在の名前を使っていない など。当てはまらない文があれば登録しない",
        "ジャケットの文字": _cover_text_note(song),
        "アーティスト写真": "DistroKid の登録画面には欄が無い。配信が始まってアーティストのページができたら、ダッシュボードの「アーティスト写真」から Spotify・Apple Music・Amazon Music に入れる（決定 32）",
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
        (d / "歌詞.txt").write_text(song["lyrics"].strip() + "\n", encoding="utf-8")   # Suno に入れた歌詞（札・指示つき）
        try:   # 歌詞の日本語訳（対訳。Gon さんが読む用で、DistroKid には入れない。決定 107）
            import lyrics_ja
            tr = lyrics_ja.for_song(song, quiet=True)
            if tr:
                (d / "歌詞_日本語訳.txt").write_text(lyrics_ja.pair_text(song["lyrics"], tr), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"   － 歌詞の日本語訳を作れませんでした（{e}）")
        try:   # 配信サイトに出す歌詞（札・指示を外し、決まりに合わせたもの。決定 52）
            import store_lyrics
            store_lyrics.write_for_song(song)
        except Exception as e:  # noqa: BLE001
            print(f"   － 配信用の歌詞を作れませんでした（{e}）")
    ep = song.get("episode") or {}
    if ep:   # この曲のエピソード（決定 33）。曲の紹介・SNS・インタビューの元に
        li = lambda xs: "、".join(xs) if isinstance(xs, list) else str(xs or "")  # noqa: E731
        lines = [f"# {song['artist']}「{song['title']}」のエピソード", "", ep.get("story_ja", ""), ""]
        lines += [f"- {k}：{v}" for k, v in (("テーマ", ep.get("theme")), ("いつ・どこで", ep.get("when_where")),
                                              ("見たもの", li(ep.get("saw"))), ("聞いたもの", li(ep.get("heard"))),
                                              ("考えたこと", ep.get("thought")), ("感じたこと", ep.get("felt")),
                                              ("そして思ったこと", ep.get("realized")), ("生まれたもと", ep.get("source"))) if v]
        if ep.get("story_en"):
            lines += ["", "## English", "", ep["story_en"]]
        (d / "エピソード.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    import take_check   # 作曲の時点の質の確認：足りなければ案を自動で直して作曲待ちに戻す（決定 100）
    for key in [k for k, (song, _f) in picked.items() if take_check.gate(song) == "retake"]:
        picked.pop(key)
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
        if st.get("status") in ("imported", "waiting_ffmpeg", "waiting_cover") or (song["key"] in picked and st.get("status")):
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


def cover_dir(song: dict) -> Path:
    """毎週の曲（不定期の曲も）のジャケットの候補・選択・仕上がりの置き場所"""
    return OUT / "visuals" / song["slug"] / "cover" / song["week"]


COVER_RETRY_HOURS = 24   # 候補づくりに失敗したとき、次に自動で試すまでの時間（1 日 1 回。10 分ごとに料金のかかる作り直しをしないため。決定 113）


def ensure_weekly_cover(song: dict) -> Path | None:
    """毎週の曲・不定期の曲のジャケット（決定 73・112）。
    候補が無ければ作る（generate_visuals.py。OpenAI の画像の料金）→ 選んであれば 3000×3000 に仕上げる。
    最初の 5 曲は Gon さんがポップアップで選ぶ（選ぶまでは「ジャケット待ち」）。6 曲目からは自動で選ばれる。
    いままでは run_week.py の仕上げ（火〜木・今週の曲だけ）でしか作らず、ほかの曜日・前の週・不定期の曲が止まっていた"""
    d = cover_dir(song)
    if not cover_candidates(song):   # 画像が 1 枚も無ければ作る（失敗しても候補の記録だけ残るので、ファイルの有無では見ない）
        mark = d / "auto_attempt.json"
        try:
            last = datetime.fromisoformat(read_json(mark).get("at", "")) if mark.exists() else None
        except (ValueError, OSError):
            last = None
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        if last and (datetime.now() - last).total_seconds() < COVER_RETRY_HOURS * 3600:
            return None   # さっき試してだめだった：しばらく待つ
        d.mkdir(parents=True, exist_ok=True)
        mark.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
        step(f"{song['artist']}「{song['title']}」のジャケットの候補を作っています（数分。OpenAI の画像の料金がかかります）")
        lab = ["--label", song["label_slug"]] if song.get("label_slug") not in (None, "", "drive") else []
        subprocess.run([sys.executable, str(S / "generate_visuals.py"), "--artist", song["slug"], *lab, "--kind", "cover",
                        "--brief", str(song["brief"])])
    if (d / "selection_cover.json").exists() and not (d / "cover_final.jpg").exists():
        step(f"{song['artist']}「{song['title']}」の選んだジャケットを 3000×3000 に仕上げています")
        subprocess.run([sys.executable, str(S / "finalize_cover.py"), str(song["brief"])])
    return _songs.cover_source(song)


def cover_candidates(song: dict) -> list[dict]:
    """ジャケットの候補（画像ができているもの）を点の高い順に。{no, file(Path), total, note, ok}"""
    d = cover_dir(song)
    p = d / "candidates.json"
    if song["kind"] == "debut" or not p.exists():
        return []
    out = []
    for c in read_json(p):
        f = d / c["file"] if c.get("kind") == "cover" and c.get("file") else None
        if f is not None and f.exists():
            pv = d / c["preview"] if c.get("preview") else None   # アーティスト名と曲名を入れた見本（決定 122）
            out.append({"no": c["no"], "file": pv if pv and pv.exists() else f, "total": c.get("total"), "ok": c.get("compliance_ok") is not False,
                        "note": c.get("claude_note") or c.get("comment_ja") or "", "scene_ja": c.get("scene_ja", ""),
                        "shot": c.get("shot", ""), "who": c.get("who", ""), "type_ja": c.get("type_ja", ""),
                        "look_ja": c.get("look_ja", "")})
    return sorted(out, key=lambda c: (not c["ok"], -(c["total"] or 0)))


def cmd_cover(args) -> int:
    """その曲のジャケットを作る・候補から選ぶ → 3000×3000 に仕上げる → 公開の準備を続ける（決定 73）。
    毎週の曲・不定期の曲：候補を作る（generate_visuals.py）→ 最初の 5 回は Gon さんが選ぶ（--choose）・6 回目からは自動
    → 仕上げ（finalize_cover.py）。デビュー曲：作業セットのジャケット（kit_images.py）"""
    song = _songs.by_id(args.id)
    if song is None:
        print(f"【結果】曲 {args.id} が見つかりません")
        return 1
    if song["kind"] == "debut":
        subprocess.run([sys.executable, str(S / "kit_images.py"), "--kit", str(song["kit"]), "--only", song["slug"], "--kind", "cover"])
    else:
        d = cover_dir(song)
        brief = str(song["brief"])
        lab = ["--label", song["label_slug"]] if song.get("label_slug") not in (None, "", "drive") else []
        gv = [sys.executable, str(S / "generate_visuals.py"), "--artist", song["slug"], *lab, "--kind", "cover", "--brief", brief]
        if args.choose:
            step(f"{song['artist']}「{song['title']}」のジャケットの候補 #{args.choose} を選んだと記録しています")
            if subprocess.run([*gv, "--choose", f"cover:{args.choose}", "--reason", args.reason or ""]).returncode != 0:
                print("【結果】選んだ記録ができませんでした（上の記録を見てください）")
                return 1
            (d / "cover_final.jpg").unlink(missing_ok=True)   # 選び直したときは仕上げ直す
        elif args.redo or not cover_candidates(song):
            if not os.environ.get("OPENAI_API_KEY"):
                print("【結果】ジャケットを作る OpenAI の鍵がありません（「鍵を入れる」から入れてください）")
                return 2
            step(f"{song['artist']}「{song['title']}」のジャケットの候補を作っています（数分。OpenAI の画像の料金がかかります）")
            if args.redo:   # 作り直す：前の選択と仕上がりは消す（候補の画像は上書き）。ジャケットの場面も選び直す（決定 115）
                for f in ("selection_cover.json", "cover_final.jpg"):
                    (d / f).unlink(missing_ok=True)
                b = read_json(song["brief"])
                if b.pop("cover_scenes", None) is not None:
                    Path(song["brief"]).write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            subprocess.run([*gv, "--ask"] if args.redo else gv)   # 作り直したら、自動で選ばずに Gon さんの選択を待つ（決定 123）
        if (d / "selection_cover.json").exists() and not (d / "cover_final.jpg").exists():
            step("選んだジャケットを 3000×3000 に仕上げています")
            subprocess.run([sys.executable, str(S / "finalize_cover.py"), brief])
        if not (d / "cover_final.jpg").exists():
            n = len(cover_candidates(song))
            print(f"【結果】ジャケットの候補が {n} 枚できました。ポップアップで 1 枚選んでください" if n else
                  "【結果】ジャケットの候補を作れませんでした（上の記録を見てください）")
            return 2
    st = _songs.state(song)
    if st.get("status") in ("imported", "waiting_ffmpeg", "waiting_cover"):
        now = advance(song)
        store_set(song)
        if now == "ready":
            _book()
            print(f"【結果】ジャケットができて、{song['artist']}「{song['title']}」は DistroKid に登録できます")
            return 0
        print(f"【結果】ジャケットはできました。いまの段階：{now}")
        return 2
    print("【結果】ジャケットができました")
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
    if (song.get("sprint") or song.get("daily")) and song.get("brief"):   # 集中・毎日配信の曲：ブリーフの配信日時も、登録した配信日にそろえる（SNS の投稿の時刻など。決定 53・94）
        import sprint
        b = read_json(song["brief"])
        b["release_at"] = sprint.release_at_asap(day)
        Path(song["brief"]).write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if song["kind"] == "debut":
        a = load_artist(song["slug"])
        if "debut_week_before" not in st:   # 登録を取り消したときに戻せるように、前の値を残す
            st["debut_week_before"] = a.get("debut_week")
            _songs.save_state(song, st)
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


ARTIST_LINKS = OUT / "knowledge" / "artist_links.json"


def artist_links(slug: str) -> dict:
    """配信が始まってわかった、その組の配信先のページ（{"spotify": {"id": …, "url": …}}）"""
    return (read_json(ARTIST_LINKS) if ARTIST_LINKS.exists() else {}).get(slug, {})


def save_artist_link(slug: str, service: str, ident: str, url: str = "") -> None:
    d = read_json(ARTIST_LINKS) if ARTIST_LINKS.exists() else {}
    cur = d.setdefault(slug, {}).get(service) or {}
    if cur.get("id") != ident:
        d[slug][service] = {"id": ident, "url": url, "found_at": datetime.now().isoformat(timespec="seconds")}
        ARTIST_LINKS.parent.mkdir(parents=True, exist_ok=True)
        ARTIST_LINKS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"   ○ {service} のアーティストのページを記録しました：{ident}（次の曲から DistroKid で Yes とこの ID を使う）")


def find_on_spotify(token: str, song: dict) -> dict | None:
    q = urllib.parse.quote(f'track:"{song["title"]}" artist:"{song["artist"]}"')
    items = spotify_get(token, f"search?q={q}&type=track&limit=10").get("tracks", {}).get("items", [])
    for t in items:
        if _songs.norm(t.get("name")) == _songs.norm(song["title"]) and \
                any(_songs.norm(x.get("name")) == _songs.norm(song["artist"]) for x in t.get("artists", [])):
            found = {"isrc": (t.get("external_ids") or {}).get("isrc", ""), "url": (t.get("external_urls") or {}).get("spotify", ""),
                     "released": (t.get("album") or {}).get("release_date", ""), "spotify_track_id": t.get("id")}
            ar = next((x for x in t.get("artists", []) if _songs.norm(x.get("name")) == _songs.norm(song["artist"])), {})
            if ar.get("id"):   # DistroKid が作ったアーティストのページ。2 曲目からは「Artist already in Spotify? → Yes」でこの ID を使う
                save_artist_link(song["slug"], "spotify", ar["id"], (ar.get("external_urls") or {}).get("spotify", ""))
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
    if not token:   # 鍵が無いとき：Apple Music・Deezer・song.link・Spotify の曲のページ（・Claude の Web 検索）で確かめる（決定 57）
        return track_without_key(waiting, args)
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
                     '<p>ロボット確認が続くときは、<a href="/paste">写して貼る</a>ページ（ふだんのブラウザで 1 欄ずつ）を使ってください。</p></section>')
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


# ---------------------------------------------------------------------------
# 戻す（ダッシュボードで間違えて進めたとき。決定 38）。消さずに out/publish/undo/ に移すので、あとから取り出せる
BACK_TO = {"suno": "Suno で作曲（取り込みを取り消す）", "master": "音量を整え直す（取り込んだ曲はそのまま）",
           "cover": "ジャケットを選び直す（6 案を作り直す）",
           "ready": "登録の準備ができた（DistroKid の登録を取り消す）", "registered": "DistroKid 登録済み（配信中を取り消す）"}


def default_back(status: str) -> str | None:
    """1 つ前の工程"""
    return {"live": "registered", "registered": "ready", "ready": "suno", "waiting_cover": "suno",
            "waiting_ffmpeg": "suno", "imported": "suno"}.get(status)


def back_options(song: dict) -> list[str]:
    """この曲でいま選べる戻り先（近い順）"""
    s = _songs.state(song).get("status", "")
    order = {"live": ["registered", "ready", "suno"], "registered": ["ready", "suno"], "ready": ["cover", "suno", "master"],
             "waiting_cover": ["suno", "master"], "waiting_ffmpeg": ["suno"], "imported": ["suno", "master"]}
    out = order.get(s, [])
    if song["kind"] == "debut":   # デビュー曲のジャケットは作業セットのもの（候補から選ぶ流れではない）
        out = [t for t in out if t != "cover"]
    return out


def _stash(paths: list[Path], song: dict) -> Path | None:
    dest = PUB / "undo" / f"{song['key']}_{datetime.now():%Y%m%d-%H%M%S}"
    moved = False
    for p in paths:
        if p.exists():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest / p.name))
            moved = True
    return dest if moved else None


def go_back(song: dict, to: str | None = None) -> str:
    """戻して、表示用の一文を返す"""
    st = _songs.state(song)
    cur = st.get("status", "")
    to = to or default_back(cur)
    now_ja = {"": "Suno で作曲", "imported": "取り込み済み", "waiting_ffmpeg": "音量の道具待ち", "waiting_cover": "ジャケット待ち",
              "ready": "登録の準備ができた", "registered": "DistroKid 登録済み", "live": "配信中"}.get(cur, cur)
    if not to:
        raise ValueError(f"いまは「{now_ja}」なので、これより前には戻せません")
    if to not in back_options(song):
        raise ValueError(f"いまの段階（{now_ja}）からは「{BACK_TO.get(to, to)}」に戻せません")
    hist = st.setdefault("history", [])
    hist.append({"from": cur, "to": to, "at": datetime.now().isoformat(timespec="seconds"), "by": "戻す"})
    if to in ("registered", "ready", "suno"):   # 配信中を取り消す
        for k in ("released", "isrc", "upc", "url", "spotify_track_id", "live_checked_at"):
            st.pop(k, None)
    if to in ("ready", "suno") and cur in ("registered", "live"):   # DistroKid の登録を取り消す
        for k in ("registered_at", "release_date"):
            st.pop(k, None)
        if song["kind"] == "debut" and "debut_week_before" in st:
            a = load_artist(song["slug"])
            a["debut_week"] = st.pop("debut_week_before")
            save_artist(a)
    note = ""
    if to == "suno":   # 取り込みを取り消す：テイク・音量を整えた音源・下書き・公開セットを「戻したもの」に移す
        folder, _, _ = publish_folder(song)
        dest = _stash([OUT / "takes" / song["key"], OUT / "masters" / song["key"], PUB / song["key"], folder], song)
        reg = read_json(REGISTRY) if REGISTRY.exists() else {}
        for v in reg.values():   # 取り込んだファイルは「楽曲情報」に残る。自動でまた取り込まないよう、印を付ける
            if isinstance(v, dict) and v.get("song") in (song["id"], song["key"]):
                v["undone"] = datetime.now().isoformat(timespec="seconds")
        REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
        for k in ("take", "source", "imported_at", "generated_on", "ready_at"):
            st.pop(k, None)
        st["status"] = ""
        note = (f"取り込んだもの・下書き・公開セットは {dest} に移しました。" if dest else "") + \
            "Suno でいいねした 1 本を、もう一度「楽曲情報」にダウンロードすると取り込みます"
    elif to == "cover":   # ジャケットを選び直す：仕上がりと選択を「戻したもの」に移し、ジャケット待ちに戻す（決定 124）
        d = cover_dir(song)
        dest = _stash([d / "selection_cover.json", d / "cover_final.jpg", d / "cover_final_notext.jpg", d / "cover_final.text.json"], song)
        b = read_json(song["brief"]) if song.get("brief") and Path(song["brief"]).exists() else None
        if b is not None and b.pop("cover_scenes", None) is not None:   # シーンも選び直す
            Path(song["brief"]).write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        d.mkdir(parents=True, exist_ok=True)   # 作り直した候補は自動で選ばない（Gon さんが選ぶまで待つ。決定 123）
        (d / "owner_pick.json").write_text(json.dumps({"why": "DistroKid の登録の前にジャケットに戻した", "date": date.today().isoformat()},
                                                      ensure_ascii=False), encoding="utf-8")
        st.pop("ready_at", None)
        st["status"] = "waiting_cover"
        note = (f"前のジャケットは {dest} に移しました。" if dest else "") + "ジャケットの案を 6 つ作り直します（数分）。できたらポップアップで 1 枚選んでください"
    elif to == "master":   # 音量を整え直す：取り込んだ曲はそのまま、整えた音源と下書きをやり直す
        dest = _stash([OUT / "masters" / song["key"], PUB / song["key"]], song)
        sel_p = OUT / "takes" / song["key"] / "selection.json"
        if sel_p.exists():
            sel = read_json(sel_p)
            sel.pop("master", None)
            sel_p.write_text(json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8")
        st.pop("ready_at", None)
        st["status"] = "imported"
        note = "次の「曲を取り込む」（数分ごとの自動でも）で、音量からやり直します"
    else:
        st["status"] = to
    _songs.save_state(song, st)
    _book()
    return f"{song['artist']}「{song['title']}」を「{BACK_TO[to]}」に戻しました。{note}"


def cmd_back(args) -> int:
    song = _songs.by_id(args.id)
    if song is None:
        sys.exit(f"[エラー] 曲 {args.id} が見つかりません")
    try:
        msg = go_back(song, args.to)
    except ValueError as e:
        print(f"【結果】まだ終わっていません：{e}")
        return 2
    print(f"○ {msg}")
    if args.to == "cover":   # そのまま 6 案を作り直す（OpenAI の画像の料金。決定 124）
        return cmd_cover(argparse.Namespace(id=song["id"], redo=True, choose=None, reason=""))
    print(f"【結果】{msg}")
    return 0


def track_without_key(waiting: list[dict], args) -> int:
    import store_check
    changed = False
    for song in waiting:
        r = store_check.check(song)
        for n in r["notes"]:
            print(f"   － {song['artist']}「{song['title']}」：{n}")
        if not r["live"]:
            print(f"   ・{song['artist']}「{song['title']}」：まだ Apple Music・Deezer・Spotify に出ていません")
            continue
        sp, ap, dz = r["spotify"], r["apple"], r["deezer"]
        found = {"isrc": dz.get("isrc", ""), "upc": dz.get("upc", ""), "url": sp.get("url") or ap.get("url") or dz.get("url", ""),
                 "released": ap.get("released") or dz.get("released") or date.today().isoformat(),
                 "spotify_track_id": sp.get("track_id", ""), "apple_url": ap.get("url", ""), "deezer_url": dz.get("url", ""),
                 "youtube_music_url": r.get("youtube_music", "")}
        st = _songs.state(song)
        st.update({"status": "live", **{k: v for k, v in found.items() if v}, "live_checked_at": date.today().isoformat(),
                   "live_checked_by": "store_check"})
        _songs.save_state(song, st)
        changed = True
        where = "・".join(n for n, x in (("Spotify", sp.get("url")), ("Apple Music", ap.get("url")), ("Deezer", dz.get("url"))) if x)
        print(f"   ○ {song['artist']}「{song['title']}」が配信中になりました（{where} で確認）" + (f"。ISRC {found['isrc']}" if found["isrc"] else ""))
        notify("配信が始まりました", f"{song['artist']}「{song['title']}」（{where}）")
        if sp.get("artist_id"):   # 2 曲目から DistroKid で「Yes」とこの ID を使い、同じページにまとめる
            save_artist_link(song["slug"], "spotify", sp["artist_id"], f"https://open.spotify.com/artist/{sp['artist_id']}")
        elif not sp.get("url"):
            print("   ・Spotify ではまだ見つかりません（Apple Music などには出ています）。Spotify のアーティストのページは次の確認で探します")
        if ap.get("artist_id"):
            save_artist_link(song["slug"], "apple", ap["artist_id"], ap.get("artist_url", ""))
    if changed:
        _book()
    # 配信中なのに Spotify のアーティストのページがまだわからない組は、次の曲の登録のために探し続ける
    todo = {}
    for s in _songs.all_songs():   # 1 組 1 曲（デビュー曲を先に）
        if _songs.state(s).get("status") == "live" and not (artist_links(s["slug"]).get("spotify") or {}).get("id"):
            todo.setdefault(s["slug"], s)
    for song in todo.values():
        sp = store_check.check(song).get("spotify") or {}
        if sp.get("artist_id"):
            save_artist_link(song["slug"], "spotify", sp["artist_id"], f"https://open.spotify.com/artist/{sp['artist_id']}")
    return 0


def cmd_live(args) -> int:
    """配信が始まったことを手で記録する（Gon さんが Spotify・Apple Music などで見つけたとき。Spotify の鍵が無くても進められる。決定 55）。
    Spotify の曲の住所があれば記録し、鍵があれば ISRC なども取りに行く。アーティストの住所があれば、2 曲目から同じページにまとめるのに使う"""
    song = _songs.by_id(args.id)
    if song is None:
        print(f"[エラー] 曲 {args.id} が見つかりません")
        return 2
    st = _songs.state(song)
    if st.get("status") not in ("registered", "live"):
        print(f"[エラー] {song['artist']}「{song['title']}」はまだ DistroKid に登録した記録がありません（先に「登録した」を）")
        return 2
    found = {}
    try:
        token = spotify_token()
        if token:
            found = find_on_spotify(token, song) or {}
    except Exception:  # noqa: BLE001
        found = {}
    tm = re.search(r"open\.spotify\.com/(?:intl-\w+/)?track/([A-Za-z0-9]{22})", args.track or "")
    if tm and not found.get("url"):
        found.update({"url": f"https://open.spotify.com/track/{tm.group(1)}", "spotify_track_id": tm.group(1)})
    day = args.date or date.today().isoformat()
    st.update({"status": "live", "released": st.get("released") or found.get("released") or day,
               "live_checked_at": date.today().isoformat(), "live_marked_by": "owner", **{k: v for k, v in found.items() if v}})
    _songs.save_state(song, st)
    print(f"○ {song['artist']}「{song['title']}」を配信中にしました" + (f"（ISRC {found['isrc']}）" if found.get("isrc") else ""))
    if args.artist:
        cmd_artist_page(argparse.Namespace(slug=song["slug"], page=args.artist))
    elif not (artist_links(song["slug"]).get("spotify") or {}).get("id"):
        print("   ・Spotify のアーティストのページはまだ記録されていません（2 曲目からの登録の前に、アーティストの住所を記録してください）")
    _book()
    return 0


def cmd_artist_page(args) -> int:
    """デビュー曲の Spotify のアーティストのページを手で記録する（DistroKid の曲のページに出た URI か、Spotify の住所を貼る）。
    2 曲目からの DistroKid 登録で「Yes」とこの ID を使い、同じページにまとめる（決定 53）"""
    m = re.search(r"(?:spotify:artist:|open\.spotify\.com/(?:intl-\w+/)?artist/)([A-Za-z0-9]{22})", args.page) or re.fullmatch(r"([A-Za-z0-9]{22})", args.page.strip())
    if not m:
        print("[エラー] Spotify のアーティストの URI（spotify:artist:…）か住所（https://open.spotify.com/artist/…）を貼ってください")
        return 2
    if not any(a.get("slug") == args.slug for a in all_artists()):
        print(f"[エラー] 組 {args.slug} が見つかりません")
        return 2
    save_artist_link(args.slug, "spotify", m.group(1), f"https://open.spotify.com/artist/{m.group(1)}")
    print(f"【結果】{args.slug} の Spotify のアーティストのページを記録しました：spotify:artist:{m.group(1)}")
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
    p = sub.add_parser("back", help="間違えて進めた工程を戻す")
    p.add_argument("id")
    p.add_argument("--to", choices=list(BACK_TO), help="戻り先（省くと 1 つ前）")
    p = sub.add_parser("live", help="配信が始まったことを手で記録する（Spotify の鍵が無くても）")
    p.add_argument("id")
    p.add_argument("--track", help="Spotify の曲の住所（https://open.spotify.com/track/…）")
    p.add_argument("--artist", help="Spotify のアーティストの住所（https://open.spotify.com/artist/…）")
    p.add_argument("--date", help="配信が始まった日（省くと今日）")
    p = sub.add_parser("artist-page", help="Spotify のアーティストのページを手で記録する（2 曲目からを同じページにまとめる）")
    p.add_argument("slug")
    p.add_argument("page", help="spotify:artist:… か https://open.spotify.com/artist/…")
    p = sub.add_parser("cover", help="その曲のジャケットを作る・候補から選ぶ → 仕上げて公開の準備を続ける")
    p.add_argument("id")
    p.add_argument("--choose", type=int, help="選んだ候補の番号")
    p.add_argument("--reason", default="", help="選んだ理由（一言。次からの自動選択の好みに使う）")
    p.add_argument("--redo", action="store_true", help="候補を作り直す")
    p = sub.add_parser("suno", help="Suno に貼る内容のページを作る")
    p.add_argument("--open", action="store_true", help="ブラウザで開く")
    args = ap.parse_args()
    fn = {"scan": cmd_scan, "status": cmd_status, "draft": cmd_draft, "registered": cmd_registered,
          "track": cmd_track, "suno": cmd_suno, "suno-request": cmd_suno_request, "back": cmd_back,
          "artist-page": cmd_artist_page, "live": cmd_live, "cover": cmd_cover}.get(args.cmd)
    if fn is None:
        ap.print_help()
        return
    sys.exit(fn(args))


if __name__ == "__main__":
    main()

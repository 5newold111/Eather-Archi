#!/usr/bin/env python3
"""
アーティストの情報・写真・ジャケット・楽曲を、まず Mac の「ローカル保存（一時保存）」に、つぎに外付け SSD（DS72）にまとめて保管する

  1. ローカル保存（一時保存）… ダウンロード/SUNO楽曲/ローカル保存（一時保存）/<アーティスト名>/。いつでも。SSD が無くても公開に要るものがここにそろう
  2. SSD … つながっていれば、ローカル保存の <アーティスト名>/ を SSD の 02_アーティスト/<アーティスト名>/ へ写す。無ければ後回し（次につないだときに追いつく）

保管するもの（Postdog Records の全組と子レーベルの組。試験用の週 2000-xx-xx は除く。並びは _archive.py）：
  01_管理表/       アーティスト情報.xlsx（SSD に無いときだけ見本を置く。以後は SSD の正本を触らない）
                   楽曲管理.xlsx（song_book.py が曲の一覧と進み具合を書き直す）
  03_作業書/       デビュー_<日付>/ 作業書（PDF）・曲の情報（kit.json）・画像の依頼文（chatgpt_prompts.json）
  02_アーティスト/<アーティスト名>/
     01_情報/      設定書（templates/artists/<組>.json）とプロフィール（out/profiles/<組>.md・.json）
     02_写真/      デビュー用の作業セットの写真（<アーティスト名>_photo.jpg・_photo2.jpg）と、選ばれたデビュー写真・ロゴ
     03_ジャケット/ 曲ごとのジャケット（<アーティスト名>_<曲名>_1.jpg。作り直すと _2・_3 …）
     04_楽曲/<曲名>/ 公開セット：配信用の音源（<曲名>_<アーティスト名>_<生成日>.wav）・Suno の元データ（…_Suno元データ.wav）・
                   ジャケット・DistroKid の下書き（登録シート）・
                   選んだテイク・歌詞。ここを開けば DistroKid に登録できる（曲名が未定の間は <週>）
  01_管理表・03_作業書は SSD にだけ置く（SSD が無いときは書き込みを保留）

同じ中身のファイルは写し直さない。中身が変わったファイルは前の版を _old で残す。

使い方：
  python3 scripts/archive_sync.py                          # 全組を保管
  python3 scripts/archive_sync.py --only nao_easterly      # 1 組だけ
  python3 scripts/archive_sync.py --check                  # SSD にまだ写していないファイルを見るだけ
  python3 scripts/archive_sync.py --setup "/Volumes/DS72"  # 保管庫の場所を .env に書く（自動で見つからないとき）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_dotenv, read_json  # noqa: E402

load_dotenv()
import _archive as archive  # noqa: E402  （.env を読んだあとに読み込む）
import _songs  # noqa: E402  曲名・公開セットのフォルダ名

KIT_ROOT = ROOT / "debut_kit"


def items_for(a: dict) -> list[tuple[str, str | None, Path, str]]:
    """(種類, 04_楽曲 の下のフォルダ名, 元のファイル, 保管庫での名前) の一覧。
    名前が「#」で始まるものは番号付き（#<アーティスト名>_<曲名> → <アーティスト名>_<曲名>_1.jpg。同じ中身なら同じ番号）"""
    slug, name = a["slug"], a.get("name") or a["slug"]
    out: list[tuple[str, str | None, Path, str]] = []
    # 情報
    p = ROOT / "templates" / "artists" / f"{slug}.json"
    if p.exists():
        out.append(("info", None, p, f"{name}_設定書.json"))
    for ext in ("md", "json"):
        p = OUT / "profiles" / f"{slug}.{ext}"
        if p.exists():
            out.append(("info", None, p, f"{name}_プロフィール.{ext}"))
    # アーティスト写真（デビュー用の作業セット。新しい日付のものを優先）
    for kit in sorted(KIT_ROOT.glob("*/images"), reverse=True):
        for kind in ("photo", "photo2"):
            p = kit / f"{slug}_{kind}.jpg"
            if p.exists() and not any(x[3] == f"{name}_{kind}.jpg" for x in out):
                out.append(("photo", None, p, f"{name}_{kind}.jpg"))
    # 半年ごとに刷新した写真（refresh_photos.py。名前は <アーティスト名>_photo_<年-月>.jpg）
    for p in sorted((OUT / "visuals" / slug / "photos").glob("*/*.jpg")):
        out.append(("photo", None, p, p.name))
    # 選ばれたデビュー写真・ロゴ（generate_visuals.py --kind debut）
    vdir = OUT / "visuals" / slug
    for sel in sorted(vdir.glob("debut/selection_*.json")):
        d = read_json(sel)
        src = sel.parent / f"{d.get('kind')}_{int(d.get('chosen', 0)):02d}.png"
        if src.exists():
            out.append(("photo", None, src, f"{name}_debut_{d.get('kind')}.png"))
    # 曲ごと：ジャケット（03_ジャケット）と、公開セット（04_楽曲/<曲名>/）
    for song in _songs.all_songs():
        if song["slug"] != slug or str(song["week"]).startswith("2000-"):
            continue
        stem = _songs.cover_stem(song)
        cover = _songs.cover_source(song)
        if cover:
            out.append(("cover", None, cover, "#" + stem))
        master = OUT / "masters" / song["key"] / "master.wav"
        if not master.exists():
            continue   # 公開セットは、配信用の音源ができてから作る
        if _songs.state(song).get("status") == "live":
            continue   # 配信が始まった曲は、ローカル保存（一時保存）に入れ直さない（SSD に写したあと tidy_released が片付ける）
        sub = _songs.folder_name(song)
        astem = _songs.audio_stem(song)   # 楽曲データ：<曲名>_<アーティスト名>_<生成日>
        out.append(("music", sub, master, f"{astem}.wav"))
        if cover:
            out.append(("music", sub, cover, "#" + stem))
        lyr = OUT / "briefs" / f"{song['key']}.lyrics_final.txt"
        if lyr.exists():
            out.append(("music", sub, lyr, f"{stem}_歌詞.txt"))
        sel = OUT / "takes" / song["key"] / "selection.json"
        if sel.exists():
            chosen = read_json(sel).get("decision", {}).get("selected")
            src = sel.parent / chosen if chosen else None
            if src and src.exists():
                out.append(("music", sub, src, f"{astem}_Suno元データ{src.suffix.lower()}"))
        for f in sorted((OUT / "publish" / song["key"]).glob("*")):   # publish.py：DistroKid の下書き・歌詞など
            if f.is_file():
                out.append(("music", sub, f, f"{stem}_{f.name}"))
        if song["kind"] == "weekly":
            sheet = song_sheet(song["week"], slug, name)
            if sheet:
                out.append(("music", sub, sheet, f"{stem}_登録シート.md"))
    return out


def song_sheet(week: str, slug: str, name: str) -> Path | None:
    """DistroKid 登録シート（その週の全曲）から、この曲の分だけを抜き出して out/distrokid/<週>/songs/<組>.md に書く"""
    src = OUT / "distrokid" / week / "sheet.md"
    if not src.exists():
        return None
    text, part, keep = src.read_text(encoding="utf-8"), [], False
    for line in text.splitlines():
        if line.startswith("## "):
            keep = re.match(rf"## \d+\. {re.escape(name)} — ", line) is not None
        if keep:
            part.append(line)
    if not part:
        return None
    dst = OUT / "distrokid" / week / "songs" / f"{slug}.md"
    body = f"# DistroKid 登録シート（{name}・制作週 {week}）\n\n同じフォルダの master.wav と cover.jpg を使います。\n\n" + "\n".join(part).rstrip() + "\n"
    if not dst.exists() or dst.read_text(encoding="utf-8") != body:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(body, encoding="utf-8")
    return dst


def _release_day(slug: str, week: str) -> str | None:
    b = OUT / "briefs" / f"{week}_{slug}.json"
    if b.exists():
        r = json.loads(b.read_text(encoding="utf-8")).get("release_at")
        return str(r)[:10] if r else None
    return None


def seed_book(base: Path, check: bool) -> None:
    """SSD に管理表が無いときだけ、見本（music-label/artists.xlsx）を置く。SSD の正本はそれ以外では触らない"""
    from _artist_book import ssd_book_path
    if ssd_book_path(seed=False) is None:
        print("  ■ 管理表：SSD に アーティスト情報.xlsx を作ります（見本の artists.xlsx から）")
        if not check:
            ssd_book_path(seed=True)


def sync_kits(base: Path, check: bool) -> int:
    """デビュー用の作業セットの書類（作業書の PDF・曲の情報・依頼文）を 03_作業書/デビュー_<日付>/ へ"""
    n = 0
    for kit in sorted(p for p in KIT_ROOT.iterdir() if p.is_dir()):
        files = [f for f in kit.iterdir() if f.suffix.lower() in (".pdf", ".json", ".md")]
        if not files:
            continue
        folder = archive.child(archive.child(base, archive.KIT_DIR), f"デビュー_{kit.name}")
        for f in files:
            dst = folder / f.name
            if check:
                n += 0 if dst.exists() else 1
                continue
            if not archive.save(f, folder, f.name).startswith("保管済み"):
                n += 1
                print(f"    ○ {archive.KIT_DIR}/デビュー_{kit.name}/{f.name} を保管")
    return n


def tidy_released() -> int:
    """配信が始まった曲（publish.py の記録が live）の公開セットを、ローカル保存（一時保存）から片付ける。
    SSD に同じ中身のファイルがあるものだけ消す（無いもの・中身が違うものは残す）。片付けた数を返す"""
    import filecmp
    base, _ = archive.root()
    if base is None:
        return 0
    n = 0
    for song in _songs.all_songs():
        if _songs.state(song).get("status") != "live":
            continue
        sub = _songs.folder_name(song)
        lf = archive.local_folder(song["artist"], "music", sub)
        sf = archive.find_child(base, archive.ARTIST_DIR)
        for part in (song["artist"], archive.SUBS["music"], sub):
            sf = archive.find_child(sf, part) if sf else None
        if sf is None:
            continue
        for f in [f for f in lf.iterdir() if f.is_file()]:
            twin = sf / f.name
            if twin.exists() and filecmp.cmp(f, twin, shallow=False):
                f.unlink()
                n += 1
        try:
            lf.rmdir()   # 空になったらフォルダも片付ける
        except OSError:
            pass
    return n


def setup(path: str) -> None:
    p = Path(path).expanduser()
    if not p.exists():
        sys.exit(f"[エラー] {p} が見つかりません。SSD がつながっているか、場所が合っているか確かめてください")
    target = p if p.name == archive.LABEL_DIR else p / archive.LABEL_DIR
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [l for l in lines if not l.startswith("ARCHIVE_ROOT=")] + [f"ARCHIVE_ROOT={target}"]
    env.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    print(f"▶ 保管庫の場所を .env に書きました：{target}")


def to_local(acts: list[dict], check: bool) -> tuple[int, int]:
    """1 段目：ローカル保存へ（いつでも）。(対象の数, 新しく入れた・変わった数)"""
    total = changed = 0
    for a in acts:
        name = a.get("name") or a["slug"]
        rows = items_for(a)
        new = []
        for kind, week, src, fname in rows:
            folder = archive.local_folder(name, kind, week)
            total += 1
            if check:
                continue
            if fname.startswith("#"):
                fname = archive.numbered(folder, fname[1:], src, src.suffix.lower())
            res = archive.save(src, folder, fname, link=True)
            if not res.startswith("保管済み"):
                changed += 1
                new.append(f"    ○ {archive.local_label(folder)}/{fname} を{res}")
        if new:
            print(f"  ■ {name}")
            print("\n".join(new))
    return total, changed


def main() -> None:
    ap = argparse.ArgumentParser(description="アーティストの情報・写真・ジャケット・楽曲を、ローカル保存（一時保存）と外付け SSD に保管する")
    ap.add_argument("--only", help="この組だけ（組の ID）")
    ap.add_argument("--check", action="store_true", help="SSD にまだ写していないファイルを見るだけ")
    ap.add_argument("--setup", metavar="PATH", help="SSD の保管庫の場所を .env に書く（例 /Volumes/DS72）")
    ap.add_argument("--on-mount", action="store_true", help="SSD をつないだときの自動実行用（SSD が無ければ何も言わずに終わる。終わったら Mac に知らせる）")
    args = ap.parse_args()
    if args.setup:
        setup(args.setup)
        return

    if args.on_mount and archive.root()[0] is None:
        return   # つながったのは別のドライブ
    acts = [a for a in all_artists() if a.get("slug") and (not args.only or a["slug"] == args.only)]
    moved = []
    if archive.root()[0] is not None and not args.check:
        import publish
        moved = publish.move_to_ssd()
        if moved:
            print(f"▶ 0. SSD が無い間に「ダウンロード/SUNO楽曲」へ入った曲を、SSD の「{archive.INBOX_DIR}」へ移しました")
            for l in moved:
                print("    " + l)
    print(f"▶ 1. ローカル保存（一時保存）：{archive.local_root()}")
    total, changed = to_local(acts, args.check)
    print(f"   対象 {total} 件のうち、新しく入れた・変わったもの {changed} 件")

    base, how = archive.root()
    if base is None:
        n = len(archive.pending())
        print(f"▶ 2. SSD：写すのは後回しにします（{how}）")
        print(f"   SSD にまだ写していないもの {n} 件。公開に要るもの（<アーティスト名>/04_楽曲/<曲名>/）はローカル保存にそろっています")
        print("   管理表（アーティスト情報・楽曲管理）への書き込みも、SSD をつなぐまで保留します")
        return
    print(f"▶ 2. SSD：{base}")
    if args.check:
        n, lines = archive.push(check=True)
        print("\n".join("    " + l for l in lines[:50]))
        print(f"   SSD にまだ写していないもの {n} 件")
        return
    seed_book(base, False)
    kit_changed = sync_kits(base, False)
    n, lines = archive.push()
    for l in lines:
        print("    " + l)
    print(f"   SSD に新しく写した・更新したもの {n + kit_changed} 件")
    cleaned = tidy_released()
    if cleaned:
        print(f"▶ 3. 配信が始まった曲を、ローカル保存（一時保存）から片付けました（SSD に同じものがあるのを確かめてから）：{cleaned} 件")
    if not args.only:
        import song_book
        song_book.update(quiet=True)
    if args.on_mount and (moved or n + kit_changed or cleaned):
        from _common import notify
        notify("SSD に移しました", f"楽曲情報へ {len(moved)} 件・保管 {n + kit_changed} 件・片付け {cleaned} 件")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
アーティストの情報・写真・ジャケット・楽曲を、外付け SSD（DS72）の保管庫にまとめて保管する

保管するもの（Postdog Records の全組と子レーベルの組。試験用の週 2000-xx-xx は除く）：
  情報/        設定書（templates/artists/<組>.json）とプロフィール（out/profiles/<組>.md・.json）
  写真/        デビュー用の作業セットの写真（<アーティスト名>_photo.jpg・_photo2.jpg）と、選ばれたデビュー写真・ロゴ
  ジャケット/   デビュー曲のジャケットと、毎週仕上げたジャケット（<アーティスト名>_<配信日>_cover.jpg）
  楽曲/<週>/   配信用の音源（master.wav）と選んだテイク

同じ中身のファイルは写し直さない。中身が変わったファイルは前の版を _old で残す。
SSD がつながっていなければ何もしない（次につないで実行すれば、まとめて追いつく）。

使い方：
  python3 scripts/archive_sync.py                          # 全組を保管
  python3 scripts/archive_sync.py --only nao_easterly      # 1 組だけ
  python3 scripts/archive_sync.py --check                  # 保管庫の場所と、保管していないファイルの数だけ見る
  python3 scripts/archive_sync.py --setup "/Volumes/DS72"  # 保管庫の場所を .env に書く（自動で見つからないとき）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_dotenv, read_json  # noqa: E402

load_dotenv()
import _archive as archive  # noqa: E402  （.env を読んだあとに読み込む）

KIT_ROOT = ROOT / "debut_kit"


def items_for(a: dict) -> list[tuple[str, str | None, Path, str]]:
    """(種類, 楽曲の週, 元のファイル, 保管庫での名前) の一覧"""
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
    # デビュー用の作業セット（新しい日付のものを優先）
    for kit in sorted(KIT_ROOT.glob("*/images"), reverse=True):
        for kind in ("photo", "photo2", "cover"):
            p = kit / f"{slug}_{kind}.jpg"
            if p.exists() and not any(x[3] == f"{name}_{kind}.jpg" for x in out):
                out.append(("cover" if kind == "cover" else "photo", None, p, f"{name}_{kind}.jpg"))
    # 選ばれたデビュー写真・ロゴ（generate_visuals.py --kind debut）
    vdir = OUT / "visuals" / slug
    for sel in sorted(vdir.glob("debut/selection_*.json")):
        d = read_json(sel)
        src = sel.parent / f"{d.get('kind')}_{int(d.get('chosen', 0)):02d}.png"
        if src.exists():
            out.append(("photo", None, src, f"{name}_debut_{d.get('kind')}.png"))
    # 毎週のジャケット
    for final in sorted(vdir.glob("cover/*/cover_final.jpg")):
        week = final.parent.name
        if week.startswith("2000-"):
            continue
        day = _release_day(slug, week) or week
        out.append(("cover", None, final, f"{name}_{day}_cover.jpg"))
    # 楽曲
    for mdir in sorted(OUT.glob(f"masters/*_{slug}")):
        week = mdir.name[: -len(slug) - 1]
        if week.startswith("2000-"):
            continue
        if (mdir / "master.wav").exists():
            out.append(("music", week, mdir / "master.wav", f"{name}_{week}_master.wav"))
        sel = OUT / "takes" / mdir.name / "selection.json"
        if sel.exists():
            chosen = read_json(sel).get("decision", {}).get("selected")
            src = sel.parent / chosen if chosen else None
            if src and src.exists():
                out.append(("music", week, src, f"{name}_{week}_{src.name}"))
    return out


def _release_day(slug: str, week: str) -> str | None:
    b = OUT / "briefs" / f"{week}_{slug}.json"
    if b.exists():
        r = json.loads(b.read_text(encoding="utf-8")).get("release_at")
        return str(r)[:10] if r else None
    return None


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


def main() -> None:
    ap = argparse.ArgumentParser(description="アーティストの情報・写真・ジャケット・楽曲を外付け SSD に保管する")
    ap.add_argument("--only", help="この組だけ（組の ID）")
    ap.add_argument("--check", action="store_true", help="保管庫の場所と、まだ保管していないファイルの数だけ見る")
    ap.add_argument("--setup", metavar="PATH", help="保管庫の場所を .env に書く（例 /Volumes/DS72）")
    args = ap.parse_args()
    if args.setup:
        setup(args.setup)
        return

    base, how = archive.root()
    if base is None:
        print(f"－ SSD には保管しませんでした：{how}")
        return
    print(f"▶ 保管庫：{base}")
    acts = [a for a in all_artists() if a.get("slug") and (not args.only or a["slug"] == args.only)]
    total = changed = 0
    for a in acts:
        name = a.get("name") or a["slug"]
        rows = items_for(a)
        if not rows:
            continue
        print(f"  ■ {name}")
        for kind, week, src, fname in rows:
            folder, _ = archive.artist_folder(name, kind)
            if week:
                folder = archive.child(folder, week)
            total += 1
            if args.check:
                dst = folder / fname
                if not dst.exists():
                    changed += 1
                    print(f"    ・未保管：{archive.label(folder)}/{fname}")
                continue
            res = archive.save(src, folder, fname)
            if not res.startswith("保管済み"):
                changed += 1
                print(f"    ○ {archive.label(folder)}/{fname} を{res}")
    verb = "まだ保管していない" if args.check else "新しく保管・更新した"
    print(f"▶ 対象 {total} 件のうち、{verb}もの {changed} 件")


if __name__ == "__main__":
    main()

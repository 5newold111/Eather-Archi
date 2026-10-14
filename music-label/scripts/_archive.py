"""
外付け SSD（Silicon Power DS72）にアーティストの情報・写真・ジャケット・楽曲を保管する部品

保管庫の場所（上から順に探す）：
  1. .env の ARCHIVE_ROOT（例 ARCHIVE_ROOT=/Volumes/DS72/Postdog Records）
  2. Mac につながっているドライブ（/Volumes/ の下）のうち、名前に「DS72」を含むもの → その中の「Postdog Records」フォルダ
SSD がつながっていないときは何もせず、その旨だけ表示する（次に動いたときにまとめて保管される）。

保管庫の中の並び：
  Postdog Records/アーティスト情報/<アーティスト名>/
      写真/        <アーティスト名>_photo.jpg・_photo2.jpg
      ジャケット/   <アーティスト名>_cover.jpg（デビュー曲）・<アーティスト名>_<配信日>_cover.jpg（毎週）
      楽曲/        <週>/ master.wav（配信用）・選んだテイク
      情報/        設定書（json）・プロフィール（md）

フォルダ名は、大文字・小文字、全角・半角、Mac の濁点の持ち方（NFD／NFC）の違いを同じ名前として扱う。
同じ名前のフォルダがすでにあればそこを使い、無ければ作る。
"""
from __future__ import annotations
import filecmp, os, shutil, unicodedata
from pathlib import Path

VOLUMES = Path("/Volumes")
VOLUME_HINT = "DS72"
LABEL_DIR = "Postdog Records"
ARTIST_DIR = "アーティスト情報"
SUBS = {"photo": "写真", "cover": "ジャケット", "music": "楽曲", "info": "情報"}


def same_name(a: str, b: str) -> bool:
    n = lambda s: unicodedata.normalize("NFKC", s).strip().casefold()
    return n(a) == n(b)


def find_child(parent: Path, name: str) -> Path | None:
    for c in sorted(parent.iterdir()):
        if c.is_dir() and same_name(c.name, name):
            return c
    return None


def child(parent: Path, name: str) -> Path:
    """同じ名前のフォルダがあればそれを、無ければ作って返す"""
    return find_child(parent, name) or _mkdir(parent / name.strip())


def _mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def root() -> tuple[Path | None, str]:
    """保管庫（Postdog Records フォルダ）の場所。見つからなければ (None, 理由)"""
    env = os.environ.get("ARCHIVE_ROOT", "").strip()
    if env:
        p = Path(env).expanduser()
        drive = Path(*p.parts[:3]) if str(p).startswith("/Volumes/") and len(p.parts) >= 3 else p.parent
        if not drive.exists():
            return None, f"SSD がつながっていません（{drive}）"
        return _mkdir(p), str(p)
    if VOLUMES.is_dir():
        vols = [v for v in sorted(VOLUMES.iterdir()) if v.is_dir() and VOLUME_HINT.casefold() in v.name.casefold()]
        if len(vols) == 1:
            return child(vols[0], LABEL_DIR), str(vols[0] / LABEL_DIR)
        if len(vols) > 1:
            return None, f"名前に {VOLUME_HINT} を含むドライブが複数あります（.env の ARCHIVE_ROOT で指定）"
    return None, (f"SSD（名前に {VOLUME_HINT} を含むドライブ）が見つかりません。"
                  "つないでから実行するか、.env の ARCHIVE_ROOT で場所を指定してください")


def artist_folder(artist_name: str, kind: str | None = None) -> tuple[Path | None, str]:
    """アーティスト情報/<アーティスト名>/（kind を渡すとその下の 写真・ジャケット・楽曲・情報）"""
    base, how = root()
    if base is None:
        return None, how
    folder = child(child(base, ARTIST_DIR), artist_name)
    return (child(folder, SUBS[kind]) if kind else folder), how


def save(src: Path, folder: Path, name: str) -> str:
    """folder/name に保存する。同じ中身なら何もしない。中身が変わっていれば前の版を _old を付けて残す"""
    dst = folder / name
    if dst.exists():
        if filecmp.cmp(src, dst, shallow=False):
            return "保管済み（変わりなし）"
        old = dst.with_name(f"{dst.stem}_old{dst.suffix}")
        if old.exists():
            old.unlink()
        dst.rename(old)
        shutil.copy2(src, dst)
        return "上書き保管（前の版は _old で残しました）"
    shutil.copy2(src, dst)
    return "保管"


def label(folder: Path) -> str:
    """表示用：保管庫の中での位置"""
    base, _ = root()
    try:
        return str(folder.relative_to(base)) if base else str(folder)
    except ValueError:
        return str(folder)

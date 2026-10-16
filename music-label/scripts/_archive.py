"""
外付け SSD（Silicon Power DS72）にアーティストの情報・写真・ジャケット・楽曲を保管する部品

保管庫の場所（上から順に探す）：
  1. .env の ARCHIVE_ROOT（例 ARCHIVE_ROOT=/Volumes/DS72/Postdog Records）
  2. Mac につながっているドライブ（/Volumes/ の下）のうち、名前に「DS72」を含むもの → その中の「Postdog Records」フォルダ
SSD がつながっていないときは何もせず、その旨だけ表示する（次に動いたときにまとめて保管される）。

保管庫の中の並び（番号順に Finder で並ぶ。docs/14_storage.md）：
  Postdog Records/
    01_管理表/      アーティスト情報.xlsx（アーティストの正本）・楽曲管理.xlsx（曲の一覧と進み具合）
    02_アーティスト/<アーティスト名>/
        01_情報/      設定書（json）・プロフィール（md）
        02_写真/      <アーティスト名>_photo.jpg・_photo2.jpg
        03_ジャケット/ <アーティスト名>_cover.jpg（デビュー曲）・<アーティスト名>_<配信日>_cover.jpg（毎週）
        04_楽曲/<週>/ 配信用の音源（master.wav）・選んだテイク・歌詞
    03_作業書/      デビュー_<日付>/ 作業書（PDF）・曲の情報・画像の依頼文
    09_移行前/      Google から移したもの（オーナーが入れる。機械は触らない）
  前の並び（アーティスト情報・管理表・写真 など）で保管したものは、見つけしだい新しい名前へ移す。

フォルダ名は、大文字・小文字、全角・半角、Mac の濁点の持ち方（NFD／NFC）の違いを同じ名前として扱う。
同じ名前のフォルダがすでにあればそこを使い、無ければ作る。
"""
from __future__ import annotations
import filecmp, os, shutil, unicodedata
from pathlib import Path

VOLUMES = Path("/Volumes")
VOLUME_HINT = "DS72"
LABEL_DIR = "Postdog Records"
BOOK_DIR = "01_管理表"
ARTIST_DIR = "02_アーティスト"
KIT_DIR = "03_作業書"
OLD_DIR = "09_移行前"
SUBS = {"info": "01_情報", "photo": "02_写真", "cover": "03_ジャケット", "music": "04_楽曲"}
# 前の並びの名前 → 新しい名前
_RENAME_TOP = {"管理表": BOOK_DIR, "アーティスト情報": ARTIST_DIR}
_RENAME_SUB = {"情報": SUBS["info"], "写真": SUBS["photo"], "ジャケット": SUBS["cover"], "楽曲": SUBS["music"]}
_migrated: set[str] = set()


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


def _move_into(src: Path, dst: Path) -> None:
    """src を dst という名前にする。dst が既にあれば、中身を 1 つずつ移す（同じ名前のファイルは dst 側を残す）"""
    if not dst.exists():
        src.rename(dst)
        return
    for c in list(src.iterdir()):
        target = dst / c.name
        if c.is_dir():
            _move_into(c, target)
        elif not target.exists():
            c.rename(target)
    try:
        src.rmdir()
    except OSError:
        pass


def migrate(base: Path) -> None:
    """前の並びで保管したフォルダを、新しい番号付きの名前へ移す（1 回だけ）"""
    if str(base) in _migrated:
        return
    _migrated.add(str(base))
    _mkdir(base / OLD_DIR)   # Google などから移すものを入れる場所（機械は触らない）
    for old, new in _RENAME_TOP.items():
        o = find_child(base, old)
        if o is not None and o.name != new:
            _move_into(o, base / new)
    books = base / BOOK_DIR
    if books.is_dir():   # 管理表/デビュー_<日付>/ → 03_作業書/
        for d in [d for d in books.iterdir() if d.is_dir() and d.name.startswith("デビュー_")]:
            _move_into(d, _mkdir(base / KIT_DIR) / d.name)
    arts = base / ARTIST_DIR
    if arts.is_dir():
        for a in [a for a in arts.iterdir() if a.is_dir()]:
            for old, new in _RENAME_SUB.items():
                o = a / old
                if o.is_dir():
                    _move_into(o, a / new)


def root() -> tuple[Path | None, str]:
    """保管庫（Postdog Records フォルダ）の場所。見つからなければ (None, 理由)"""
    base, how = _root()
    if base is not None:
        migrate(base)
    return base, how


def _root() -> tuple[Path | None, str]:
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
    """02_アーティスト/<アーティスト名>/（kind を渡すとその下の 01_情報・02_写真・03_ジャケット・04_楽曲）"""
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

"""
Mac の「Google ドライブ（パソコン版）」の同期フォルダを通して、Google ドライブのフォルダにファイルを保存する部品

ドライブの API や鍵は使わない。パソコン版のアプリが同期しているフォルダ
（~/Library/CloudStorage/GoogleDrive-<アカウント>/マイドライブ）にコピーすれば、数秒〜数分でドライブに上がる。

フォルダ名は、大文字・小文字、全角・半角、Mac の濁点の持ち方（NFD／NFC）の違いを同じ名前として扱う。
同じ名前のフォルダがすでにあればそこを使い、無ければ作る。

.env で変えられること（どれも省略できる）：
  GDRIVE_ACCOUNT=5newold111@gmail.com            同期しているアカウントが複数あるとき、どれを使うか
  GDRIVE_ROOT=~/Library/CloudStorage/.../マイドライブ   マイドライブの場所を直接指定する
  GDRIVE_ARTIST_DIR=音楽レーベルアプリ/アーティスト情報   アーティストごとのフォルダを作る親フォルダ（マイドライブから）
"""
from __future__ import annotations
import filecmp, os, shutil, unicodedata
from pathlib import Path

MY_DRIVE_NAMES = ("マイドライブ", "My Drive")
DEFAULT_ACCOUNT = "5newold111@gmail.com"
DEFAULT_ARTIST_DIR = "音楽レーベルアプリ/アーティスト情報"


def same_name(a: str, b: str) -> bool:
    n = lambda s: unicodedata.normalize("NFKC", s).strip().casefold()
    return n(a) == n(b)


def drive_roots() -> list[Path]:
    """パソコン版 Google ドライブの「マイドライブ」の場所（見つかった順。指定のアカウントを先に）"""
    if os.environ.get("GDRIVE_ROOT"):
        p = Path(os.environ["GDRIVE_ROOT"]).expanduser()
        return [p] if p.is_dir() else []
    roots: list[Path] = []
    base = Path.home() / "Library" / "CloudStorage"
    if base.is_dir():
        want = os.environ.get("GDRIVE_ACCOUNT", DEFAULT_ACCOUNT)
        accounts = sorted(base.glob("GoogleDrive-*"), key=lambda p: (want.casefold() not in p.name.casefold(), p.name))
        roots += [a / n for a in accounts for n in MY_DRIVE_NAMES if (a / n).is_dir()]
    roots += [Path("/Volumes/GoogleDrive") / n for n in MY_DRIVE_NAMES if (Path("/Volumes/GoogleDrive") / n).is_dir()]   # 古い版
    return roots


def find_child(parent: Path, name: str) -> Path | None:
    for c in sorted(parent.iterdir()):
        if c.is_dir() and same_name(c.name, name):
            return c
    return None


def resolve(rel_path: str) -> Path | None:
    """マイドライブから rel_path をたどったフォルダ（途中が無ければ None。親フォルダは勝手に作らない）"""
    for root in drive_roots():
        cur: Path | None = root
        for part in Path(rel_path).parts:
            cur = find_child(cur, part)
            if cur is None:
                break
        if cur is not None:
            return cur
    return None


def artist_folder(artist_name: str) -> tuple[Path | None, str]:
    """アーティスト情報/<アーティスト名> のフォルダ。同じ名前があればそれを使い、無ければ作る"""
    base_rel = os.environ.get("GDRIVE_ARTIST_DIR", DEFAULT_ARTIST_DIR)
    if not drive_roots():
        return None, "パソコン版の Google ドライブが見つかりません（入れてログインするか、.env の GDRIVE_ROOT で場所を指定）"
    base = resolve(base_rel)
    if base is None:
        return None, f"マイドライブに「{base_rel}」フォルダが見つかりません（ドライブで作るか、.env の GDRIVE_ARTIST_DIR を直す）"
    found = find_child(base, artist_name)
    if found:
        return found, f"既存のフォルダ「{found.name}」"
    made = base / artist_name.strip()
    made.mkdir()
    return made, f"フォルダ「{made.name}」を新しく作成"


def save(src: Path, folder: Path, name: str) -> str:
    """folder/name に保存する。同じ中身なら何もしない（ドライブ側は上書きしても版の履歴が残る）"""
    dst = folder / name
    if dst.exists() and filecmp.cmp(src, dst, shallow=False):
        return "保存済み（変わりなし）"
    existed = dst.exists()
    shutil.copy2(src, dst)
    return "上書き保存" if existed else "保存"

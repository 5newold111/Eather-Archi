"""
アーティストの情報・写真・ジャケット・楽曲を、まず Mac の「ローカル保存（一時保存）」に、つぎに外付け SSD（Silicon Power DS72）に保管する部品

2 段構え：
  1. ローカル保存（一時保存）… いつでも必ず入れる。場所はダウンロードの中の
       ~/Downloads/SUNO楽曲/ローカル保存（一時保存）/<アーティスト名>/01_情報〜04_楽曲
     （SUNO楽曲・ローカル保存（一時保存）が無ければ作る。.env の LOCAL_ARCHIVE で変えられる）。
     SSD が無くても、公開（DistroKid への登録・SNS）に要るものはここにそろう。
     元のファイル（out/ や debut_kit/）と同じ実体を指す「ハードリンク」で置くので、Mac の容量は増えない
  2. SSD … つながっていれば、ローカル保存の <アーティスト名>/ を SSD の 02_アーティスト/<アーティスト名>/ へ同じ並びで写す（archive_sync.py）。
     つながっていなければ写すのは後回しにし、次につないだときにまとめて追いつく

SSD の場所（上から順に探す）：
  1. .env の ARCHIVE_ROOT（例 ARCHIVE_ROOT=/Volumes/DS72/Postdog Records）
  2. Mac につながっているドライブ（/Volumes/ の下）のうち、名前に「DS72」を含むもの → その中の「Postdog Records」フォルダ
SSD がつながっていないときは何もせず、その旨だけ表示する（次に動いたときにまとめて保管される）。

保管庫の中の並び（番号順に Finder で並ぶ。docs/14_storage.md）：
  Postdog Records/
    01_管理表/      アーティスト情報.xlsx（アーティストの正本）・楽曲管理.xlsx（曲の一覧と進み具合）
    02_アーティスト/<アーティスト名>/
        01_情報/      設定書（json）・プロフィール（md）
        02_写真/      <アーティスト名>_photo.jpg・_photo2.jpg
        03_ジャケット/ <アーティスト名>_<曲名>_1.jpg（作り直すと _2・_3 …）
        04_楽曲/<曲名>/ 公開セット：配信用の音源・ジャケット・DistroKid の下書き・選んだテイク・歌詞
    03_作業書/      デビュー_<日付>/ 作業書（PDF）・曲の情報・画像の依頼文
    09_移行前/      Google から移したもの（オーナーが入れる。機械は触らない）
  前の並び（アーティスト情報・管理表・写真 など）で保管したものは、見つけしだい新しい名前へ移す。

フォルダ名は、大文字・小文字、全角・半角、Mac の濁点の持ち方（NFD／NFC）の違いを同じ名前として扱う。
同じ名前のフォルダがすでにあればそこを使い、無ければ作る。
"""
from __future__ import annotations
import filecmp, os, re, shutil, time, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_PARENT = "SUNO楽曲"                 # ダウンロードの中のフォルダ
LOCAL_NAME = "ローカル保存（一時保存）"
_OLD_LOCAL = ROOT / "ローカル保管庫"        # 前の置き場所（見つけたら中身を新しい場所へ移す）

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
        shutil.move(str(src), str(dst))   # 同じドライブなら名前の付け替えだけ。別のドライブなら写してから消す
        return
    for c in list(src.iterdir()):
        target = dst / c.name
        if c.is_dir():
            _move_into(c, target)
        elif not target.exists():
            shutil.move(str(c), str(target))
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


def local_root() -> Path:
    """ローカル保存（一時保存）の場所（いつでも使える）。中にアーティスト名のフォルダが並ぶ"""
    env = os.environ.get("LOCAL_ARCHIVE", "").strip()
    if env:
        base = _mkdir(Path(env).expanduser())
    else:
        downloads = Path.home() / "Downloads"   # Finder では「ダウンロード」と表示される
        base = child(child(_mkdir(downloads), LOCAL_PARENT), LOCAL_NAME)
    _migrate_local(base)
    return base


def _migrate_local(base: Path) -> None:
    """前の置き場所（music-label/ローカル保管庫/02_アーティスト/<名前>）の中身を、新しい場所の <名前>/ へ移す（1 回だけ）"""
    old = _OLD_LOCAL / ARTIST_DIR
    if str(base) in _migrated or not old.is_dir():
        return
    _migrated.add(str(base))
    for a in [a for a in old.iterdir() if a.is_dir()]:
        _move_into(a, child(base, a.name))
    for d in (old, _OLD_LOCAL):
        try:
            for f in d.glob(".*"):
                f.unlink()
            d.rmdir()
        except OSError:
            pass


def _artist_path(base: Path, artist_name: str, kind: str | None, week: str | None) -> Path:
    folder = child(base, artist_name)
    if kind:
        folder = child(folder, SUBS[kind])
    return child(folder, week) if week else folder


def local_folder(artist_name: str, kind: str | None = None, week: str | None = None) -> Path:
    """ローカル保存の <アーティスト名>/（kind で 01_情報〜04_楽曲、week で 04_楽曲/<週>）"""
    return _artist_path(local_root(), artist_name, kind, week)


def artist_folder(artist_name: str, kind: str | None = None, week: str | None = None) -> tuple[Path | None, str]:
    """SSD の 02_アーティスト/<アーティスト名>/…（SSD がなければ (None, 理由)）"""
    base, how = root()
    if base is None:
        return None, how
    return _artist_path(child(base, ARTIST_DIR), artist_name, kind, week), how


def store(src: Path, artist_name: str, kind: str, fname: str | None = None, sub: str | None = None,
          stem: str | None = None, ext: str = ".jpg") -> list[str]:
    """ローカル保存に入れ、SSD がつながっていれば SSD にも写す。表示用の行を返す。
    fname を渡さず stem を渡すと、<stem>_<番号><ext> の名前にする（ジャケット：<アーティスト名>_<曲名>_1.jpg）"""
    lf = local_folder(artist_name, kind, sub)
    if fname is None:
        fname = numbered(lf, stem, src, ext)
    lines = [f"ローカル：{local_label(lf)}/{fname} を{save(src, lf, fname, link=True)}"]
    sf, how = artist_folder(artist_name, kind, sub)
    if sf is None:
        lines.append(f"SSD：あとで写します（{how}）")
    else:
        lines.append(f"SSD：{label(sf)}/{fname} を{save(lf / fname, sf, fname)}")
    return lines


def local_files() -> list[Path]:
    """ローカル保存の中の、SSD へ写す対象のファイル（<アーティスト名>/01_情報〜04_楽曲 の中だけ。_old の古い版・隠しファイルは除く）"""
    base = local_root()
    out = []
    for a in (a for a in base.iterdir() if a.is_dir()):
        for sub in (d for d in a.iterdir() if d.is_dir() and any(same_name(d.name, x) for x in SUBS.values())):
            out += [f for f in sub.rglob("*") if f.is_file() and not f.name.startswith((".", "~$"))
                    and not f.stem.endswith("_old")]
    return sorted(out)


def _stamp() -> Path:
    return local_root() / ".SSD に写した時刻"


def pending() -> list[Path]:
    """最後に SSD へ写したあとで、ローカル保存に入った・変わったファイル"""
    st = _stamp()
    since = st.stat().st_mtime if st.exists() else 0
    return [f for f in local_files() if f.stat().st_mtime > since or f.stat().st_ctime > since]


def push(check: bool = False) -> tuple[int, list[str]]:
    """ローカル保存の <アーティスト名>/ を、SSD の 02_アーティスト/<アーティスト名>/ へ同じ並びで写す。(写した数, 表示用の行)"""
    base, how = root()
    if base is None:
        return 0, [f"SSD：{how}"]
    lroot = local_root()
    n, lines = 0, []
    for f in local_files():
        relp = Path(ARTIST_DIR) / f.relative_to(lroot)
        folder = base
        for part in relp.parts[:-1]:
            folder = child(folder, part)
        if check:
            if not (folder / f.name).exists():
                n += 1
                lines.append(f"・未保管：{relp}")
            continue
        res = save(f, folder, f.name)
        if not res.startswith("保管済み"):
            n += 1
            lines.append(f"○ {relp} を{res}")
    if not check:
        _stamp().write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")
    return n, lines


AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".aif", ".aiff"}


def numbered(folder: Path, stem: str, src: Path, ext: str = ".jpg") -> str:
    """<stem>_<番号><ext> の名前を決める。同じ中身のものがすでにあればその番号、無ければ次の番号（1, 2, 3 …）"""
    nums = []
    if folder.is_dir():
        pat = re.compile(re.escape(stem) + r"_(\d+)" + re.escape(ext) + "$")
        for f in folder.iterdir():
            m = pat.match(f.name)
            if m:
                if f.samefile(src) or filecmp.cmp(src, f, shallow=False):
                    return f.name
                nums.append(int(m.group(1)))
    return f"{stem}_{max(nums, default=0) + 1}{ext}"


def save(src: Path, folder: Path, name: str, link: bool = False) -> str:
    """folder/name に保存する。同じ中身なら何もしない。中身が変わっていれば前の版を _old を付けて残す。
    link=True は音源だけハードリンク（同じ実体を指す。容量を使わない。できないときは普通に写す）。
    画像などの小さいファイルは、元が上書きされても保管した版が変わらないように普通に写す"""
    dst = folder / name
    verb = "保管"
    if dst.exists():
        if dst.samefile(src) or filecmp.cmp(src, dst, shallow=False):
            return "保管済み（変わりなし）"
        old = dst.with_name(f"{dst.stem}_old{dst.suffix}")
        if old.exists():
            old.unlink()
        dst.rename(old)
        verb = "上書き保管（前の版は _old で残しました）"
    if link and src.suffix.lower() in AUDIO_EXT:
        try:
            os.link(src, dst)
            return verb
        except OSError:
            pass
    shutil.copy2(src, dst)
    return verb


def local_label(folder: Path) -> str:
    """表示用：ローカル保存の中での位置"""
    try:
        return f"{LOCAL_NAME}/{folder.relative_to(local_root())}"
    except ValueError:
        return str(folder)


def label(folder: Path) -> str:
    """表示用：SSD の保管庫の中での位置"""
    base, _ = root()
    try:
        return str(folder.relative_to(base)) if base else str(folder)
    except ValueError:
        return str(folder)

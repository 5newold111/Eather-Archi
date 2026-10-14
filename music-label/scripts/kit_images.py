#!/usr/bin/env python3
"""
デビュー用の作業セット（debut_kit/<日付>/chatgpt_prompts.json）から、ジャケットとアーティスト写真を自動で作る

ChatGPT と同じ OpenAI の画像生成を、正式な窓口（画像 API）から呼び出す。手で ChatGPT に貼る作業の代わり。

流れ：
  1. chatgpt_prompts.json の依頼文に、共通の決まり（顔なし・文字なし・実在の人物に似せない）を足す
  2. 管理表の実在の名前（影響・好きなアーティスト）が依頼文に混ざっていないか確かめる（混ざっていたら止める）
  3. 画像 API で 1 枚ずつ作り、images/<組の ID>_<cover|photo>_raw.png に保存する（鍵が無ければ依頼文だけ書き出す）
  4. 配信の基準（3000×3000・RGB・10MB 未満の JPG）に整えて images/<組の ID>_<cover|photo>.jpg に書き出す
  5. 外付け SSD（DS72）の「Postdog Records/アーティスト情報/<アーティスト名>/写真・ジャケット」に保管する
     （同じ名前のフォルダがあればそこへ。無ければ作る。SSD がつながっていなければ保管だけ飛ばす。_archive.py）
     保管庫での名前は <アーティスト名>_cover.jpg（ジャケット）・_photo.jpg（アーティスト写真）・_photo2.jpg（2 枚目）

ChatGPT の画面で手作りした画像も、images/ に <組の ID>_cover.png のような名前で置いて --fit-only で整えられる。

使い方：
  python3 scripts/kit_images.py                         # いちばん新しい作業セットの 5 組 × 2 枚
  python3 scripts/kit_images.py --only nao_easterly --kind cover
  python3 scripts/kit_images.py --kind photo2           # 2 枚目のアーティスト写真だけ
  python3 scripts/kit_images.py --fit-only              # 画像は作らず、images/ にある画像を整えるだけ
  python3 scripts/kit_images.py --redo                  # 作り直す（今ある画像は _old を付けて残す）
  python3 scripts/kit_images.py --no-archive            # SSD には保管しない

鍵は music-label/.env の OPENAI_API_KEY（会話には貼らない）。画像モデルは generate_visuals.py と同じ選び方。
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _life import real_names  # noqa: E402  管理表の実名（指示文に混ぜない）
import generate_visuals as gv  # noqa: E402  画像 API の呼び出し（モデルの自動選択つき）
from _common import rel  # noqa: E402
import _archive as archive  # noqa: E402  外付け SSD への保管

ROOT = Path(__file__).resolve().parent.parent
KIT_ROOT = ROOT / "debut_kit"
SIDE = 3000                    # 配信の基準に合わせた一辺
MAX_BYTES = 10 * 1024 * 1024   # 10MB 未満
KINDS = {"cover": ("cover", "common_cover", "ジャケット"), "photo": ("photo", "common_photo", "アーティスト写真"),
         "photo2": ("photo2", "common_photo2", "アーティスト写真（2 枚目）")}
SOURCE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def latest_kit() -> Path:
    kits = sorted(p for p in KIT_ROOT.iterdir() if (p / "chatgpt_prompts.json").exists())
    if not kits:
        sys.exit("作業セット（debut_kit/<日付>/chatgpt_prompts.json）が見つかりません")
    return kits[-1]


def check_real_names(slug: str, prompt: str) -> None:
    path = ROOT / "templates" / "artists" / f"{slug}.json"
    if not path.exists():
        return
    hits = [n for n in real_names(json.loads(path.read_text(encoding="utf-8"))) if n.casefold() in prompt.casefold()]
    if hits:
        sys.exit(f"  × {slug} の依頼文に実在の名前が入っています：{', '.join(hits)}。依頼文から外してからやり直してください")


def find_source(images: Path, slug: str, kind: str) -> Path | None:
    """整える元の画像：自動で作った _raw か、手で置いた <組>_<種類>.png など"""
    for stem in (f"{slug}_{kind}_raw", f"{slug}_{kind}"):
        for ext in SOURCE_EXT:
            p = images / f"{stem}{ext}"
            if p.exists() and not (stem == f"{slug}_{kind}" and ext in (".jpg", ".jpeg") and is_fitted(p)):
                return p
    return None


def is_fitted(p: Path) -> bool:
    from PIL import Image
    with Image.open(p) as im:
        return im.size == (SIDE, SIDE) and im.mode == "RGB"


def fit(src: Path, dst: Path) -> str:
    """中央を正方形に切り出し、3000×3000・RGB の JPG にする（10MB を超えたら画質を下げる）"""
    from PIL import Image, ImageFilter
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
        note = "" if s >= SIDE else f"（元は {s}×{s}。拡大したので、細部は元の画像の細かさのまま）"
        im = im.resize((SIDE, SIDE), Image.LANCZOS)
        if s < SIDE:   # 拡大でぼやけた輪郭を少しだけ締める（かけすぎると縁が白く光るので弱め）
            im = im.filter(ImageFilter.UnsharpMask(radius=2.2, percent=70, threshold=3))
        for q in (92, 88, 84, 80, 75):
            im.save(dst, "JPEG", quality=q, optimize=True, progressive=True)
            if dst.stat().st_size < MAX_BYTES:
                return f"{SIDE}×{SIDE}・RGB・{dst.stat().st_size / 1024 / 1024:.1f}MB{note}"
    raise RuntimeError(f"{dst.name} が 10MB 未満になりません")


def main() -> None:
    ap = argparse.ArgumentParser(description="作業セットからジャケットとアーティスト写真を自動で作る")
    ap.add_argument("--kit", type=Path, help="作業セットのフォルダ（省略時はいちばん新しいもの）")
    ap.add_argument("--only", help="この組だけ（組の ID。例 nao_easterly）")
    ap.add_argument("--kind", choices=["cover", "photo", "photo2", "both", "all"], default="all",
                    help="all＝ジャケット・写真・2 枚目の写真（既定）、both＝ジャケットと写真")
    ap.add_argument("--fit-only", action="store_true", help="画像は作らず、images/ にある画像を整えるだけ")
    ap.add_argument("--redo", action="store_true", help="作り直す（今ある画像は _old を付けて残す）")
    ap.add_argument("--no-archive", "--no-drive", dest="no_archive", action="store_true", help="SSD には保管しない")
    args = ap.parse_args()

    kit = args.kit or latest_kit()
    data = json.loads((kit / "chatgpt_prompts.json").read_text(encoding="utf-8"))
    images = kit / "images"
    images.mkdir(exist_ok=True)
    kinds = {"all": ["cover", "photo", "photo2"], "both": ["cover", "photo"]}.get(args.kind, [args.kind])
    acts = [a for a in data["acts"] if not args.only or a["slug"] == args.only]
    if not acts:
        sys.exit(f"組 {args.only} が作業セットにありません")

    has_key = bool(gv.os.environ.get("OPENAI_API_KEY"))
    if not args.fit_only and not has_key:
        print("  OPENAI_API_KEY が見つかりません（music-label/.env）。画像は作らず、依頼文だけ images/prompts.txt に書き出します")

    print(f"▶ 作業セット：{rel(kit)}")
    dry_lines = []
    warned: list[str] = []

    def to_archive(act: dict, kind: str, out: Path) -> None:
        """整えた画像を SSD の アーティスト情報/<アーティスト名>/写真・ジャケット へ"""
        if args.no_archive:
            return
        name = act["artist"]
        folder, how = archive.artist_folder(name, "cover" if kind == "cover" else "photo")
        if folder is None:
            if not warned:
                print(f"    SSD には保管しませんでした：{how}")
                warned.append(how)
            return
        fname = f"{name}_{kind}.jpg"
        print(f"    SSD：{archive.label(folder)}/{fname} を{archive.save(out, folder, fname)}")

    for act in acts:
        slug = act["slug"]
        for kind in kinds:
            key, common_key, ja = KINDS[kind]
            if key not in act:   # この作業セットに無い種類（古い作業セットの photo2 など）
                continue
            prompt = f"{act[key]}{data.get(common_key, '')}"
            check_real_names(slug, prompt)
            raw = images / f"{slug}_{kind}_raw.png"
            out = images / f"{slug}_{kind}.jpg"
            label = f"{act['artist']} の{ja}"

            if not args.fit_only:
                if raw.exists() and args.redo:
                    raw.rename(raw.with_name(f"{raw.stem}_old.png"))
                if not raw.exists():
                    if not has_key:
                        dry_lines.append(f"## {label}（{slug}_{kind}）\n{prompt}\n")
                        continue
                    print(f"  … {label}を作っています")
                    try:
                        gv.generate_image(prompt, raw, size=gv.os.environ.get("OPENAI_IMAGE_SIZE", "1024x1024"))
                    except (RuntimeError, OSError) as e:  # OSError は接続できないとき（ネットワークの制限など）
                        print(f"  × {label}：{e}（api.openai.com に届くか、鍵と残高を確かめる）")
                        continue

            src = find_source(images, slug, kind)
            if not src:
                print(f"  ・{label}：元の画像がまだありません（images/{slug}_{kind}.png などを置く）")
                continue
            if out.exists() and out.stat().st_mtime >= src.stat().st_mtime and src != out:
                print(f"  ○ {label}：整え済み（{out.name}）")
            else:
                print(f"  ○ {label}：{out.name} に整えました（{fit(src, out)}）")
            to_archive(act, kind, out)

    if dry_lines:
        (images / "prompts.txt").write_text("\n".join(dry_lines), encoding="utf-8")
        print(f"  依頼文：{rel(images / 'prompts.txt')}（{len(dry_lines)} 件）")
    print("  次：SSD の アーティスト情報/<アーティスト名>/写真・ジャケット で、顔・文字・透かしが無いか確かめる")


if __name__ == "__main__":
    main()

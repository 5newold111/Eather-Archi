#!/usr/bin/env python3
"""
アーティスト写真を半年ごとに刷新する（人物・メンバーは変えない）

いつ：デビュー曲の配信日から 6 か月ごと（前回の刷新から 6 か月たった組だけ）。毎月 1 日の定期実行（monthly）で確かめる。
どう：
  1. いまのアーティスト写真（前回刷新した写真、無ければデビューの写真）を「参照画像」として画像 API に渡す
  2. 指示文で「同じ人物・同じ人数・同じ体格・髪・肌の色のまま。人を足さない・減らさない・入れ替えない」と固定し、
     変えるのは服装・場所・光・色だけにする
  3. 変える方向は「これから出していく曲」の雰囲気から決める（最近と次の曲のブリーフ＝Suno の指示文・ジャケットの方向、
     参考データベースの傾向）。顔の隠し方・構図の決まり（決定 10・11）はそのまま
  4. 1 枚目（バストアップか腰から上）と 2 枚目（頭からつま先まで）を作り、3000×3000 に整えて
     out/visuals/<組>/photos/<年-月>/<アーティスト名>_photo_<年-月>.jpg・_photo2_<年-月>.jpg に置く。
     ローカル保存と SSD の 02_写真 にも入れる（前の写真は消さない）
  5. Mac に「写真を刷新しました」と知らせる。気に入らなければ --only <組> --force で作り直せる

使い方：
  python3 scripts/refresh_photos.py --due              # 6 か月たった組だけ（定期実行用）
  python3 scripts/refresh_photos.py --list             # 組ごとの前回と次回の刷新日
  python3 scripts/refresh_photos.py --only nao_easterly --force   # 期限に関係なく、この組を作り直す
  python3 scripts/refresh_photos.py --only nao_easterly --dry-run # 指示文だけ書き出す（画像は作らない）

鍵は music-label/.env の OPENAI_API_KEY。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from base64 import b64decode
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_dotenv, notify, read_json, step  # noqa: E402

load_dotenv()
import _archive as archive  # noqa: E402
import _songs  # noqa: E402
import generate_visuals as gv  # noqa: E402
from kit_images import fit  # noqa: E402

PERIOD = timedelta(days=182)   # 半年
KEEP_PEOPLE = ("Use the reference image as the identity reference. Keep exactly the same people: the same number of members, "
               "the same body types, heights, hair, skin tones and overall look. Do not add, remove, swap or merge anyone, "
               "and do not change who they are. Change only the styling (clothes and accessories), the location, the light and the colours. ")
SHOTS = [("photo", gv.FRAMINGS["hip"], gv.CONCEALS["hand"]),
         ("photo2", gv.FRAMINGS["full"], gv.CONCEALS["glasses"])]


def eras_path(slug: str) -> Path:
    return OUT / "visuals" / slug / "photos" / "eras.json"


def debut_date(slug: str) -> date | None:
    """デビュー曲の配信日（publish.py の記録。配信が確かめられた日 → 登録したときの配信日の順）"""
    days = []
    for s in _songs.all_songs():
        if s["slug"] != slug:
            continue
        st = _songs.state(s)
        d = st.get("released") or st.get("release_date")
        if st.get("status") in ("registered", "live") and d:
            try:
                days.append(date.fromisoformat(str(d)[:10]))
            except ValueError:
                pass
    return min(days) if days else None


def last_refresh(slug: str) -> date | None:
    p = eras_path(slug)
    eras = read_json(p).get("eras", []) if p.exists() else []
    return date.fromisoformat(eras[-1]["date"]) if eras else None


def next_due(slug: str) -> date | None:
    base = last_refresh(slug) or debut_date(slug)
    return base + PERIOD if base else None


def current_photo(slug: str) -> Path | None:
    """参照画像：前回刷新した写真 → デビューの作業セットの写真"""
    eras = read_json(eras_path(slug)).get("eras", []) if eras_path(slug).exists() else []
    for e in reversed(eras):
        p = Path(e.get("photo", ""))
        if p.exists():
            return p
    for kit in sorted((ROOT / "debut_kit").glob("*/images"), reverse=True):
        p = kit / f"{slug}_photo.jpg"
        if p.exists():
            return p
    return None


def upcoming_mood(artist: dict) -> str:
    """これから出していく曲の雰囲気（最近と次の曲のブリーフ・参考データベースの傾向）。実名は入れない"""
    slug = artist["slug"]
    briefs = sorted((OUT / "briefs").glob(f"*_{slug}.json"), reverse=True)[:4]
    bits = []
    for p in briefs:
        b = read_json(p)
        for k in ("cover_prompt_seed", "suno_style_prompt"):
            if b.get(k):
                bits.append(str(b[k])[:300])
                break
    if not bits:   # まだ毎週の曲が無い：デビュー曲の方向
        for s in _songs.debut_songs():
            if s["slug"] == slug and s.get("style"):
                bits.append(s["style"][:300])
    dg = OUT / "reference_db" / "digest.json"
    if dg.exists():
        tags = read_json(dg).get("top_tags", [])[:8]
        if tags:
            bits.append("owner's reference moods: " + ", ".join(tags))
    return gv.scrub(" / ".join(bits), artist) if bits else ""


def build_prompt(artist: dict, framing: str, conceal: str, mood: str, season: str) -> str:
    return gv.scrub(
        f"New artist photo for the music act '{artist['name']}' for the {season} era. " + KEEP_PEOPLE
        + gv.appearance_clause(artist)
        + f"Direction for the new era, taken from the songs they will release next: {mood}. "
        + "Keep the act's world but refresh it: " + gv.photo_base(artist) + " "
        + f"Framing: {framing}. " + gv.PHOTO_RULE + " "
        + gv.concealment_clause(conceal.format(prop=gv._prop(artist), light="a low side light")),
        artist)


def edit_image(ref: Path, prompt: str, out_path: Path, size: str = "1024x1536") -> None:
    """参照画像つきで画像を作る（OpenAI の images/edits）。人物を変えないために、いまの写真を渡す"""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY が .env にありません")
    last = ""
    for model in gv.image_model_candidates():
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("model", model), ("prompt", prompt), ("size", size), ("n", "1"),
                     ("quality", os.environ.get("OPENAI_IMAGE_QUALITY", "high"))):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        ctype = "image/png" if ref.suffix.lower() == ".png" else "image/jpeg"
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="{ref.name}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n".encode() + ref.read_bytes() + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        req = urllib.request.Request("https://api.openai.com/v1/images/edits", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=300, context=gv.ssl_context()) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
            if e.code in (400, 404) and "safety" not in last.lower():
                continue   # このモデルでは参照画像つきが使えない → 次の候補
            raise RuntimeError(last) from None
        out_path.write_bytes(b64decode(data["data"][0]["b64_json"]))
        return
    raise RuntimeError(f"参照画像つきで作れる画像モデルが見つかりません（{last}）")


def refresh(artist: dict, dry_run: bool = False) -> bool:
    slug, name = artist["slug"], artist["name"]
    ref = current_photo(slug)
    if ref is None:
        print(f"   － {name}：参照にするいまの写真がありません（先に kit_images.py でデビューの写真を作る）")
        return False
    today = date.today()
    season = today.strftime("%Y-%m")
    mood = upcoming_mood(artist)
    folder = OUT / "visuals" / slug / "photos" / season
    folder.mkdir(parents=True, exist_ok=True)
    made = {}
    for kind, framing, conceal in SHOTS:
        prompt = build_prompt(artist, framing, conceal, mood, season)
        (folder / f"{kind}.prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        if dry_run:
            print(f"   ・{name} の {kind} の指示文：{folder / (kind + '.prompt.txt')}")
            continue
        step(f"{name} の新しい写真（{kind}）を、いまの写真を参照して作っています")
        raw = folder / f"{kind}_raw.png"
        edit_image(ref, prompt, raw)
        dst = folder / f"{name}_{kind}_{season}.jpg"
        print(f"   ○ {dst.name}：{fit(raw, dst)}")
        for line in archive.store(dst, name, "photo", dst.name):
            print(f"     {line}")
        made[kind] = str(dst)
    if dry_run or not made:
        return False
    p = eras_path(slug)
    eras = read_json(p).get("eras", []) if p.exists() else []
    eras.append({"date": today.isoformat(), "season": season, "reference": str(ref), "mood": mood, **made})
    p.write_text(json.dumps({"eras": eras}, ensure_ascii=False, indent=1), encoding="utf-8")
    notify("アーティスト写真を刷新しました", f"{name}（{season}）。同じ人物のまま、次の曲の雰囲気に合わせました")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="アーティスト写真を半年ごとに刷新する（人物・メンバーは変えない）")
    ap.add_argument("--due", action="store_true", help="前回から 6 か月たった組だけ（定期実行用）")
    ap.add_argument("--list", action="store_true", help="組ごとの前回と次回の刷新日")
    ap.add_argument("--only", help="この組だけ（組の ID）")
    ap.add_argument("--force", action="store_true", help="期限に関係なく作る")
    ap.add_argument("--dry-run", action="store_true", help="指示文だけ書き出す")
    a = ap.parse_args()
    today = date.today()
    acts = [x for x in all_artists() if x.get("slug") and (not a.only or x["slug"] == a.only)]
    if a.list or not (a.due or a.only):
        for x in acts:
            d, n = debut_date(x["slug"]), next_due(x["slug"])
            print(f"  {x.get('name', x['slug']):<16} デビュー配信 {d or '—'}  前回の刷新 {last_refresh(x['slug']) or '—'}  次回 {n or '（デビュー後に決まる）'}")
        return
    for x in acts:
        n = next_due(x["slug"])
        if not a.force and (n is None or n > today):
            continue
        artist = gv.load_artist(x["slug"], x.get("label_slug") if x.get("label_slug") not in (None, "drive") else None)
        try:
            refresh(artist, a.dry_run)
        except RuntimeError as e:
            print(f"   ✕ {x.get('name')}：{e}")


if __name__ == "__main__":
    main()

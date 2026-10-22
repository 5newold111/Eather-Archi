#!/usr/bin/env python3
"""
アーティスト写真を、配信サイトのアーティストのページに入れる（決定 32）

DistroKid の登録画面には、アーティスト写真の欄が無い（2026-10 時点）。写真は、配信が始まってアーティストのページができたあとに、
各サイトのアーティスト向けの管理画面で入れる。ここでは：
  1 今のアーティスト写真（refresh_photos.py の最新 → 保管庫の <アーティスト名>_photo.jpg → デビューの作業セット）を、
    サイトごとの形に整える（正方形・Spotify のヘッダーの横長）。小さい写真は引き伸ばさない（Apple の決まり）
  2 browser_fill.py photo <組> <サイト> が専用の Chrome でそのサイトを開く。Gon さんが「写真を変える」などを押すと、
    ファイルを選ぶ画面の代わりに、整えた画像が自動で選ばれる。切り抜きの確認と「保存」は Gon さんが押す（公開されるため）
  3 入れた写真を記録する（out/knowledge/artist_photos.json）。写真を半年ごとに刷新したら「入れ直す」と出る

サイト（アーティストのページを先に受け取る必要がある）：
  spotify … Spotify for Artists（DistroKid の「Special Access」から申し込める）。正方形のプロフィール写真と、横長のヘッダー
  apple   … Apple Music for Artists。正方形（2400×2400 以上がよい・最小 800×800・引き伸ばさない）
  amazon  … Amazon Music for Artists。正方形

使い方：
  python3 scripts/artist_photo.py                  # 組ごと・サイトごとの状態
  python3 scripts/artist_photo.py prepare <組>     # サイト用の画像を作る（out/visuals/<組>/profile/）
  python3 scripts/artist_photo.py done <組> <サイト>  # 入れたことを記録する（ふだんは自動入力の案内の「入れた」で記録）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, all_artists, read_json, write_json  # noqa: E402

RECORD = OUT / "knowledge" / "artist_photos.json"
SERVICES = {
    "spotify": {"name": "Spotify for Artists", "url": "https://artists.spotify.com/",
                "images": {"square": "プロフィール写真（正方形）", "header": "ヘッダー（横長 2660×1140）"},
                "how": ["アーティストのページを受け取っていなければ、DistroKid の「Special Access」→ Spotify for Artists から申し込む",
                        "Spotify for Artists で「プロフィール」→ 写真の「編集」"]},
    "apple": {"name": "Apple Music for Artists", "url": "https://artists.apple.com/",
              "images": {"square": "アーティストの画像（正方形）"},
              "how": ["アーティストのページを受け取っていなければ、Apple Music for Artists で申し込む（審査に数日）",
                      "「プロフィール」→ アーティストの画像の「編集」"]},
    "amazon": {"name": "Amazon Music for Artists", "url": "https://artists.amazonmusic.com/",
               "images": {"square": "アーティストの画像（正方形）"},
               "how": ["アーティストのページを受け取っていなければ、Amazon Music for Artists で申し込む",
                       "プロフィールの画像の「編集」"]},
}
SQUARE_MAX, SQUARE_MIN = 3000, 800
HEADER = (2660, 1140)


def artist_of(slug: str) -> dict | None:
    return next((a for a in all_artists() if a.get("slug") == slug), None)


def current_photo(slug: str) -> Path | None:
    """今のアーティスト写真：半年ごとの刷新の最新 → 保管庫（ローカル・SSD）の <アーティスト名>_photo*.jpg の新しいもの"""
    try:
        import refresh_photos
        p = refresh_photos.current_photo(slug)
        if p:
            return p
    except Exception:  # noqa: BLE001
        pass
    a = artist_of(slug)
    if not a:
        return None
    import _archive as archive
    folders = [archive.local_folder(a["name"], "photo")]
    sf, _ = archive.artist_folder(a["name"], "photo")
    if sf is not None:
        folders.insert(0, sf)
    found = [p for f in folders if f.is_dir() for p in f.glob("*_photo*.jpg") if "profile" not in p.name]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def prepare(slug: str) -> dict[str, Path]:
    """サイト用の画像を作る：square（正方形・引き伸ばさない）と header（Spotify のヘッダー）。{種類: 場所}"""
    from PIL import Image
    src = current_photo(slug)
    if src is None:
        raise FileNotFoundError("アーティスト写真が見つかりません（写真とジャケットを作るか、保管庫の 02_写真 に置いてください）")
    out = OUT / "visuals" / slug / "profile"
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        side = min(w, h)
        sq = im.crop(((w - side) // 2, (h - side) // 4 if h > w else 0, (w - side) // 2 + side, ((h - side) // 4 if h > w else 0) + side))
        if side > SQUARE_MAX:
            sq = sq.resize((SQUARE_MAX, SQUARE_MAX), Image.LANCZOS)
        p = out / f"{src.stem}_square.jpg"
        sq.save(p, "JPEG", quality=92)
        res["square"] = p
        # ヘッダー：横長に切り抜く（上下の真ん中より少し上。顔が無い写真でも、人物の上半身が入りやすい位置）
        hw, hh = HEADER
        cw = w
        ch = round(cw * hh / hw)
        if ch > h:
            ch, cw = h, round(h * hw / hh)
        top = max(0, min(h - ch, round((h - ch) * 0.35)))
        left = (w - cw) // 2
        hd = im.crop((left, top, left + cw, top + ch)).resize(HEADER, Image.LANCZOS)
        p = out / f"{src.stem}_header.jpg"
        hd.save(p, "JPEG", quality=92)
        res["header"] = p
    res["_source"] = src
    res["_small"] = side < SQUARE_MIN
    return res


def record() -> dict:
    return read_json(RECORD) if RECORD.exists() else {}


def mark_done(slug: str, service: str, src: Path | None = None) -> None:
    rec = record()
    src = src or current_photo(slug)
    rec.setdefault(slug, {})[service] = {"photo": str(src) if src else "", "at": datetime.now().isoformat(timespec="seconds")}
    write_json(RECORD, rec)


def unmark(slug: str, service: str) -> bool:
    """間違えて「保存した」を押したとき：記録を取り消す（決定 38）"""
    rec = record()
    if service in rec.get(slug, {}):
        rec[slug].pop(service)
        write_json(RECORD, rec)
        return True
    return False


def status(slug: str) -> dict[str, str]:
    """サイトごと：done（今の写真を入れた）／update（前の写真のまま。入れ直す）／todo（まだ）"""
    rec = record().get(slug, {})
    cur = current_photo(slug)
    out = {}
    for k in SERVICES:
        r = rec.get(k)
        out[k] = "todo" if not r else ("done" if cur is None or r.get("photo") == str(cur) else "update")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="アーティスト写真を配信サイトのアーティストのページに入れる準備と記録")
    ap.add_argument("cmd", nargs="?", default="status", choices=["status", "prepare", "done", "undo"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    if a.cmd == "prepare":
        r = prepare(a.args[0])
        print(f"▶ {a.args[0]}：元の写真 {r['_source']}")
        for k in ("square", "header"):
            print(f"   {k}：{r[k]}")
        if r["_small"]:
            print("   ⚠ 元の写真が 800×800 より小さいので、Apple では断られるかもしれません")
        return
    if a.cmd == "undo":
        ok = unmark(a.args[0], a.args[1])
        print(f"【結果】{a.args[0]} の {SERVICES[a.args[1]]['name']} の写真の記録を" + ("取り消しました" if ok else "取り消せませんでした（記録がありません）"))
        return
    if a.cmd == "done":
        mark_done(a.args[0], a.args[1])
        print(f"○ {a.args[0]} の {SERVICES[a.args[1]]['name']} に写真を入れたと記録しました")
        return
    ja = {"done": "✓ 入れた", "update": "！ 新しい写真に入れ直す", "todo": "○ まだ"}
    for art in all_artists():
        st = status(art["slug"])
        print(f"{art['name']}：" + "／".join(f"{SERVICES[k]['name']} {ja[v]}" for k, v in st.items()))


if __name__ == "__main__":
    main()


def todos() -> list[dict]:
    """「今やること」に出す：配信が始まった組（配信中の曲がある・Spotify のアーティストのページが見つかった）で、
    まだ今の写真を入れていないサイト（決定 32・38）"""
    import _songs
    try:
        import publish
    except Exception:  # noqa: BLE001
        publish = None
    live = {s["slug"] for s in _songs.all_songs() if _songs.state(s).get("status") == "live"}
    out = []
    for art in all_artists():
        slug = art["slug"]
        has_page = slug in live or bool(publish and (publish.artist_links(slug).get("spotify") or {}).get("id"))
        if not has_page or current_photo(slug) is None:
            continue
        st = status(slug)
        left = [{"service": k, "name": SERVICES[k]["name"], "state": v} for k, v in st.items() if v != "done"]
        if left:
            out.append({"slug": slug, "artist": art["name"], "services": left})
    return out


def photos_page(token: str) -> str:
    """ダッシュボードの /photos：組ごと・サイトごとの写真の状態と、「写真を入れる」ボタン"""
    import html
    import song_check
    E = html.escape
    ja = {"done": ("done", "✓ 入れた"), "update": ("ng", "！ 新しい写真に入れ直す"), "todo": ("wait", "○ まだ")}
    secs = []
    for art in all_artists():
        slug = art["slug"]
        src = current_photo(slug)
        if src is None:
            continue
        st = status(slug)
        rows = []
        for k, svc in SERVICES.items():
            cls, txt = ja[st[k]]
            btn = song_check._btn({"action": "fill_photo", "slug": slug, "service": k,
                                   "label": f"{svc['name']} に写真を入れる（ブラウザが開きます）"}, st[k] != "done")
            undo = song_check._btn({"action": "photo_undo", "slug": slug, "service": k, "label": "← 記録を取り消す（間違えて「保存した」を押したとき）",
                                    "confirm": f"{svc['name']} に写真を入れた記録を取り消します。よいですか？"}) if st[k] != "todo" else ""
            rows.append(f'<h4>{E(svc["name"])} <span class="badge {cls}">{E(txt)}</span></h4>{btn}'
                        + song_check._btn({"action": "browser_login", "site": k, "label": "ログイン（初回だけ）"}) + undo)
        secs.append(f'<section><h2>{E(art["name"])}</h2><img class="cover" src="{song_check.furl(src)}" alt="アーティスト写真">'
                    f'<p class="sub">{E(src.name)}</p>{"".join(rows)}</section>')
    body = ("<h1>アーティスト写真を配信サイトに入れる</h1>"
            "<p class='sub'>DistroKid の登録画面には写真の欄が無いので、配信が始まってアーティストのページができたら、各サイトで入れます。"
            "ボタンを押すと専用の Chrome でサイトが開きます。サイトの写真の「編集」「アップロード」を押すと、整えた画像が自動で選ばれます。"
            "切り抜きの確認と「保存」は Gon さんが押します（決定 32）。</p>"
            + ("".join(secs) or "<section><p>アーティスト写真がまだありません。</p></section>"))
    return song_check.PAGE.replace("<title>公開の準備</title>", "<title>アーティスト写真</title>").replace(
        "__BODY__", body).replace("__TOKEN__", token).replace("__AUTO__", "false")

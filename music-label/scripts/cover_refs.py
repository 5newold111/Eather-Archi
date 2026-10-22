#!/usr/bin/env python3
"""
ジャケットの参考：Gon さんが入れた参考曲のアートワークを Web で探し、特徴を言葉にして、ジャケットづくりに使う（決定 35）

参考にする曲（Gon さんがこれまでに入れた曲）：
  1 参考データベース（SSD の「データベース」。reference_db.py が取り込んだ out/references/db-*.json）
  2 管理表の「参考曲の候補」（各組が選んだ実在の曲）
  3 その週のブリーフで各枠に割り当てた参考曲

流れ：
  1 探す   … Spotify の公式の検索（.env に SPOTIFY_CLIENT_ID / SECRET があれば）→ 無ければ Apple（iTunes）の公開の検索で、
             曲名とアーティスト名が一致する曲のジャケット画像を見つける（out/references/artwork/<ID>.jpg。Mac の中だけ。外には出さない）
  2 読む   … Claude が画像を見て、色・画材・構図・光・質感・空気・時代感・文字の扱いを言葉にする（<ID>.json）。
             その絵だけの特徴的なモチーフ（真似してはいけないもの）も書き出す
  3 使う   … ジャケットを作るとき、その組・その曲に近い参考の言葉を 2〜3 枚ぶん混ぜて「アートの方向」にする。
             参考のジャケットの画像そのものは画像を作る AI に渡さない。実在の名前も入れない（docs/04 の借り方と同じ：要素だけ借りる）

使い方：
  python3 scripts/cover_refs.py collect                 # まだ読んでいない参考曲を探して読む（毎週の流れでも自動で動く）
  python3 scripts/cover_refs.py collect --artist tidewool --limit 10
  python3 scripts/cover_refs.py show tidewool           # この組のジャケットに混ぜるアートの方向
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, all_artists, load_dotenv, read_json, write_json  # noqa: E402

load_dotenv()
ART = OUT / "references" / "artwork"
UA = {"User-Agent": "PostdogRecords/1.0 (cover reference lookup)"}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["palette", "medium", "composition", "lighting", "texture", "mood", "era_feel", "typography",
                 "do_not_copy", "transferable"],
    "properties": {
        "palette": {"type": "array", "minItems": 3, "maxItems": 6, "items": {"type": "string"},
                    "description": "主な色（英語の色名と #16 進。例 'faded teal #2f6f73'）"},
        "medium": {"type": "string", "description": "画材・手法（例 35mm film photograph, gouache illustration, collage, 3D render）英語"},
        "composition": {"type": "string", "description": "構図（被写体の位置・余白・視点・切り取り方）英語 1〜2 文"},
        "lighting": {"type": "string", "description": "光（時間帯・方向・コントラスト）英語"},
        "texture": {"type": "string", "description": "質感・仕上げ（粒子・にじみ・紙・つや）英語"},
        "mood": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "string"}, "description": "空気（英語の短い語）"},
        "era_feel": {"type": "string", "description": "時代感（例 late-80s, 2010s indie）英語"},
        "typography": {"type": "string", "description": "文字の扱い（あるか・書体の系統・大きさ・置き場所）英語。名前や曲名そのものは書かない"},
        "do_not_copy": {"type": "array", "items": {"type": "string"},
                        "description": "この絵だけの特徴的なモチーフ・人物・ロゴ・キャラクター（真似してはいけないもの）英語"},
        "transferable": {"type": "string",
                         "description": "ほかの曲のジャケットに借りてよい『考え方』（英語 1〜2 文。具体的なモチーフではなく、色・光・余白・質感の使い方）"},
    },
}
SYSTEM = """You analyse album cover artwork for an independent label's art direction. Describe only visual attributes
that can inspire a NEW, original cover: palette, medium, composition, light, texture, mood, era, typography tendency.
Never write any artist name, song title, album title, brand or text that appears on the cover. List in do_not_copy the
distinctive motifs that make this specific cover recognisable (characters, people, logos, signature objects, layouts),
because those must never be reused. Answer in English, concise."""


def norm(s: object) -> str:
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "", unicodedata.normalize("NFKC", str(s or "")).casefold())


def ref_id(title: str, artist: str) -> str:
    return hashlib.sha1(f"{norm(title)}|{norm(artist)}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 参考にする曲の一覧
def sources(slug: str | None = None) -> list[dict]:
    """{title, artist, from, slugs}。同じ曲は 1 つにまとめる"""
    out: dict[str, dict] = {}

    def add(title, artist, frm, s=None):
        title, artist = str(title or "").strip(), str(artist or "").strip()
        if not title or not artist or artist in ("?", "（架空）") or title.startswith("（"):
            return
        k = ref_id(title, artist)
        e = out.setdefault(k, {"id": k, "title": title, "artist": artist, "from": [], "slugs": []})
        if frm not in e["from"]:
            e["from"].append(frm)
        if s and s not in e["slugs"]:
            e["slugs"].append(s)

    near: dict[str, list[str]] = {}
    for p in sorted((OUT / "reference_db" / "artists").glob("*.json")) if (OUT / "reference_db" / "artists").exists() else []:
        for rid in (read_json(p).get("close") or {}):
            near.setdefault(rid, []).append(p.stem)
    for p in sorted((OUT / "references").glob("db-*.json")) if (OUT / "references").exists() else []:
        r = read_json(p).get("reference") or {}
        add(r.get("title"), r.get("artist_name"), "参考データベース")
        for s in near.get(p.stem, []):
            add(r.get("title"), r.get("artist_name"), "参考データベース", s)
    for a in all_artists():
        for c in (a.get("life") or {}).get("song_choices", []):
            add(c.get("title"), c.get("artist"), "管理表の参考曲の候補", a.get("slug"))
    for p in sorted((OUT / "briefs").glob("*.json")) if (OUT / "briefs").exists() else []:
        if "." in p.stem:
            continue
        b = read_json(p)
        for s in (b.get("slots") or {}).values():
            if s and not str(s.get("reference_id", "")).startswith("demo"):
                add(s.get("title"), s.get("artist_name"), "ブリーフの参考曲", b.get("artist_slug"))
    res = list(out.values())
    return [e for e in res if not slug or slug in e["slugs"]] if slug else res


# ---------------------------------------------------------------------------
# 1 探す
def _get(url: str, headers: dict | None = None) -> bytes:
    from _common import ssl_context
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
        return r.read()


def find_artwork(title: str, artist: str) -> tuple[str | None, str]:
    """(画像の URL, どこで見つけたか)。曲名とアーティスト名が一致するものだけ"""
    try:
        import publish
        tok = publish.spotify_token()
    except Exception:  # noqa: BLE001
        tok = None
    if tok:
        q = urllib.parse.quote(f'track:"{title}" artist:"{artist}"')
        try:
            items = json.loads(_get(f"https://api.spotify.com/v1/search?q={q}&type=track&limit=10",
                                    {"Authorization": f"Bearer {tok}"})).get("tracks", {}).get("items", [])
        except Exception:  # noqa: BLE001
            items = []
        for t in items:
            if norm(t.get("name")).startswith(norm(title)) and any(norm(x.get("name")) == norm(artist) for x in t.get("artists", [])):
                imgs = (t.get("album") or {}).get("images") or []
                if imgs:
                    return imgs[0]["url"], "Spotify"
    q = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
    try:
        items = json.loads(_get(f"https://itunes.apple.com/search?{q}")).get("results", [])
    except Exception:  # noqa: BLE001
        items = []
    for t in items:
        if norm(t.get("trackName")).startswith(norm(title)) and norm(t.get("artistName")) == norm(artist) and t.get("artworkUrl100"):
            return t["artworkUrl100"].replace("100x100bb", "600x600bb"), "Apple Music"
    return None, "見つからない"


# ---------------------------------------------------------------------------
# 2 読む
def analyze(img: Path) -> tuple[dict | None, str]:
    from _claude import call_json, image_block
    return call_json(SYSTEM, [image_block(img), {"type": "text", "text": "Analyse this cover's visual attributes."}],
                     SCHEMA, effort="medium", max_tokens=4000)


def collect(slug: str | None = None, limit: int = 30, quiet: bool = False) -> tuple[int, int]:
    """まだ読んでいない参考曲のジャケットを探して読む。(読んだ数, 見つからなかった数)"""
    ART.mkdir(parents=True, exist_ok=True)
    done = miss = 0
    for e in sources(slug):
        if done + miss >= limit:
            break
        meta_p = ART / f"{e['id']}.json"
        meta = read_json(meta_p) if meta_p.exists() else {}
        if meta.get("analysis") or meta.get("missing_tries", 0) >= 2:
            continue
        url, where = find_artwork(e["title"], e["artist"])
        if not url:
            meta.update({k: e[k] for k in ("title", "artist", "from")}, missing_tries=meta.get("missing_tries", 0) + 1)
            write_json(meta_p, meta)
            miss += 1
            if not quiet:
                print(f"   ？ {e['title']}／{e['artist']}：ジャケットが見つかりません")
            continue
        img = ART / f"{e['id']}.jpg"
        try:
            img.write_bytes(_get(url))
        except Exception as ex:  # noqa: BLE001
            miss += 1
            if not quiet:
                print(f"   ✕ {e['title']}：画像を取れませんでした（{ex}）")
            continue
        res, msg = analyze(img)
        meta.update({k: e[k] for k in ("title", "artist", "from")}, found_on=where, image=img.name, analysis=res)
        write_json(meta_p, meta)
        if res:
            done += 1
            if not quiet:
                print(f"   ○ {e['title']}／{e['artist']}（{where}）：{res['medium']}・{'、'.join(res['mood'][:3])}")
        elif not quiet:
            print(f"   － {e['title']}：画像は取れましたが、Claude が読めませんでした（{msg}）")
    return done, miss


# ---------------------------------------------------------------------------
# 3 使う
def analyses_for(slug: str, brief: dict | None = None, n: int = 3) -> list[dict]:
    """この組・この曲に近い参考のジャケットの読み取り（近い順に n 枚）"""
    pri: dict[str, float] = {}
    for s in ((brief or {}).get("slots") or {}).values():   # この曲で借りた参考曲（世界観・楽器の枠を重く）
        if s and s.get("title"):
            pri[ref_id(s["title"], s.get("artist_name", ""))] = 3.0
    a = next((x for x in all_artists() if x.get("slug") == slug), {}) or {}
    for c in (a.get("life") or {}).get("song_choices", []):   # その組が自分で選んだ参考曲
        pri.setdefault(ref_id(c.get("title"), c.get("artist")), 2.0)
    try:
        import reference_db
        close = reference_db.artist_digest(slug).get("close") or {}
        for p in (OUT / "references").glob("db-*.json"):
            if p.stem in close:
                r = read_json(p).get("reference") or {}
                k = ref_id(r.get("title"), r.get("artist_name"))
                pri[k] = max(pri.get(k, 0), 1.0 + float(close[p.stem]))
    except Exception:  # noqa: BLE001
        pass
    got = []
    for k, w in sorted(pri.items(), key=lambda x: -x[1]):
        p = ART / f"{k}.json"
        if p.exists() and (an := read_json(p).get("analysis")):
            got.append(an)
        if len(got) >= n:
            break
    return got


def art_direction(slug: str, brief: dict | None = None, n: int = 3) -> str:
    """ジャケットの指示に足す「アートの方向」（英語。実在の名前なし。特徴的なモチーフは使わないよう明記）"""
    an = analyses_for(slug, brief, n)
    if not an:
        return ""
    pal = list(dict.fromkeys(c for x in an for c in x["palette"]))[:6]
    mood = list(dict.fromkeys(m for x in an for m in x["mood"]))[:6]
    avoid = list(dict.fromkeys(d for x in an for d in x.get("do_not_copy") or []))[:8]
    lines = [f"Art direction distilled from {len(an)} reference covers the owner chose (attributes only - create a new, original image):",
             f"palette leaning on {', '.join(pal)}; mood: {', '.join(mood)}.",
             "Medium and finish ideas: " + "; ".join(f"{x['medium']} with {x['texture']}" for x in an) + ".",
             "Composition ideas: " + " / ".join(x["composition"] for x in an) + ".",
             "Light: " + " / ".join(x["lighting"] for x in an) + ".",
             "Principles to borrow: " + " ".join(x["transferable"] for x in an)]
    if avoid:
        lines.append("Do NOT reuse these recognisable elements from the references: " + "; ".join(avoid) + ".")
    text = " ".join(lines)
    for e in sources():   # 念のため：参考曲の曲名・アーティスト名が混ざっていたら伏せる（実在の名前は画像の指示に入れない）
        for w in (e["title"], e["artist"]):
            if len(w) >= 4:
                text = re.sub(re.escape(w), "", text, flags=re.I)
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="参考曲のジャケットを探して読み、ジャケットづくりの方向にする")
    ap.add_argument("cmd", choices=["collect", "show", "list"])
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--artist")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.cmd == "collect":
        from _claude import available
        if not available():
            print("－ ANTHROPIC_API_KEY が無いので、ジャケットを読めません（.env に入れてください）")
            return
        d, m = collect(a.artist or a.slug, a.limit, a.quiet)
        print(f"▶ 参考のジャケット：新しく読んだ {d} 枚・見つからなかった {m} 曲（{ART}）")
        return
    if a.cmd == "list":
        for e in sources(a.slug):
            p = ART / f"{e['id']}.json"
            st = "読んだ" if p.exists() and read_json(p).get("analysis") else "まだ"
            print(f"{st}　{e['title']}／{e['artist']}（{'・'.join(e['from'])}）")
        return
    print(art_direction(a.slug or "") or "（まだ読んだジャケットがありません。collect を実行してください）")


if __name__ == "__main__":
    main()

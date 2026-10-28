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
  （足りないとき）その組のジャンルで実際に販売されているアルバム（Apple のジャンル別の人気アルバム）のジャケットも探して読む（決定 114）
  3 使う   … ジャケットの候補は 1 枚ごとに別の参考を 1 枚選び、その「作り方」（画材・構図・光・質感・時代感）を借りる（混ぜない。決定 114）。
             選ぶ順：その曲で借りた参考曲 → 組が選んだ参考曲 → 組に近い参考データベースの曲 → 組の音とビジュアルの言葉に近いもの
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
def _words(*xs) -> set[str]:
    out = set()
    for x in xs:
        if isinstance(x, (list, tuple)):
            out |= _words(*x)
        elif isinstance(x, dict):
            out |= _words(*x.values())
        elif x:
            out |= {w for w in re.findall(r"[a-z][a-z\-]{3,}", str(x).lower())}
    return out - {"with", "from", "that", "this", "into", "very", "soft", "light", "color", "colour"}


def act_words(a: dict) -> set[str]:
    """組の音とビジュアルの言葉（参考ジャケットの近さを測る）"""
    snd, v = a.get("sound") or {}, a.get("visual") or {}
    t = v.get("art_taste") or {}
    return _words(t.get("mediums"), snd.get("genres"), snd.get("dna_tags"), snd.get("palette"), v.get("space"), v.get("light"), v.get("materials"),
                  v.get("palette"), (v.get("photo_direction") or {}).get("taste"), (a.get("persona") or {}).get("scene"),
                  (a.get("lyrics") or {}).get("themes"))


def nearest_by_vibe(slug: str, skip: set[str], n: int) -> list[tuple[str, dict]]:
    """読んだ参考ジャケット全部から、組の音とビジュアルの言葉に近い順（ジャンルで探した分はその組を少し優先）"""
    a = next((x for x in all_artists() if x.get("slug") == slug), {}) or {}
    aw = act_words(a)
    scored = []
    for p in ART.glob("*.json") if ART.exists() else []:
        if p.stem in skip:
            continue
        try:
            m = read_json(p)
        except (OSError, ValueError):
            continue
        an = m.get("analysis")
        if not an:
            continue
        w = _words(an.get("medium"), an.get("texture"), an.get("composition"), an.get("lighting"), an.get("mood"),
                   an.get("era_feel"), an.get("transferable"), an.get("palette"))
        sim = len(aw & w) / max(1, len(aw | w)) + (0.15 if slug in (m.get("for_slugs") or []) else 0)
        scored.append((sim, p.stem, an))
    return [(k, an) for _, k, an in sorted(scored, key=lambda x: -x[0])[:n]]


def analyses_for(slug: str, brief: dict | None = None, n: int = 3, fill: bool = True) -> list[dict]:
    """この組・この曲に近い参考のジャケットの読み取り（近い順に n 枚。足りなければ組の雰囲気に近いもので埋める。決定 114）"""
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
    got, ids = [], set()
    for k, w in sorted(pri.items(), key=lambda x: -x[1]):
        p = ART / f"{k}.json"
        if p.exists() and (an := read_json(p).get("analysis")):
            got.append(an)
            ids.add(k)
        if len(got) >= n:
            break
    if len(got) < n and fill:
        got += [an for _, an in nearest_by_vibe(slug, ids, n - len(got))]
    return got


def _scrub_names(text: str) -> str:
    """参考の曲名・アーティスト名・アルバム名が混ざっていたら伏せる（実在の名前は画像の指示に入れない）"""
    names = [w for e in sources() for w in (e["title"], e["artist"])]
    for p in ART.glob("*.json") if ART.exists() else []:
        try:
            m = read_json(p)
        except (OSError, ValueError):
            continue
        names += [m.get("title", ""), m.get("artist", "")]
    for w in sorted({x for x in names if x and len(x) >= 4}, key=len, reverse=True):
        text = re.sub(re.escape(w), "", text, flags=re.I)
    return text


def direction_one(an: dict) -> str:
    """参考 1 枚の「作り方」だけを借りる指示（英語。実在の名前なし。その絵だけのモチーフは使わないと明記。決定 114）"""
    t = ("Art direction borrowed from ONE real, commercially released album cover that fits this act (borrow only the approach; "
         "create a new, original image): "
         f"medium: {an.get('medium', '')}; finish and texture: {an.get('texture', '')}; composition: {an.get('composition', '')}; "
         f"light: {an.get('lighting', '')}; palette: {', '.join(an.get('palette') or [])}; era feel: {an.get('era_feel', '')}; "
         f"mood: {', '.join(an.get('mood') or [])}. Principle: {an.get('transferable', '')}")
    if an.get("do_not_copy"):
        t += " Do NOT reuse these recognisable elements of that cover: " + "; ".join(an["do_not_copy"]) + "."
    return _scrub_names(t)


# Apple のジャンル（人気アルバムの一覧）。組の音の言葉 → ジャンルの番号
GENRE_IDS = [("city pop", 27), ("j-pop", 27), ("jpop", 27), ("anime", 29), ("k-pop", 51), ("kpop", 51),
             ("hip hop", 18), ("hip-hop", 18), ("rap", 18), ("lo-fi", 18), ("lofi", 18), ("r&b", 15), ("soul", 15), ("neo-soul", 15),
             ("house", 17), ("techno", 17), ("dance", 17), ("edm", 17), ("disco", 17), ("ambient", 7), ("electronic", 7), ("synth", 7),
             ("jazz", 11), ("blues", 2), ("folk", 10), ("acoustic", 10), ("singer-songwriter", 10), ("country", 6),
             ("classical", 5), ("new age", 13), ("reggae", 24), ("latin", 12), ("soundtrack", 16), ("metal", 1153),
             ("punk", 20), ("indie", 20), ("alternative", 20), ("shoegaze", 20), ("post-rock", 20), ("rock", 21), ("pop", 14)]


def genre_ids(a: dict) -> list[tuple[int, str, str]]:
    """(Apple のジャンルの番号, 国, ジャンルの言葉)"""
    words = " ".join(str(x).lower() for x in ((a.get("sound") or {}).get("genres") or []) + ((a.get("sound") or {}).get("dna_tags") or []))
    out = []
    for kw, gid in GENRE_IDS:
        if kw in words and gid not in [g for g, _, _ in out]:
            out.append((gid, "jp" if gid in (27, 29) else "us", kw))
    return out[:3] or [(14, "us", "pop")]


def genre_albums(gid: int, store: str = "us", kw: str = "", limit: int = 40) -> list[dict]:
    """そのジャンルで実際に販売されている人気アルバム（Apple の公開の一覧。使えなければ、ジャンルの言葉で Apple を検索）。{title, artist, art}"""
    out = []
    try:
        d = json.loads(_get(f"https://itunes.apple.com/{store}/rss/topalbums/limit={limit}/genre={gid}/json"))
        for e in (d.get("feed") or {}).get("entry") or []:
            imgs = e.get("im:image") or []
            if imgs:
                out.append({"title": (e.get("im:name") or {}).get("label", ""), "artist": (e.get("im:artist") or {}).get("label", ""),
                            "art": re.sub(r"/\d+x\d+bb", "/600x600bb", imgs[-1].get("label", ""))})
    except Exception:  # noqa: BLE001
        pass
    if out or not kw:
        return out
    q = urllib.parse.urlencode({"term": kw, "media": "music", "entity": "album", "limit": limit, "country": store})
    try:
        for t in json.loads(_get(f"https://itunes.apple.com/search?{q}")).get("results", []):
            if t.get("artworkUrl100") and t.get("collectionName"):
                out.append({"title": t["collectionName"], "artist": t.get("artistName", ""),
                            "art": t["artworkUrl100"].replace("100x100bb", "600x600bb")})
    except Exception:  # noqa: BLE001
        pass
    return out


def collect_genre(slug: str, want: int = 8, max_new: int = 6, quiet: bool = False) -> int:
    """組に近い参考ジャケットが want 枚に足りなければ、組のジャンルの実在アルバムのジャケットを最大 max_new 枚読む（決定 114）"""
    a = next((x for x in all_artists() if x.get("slug") == slug), None)
    if not a:
        return 0
    # 組に確かに合う参考だけを数える：組の参考曲・近い参考データベースの曲（Gon さんが入れた曲）＋ この組のためにジャンルで探した分
    have = sum(1 for p in ART.glob("*.json") if slug in (read_json(p).get("for_slugs") or [])) if ART.exists() else 0
    have += len(analyses_for(slug, None, want, fill=False))
    if have >= want:
        return 0
    ART.mkdir(parents=True, exist_ok=True)
    done = 0
    for gid, store, kw in genre_ids(a):
        for al in genre_albums(gid, store, kw):
            if done >= max_new:
                return done
            k = ref_id(al["title"], al["artist"])
            meta_p = ART / f"{k}.json"
            meta = read_json(meta_p) if meta_p.exists() else {}
            if meta.get("analysis"):
                if slug not in (meta.get("for_slugs") or []):
                    meta.setdefault("for_slugs", []).append(slug)
                    write_json(meta_p, meta)
                continue
            img = ART / f"{k}.jpg"
            try:
                img.write_bytes(_get(al["art"]))
            except Exception:  # noqa: BLE001
                continue
            res, msg = analyze(img)
            meta.update({"title": al["title"], "artist": al["artist"], "from": [f"ジャンルの人気アルバム（Apple・{gid}）"],
                         "found_on": "Apple Music", "image": img.name, "analysis": res, "for_slugs": [slug]})
            write_json(meta_p, meta)
            if res:
                done += 1
                if not quiet:
                    print(f"   ○ {a.get('name')} 向け：{res['medium']}・{'、'.join(res['mood'][:3])}（ジャンルの人気アルバム）")
    return done


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
    return _scrub_names(text)   # 念のため：参考の曲名・アーティスト名が混ざっていたら伏せる（実在の名前は画像の指示に入れない）


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
        g = sum(collect_genre(x["slug"], quiet=a.quiet) for x in all_artists() if not (a.artist or a.slug) or x["slug"] == (a.artist or a.slug))
        print(f"▶ 参考のジャケット：新しく読んだ {d} 枚（ジャンルの人気アルバムから {g} 枚）・見つからなかった {m} 曲（{ART}）")
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

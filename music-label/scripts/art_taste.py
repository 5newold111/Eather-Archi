#!/usr/bin/env python3
"""
組ごとの「アートの好み」（好きな絵柄・テイスト・グラフィック）を下書きして、管理表の「アートの好み」タブに書く（オーナーの指示 2026-10-28。決定 118）

  ・組が足されるたびに（artist_book.py pull のあと自動で）、まだ行の無い組だけを Claude が書く
  ・材料：メンバーの好きなもの・好きな色・服・好きなアーティスト、人物像・物語、音のジャンル・DNA、見た目の設定（空間・光・色・写真の質感）
  ・書く項目：好きな画材・手法／好きな絵柄・モチーフ／テイスト／グラフィック・レイアウト／文字の雰囲気／好きな色／
    好きな写真家・デザイナー・画家・好きな漫画・好きな映画（どれも実在。管理表と設定書のプロフィールだけ。画像の指示には入れない）／
    漫画・映画から借りる画づくり（作品名なし。これはジャケットの指示に入る）／避けたい絵柄・表現／コラボで相手のジャケットに渡す要素
  ・書いた行は『採用』（オーナーの指示）。管理表で直したり『却下』にしたりしてよい（直した値が使われる。却下の組は書き直さない）
  ・Claude の鍵が無いときは、見た目の設定から仮の行を書く（テイストに「仮」と書く）
  ・使うところ：ジャケットの場面選び（cover_scene.py）・参考ジャケットの選び方（cover_refs.py）・ジャケットの指示文（generate_visuals.py）。
    コラボの曲では、客演する組の「コラボで渡す要素」を 3 割ほど混ぜる

使い方：
  python3 scripts/art_taste.py            # まだ行の無い組を書く
  python3 scripts/art_taste.py --if-new   # 書く組が無ければ何もしない（artist_book.py pull が使う）
  python3 scripts/art_taste.py --show     # 組ごとのアートの好み
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, all_artists, load_artist, load_dotenv  # noqa: E402

load_dotenv()

SYSTEM = """You are the art director of a record label. For one virtual music act, write the act's own taste in cover art -
what imagery, look and graphic design this act would choose for its single covers - so that the act's covers feel clearly
different from the label's other acts. Base it on the members' likes, favourite colours, clothes and favourite artists,
the act's story and scene, its music and its visual sheet. Be concrete and specific (materials, techniques, era, layout habits),
not generic. Write mediums in English (they are matched against analyses of real covers) and every other field in Japanese,
except visual_artists_real (names as they are).
Fields: mediums (2-4 techniques, e.g. 35mm film photography, risograph, cut-paper collage), imagery (favourite motifs and subjects),
taste (air, era, finish), graphic (layout habits: margins, placement, shapes, use of borders or grids), type_feel (the feel of
lettering), colors (3-6), visual_artists_real (3-6 real photographers, designers, illustrators or painters these members would
admire - names only), manga_real (2-4 real manga the members would love, written "title (author, year)"), films_real (3-5 real
films the members would love, written "title (director, year)"), from_works (in Japanese, WITHOUT any titles or names: what the
covers borrow from those manga and films - framing, colour grading, light, panel-like composition, the way a single frame tells
a story), avoid (looks this act would never use), collab_touch (one or two elements this act would bring to another act's cover
when it guests).
For visual_artists_real, manga_real and films_real, list only well-known works and people you are certain really exist, with the
exact published title; fit them to each member's age, roots, taste and story (different acts should have clearly different lists)."""

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["mediums", "imagery", "taste", "graphic", "type_feel", "colors", "visual_artists_real", "manga_real", "films_real",
                       "from_works", "avoid", "collab_touch"],
          "properties": {"mediums": {"type": "array", "items": {"type": "string"}}, "imagery": {"type": "string"},
                         "taste": {"type": "string"}, "graphic": {"type": "string"}, "type_feel": {"type": "string"},
                         "colors": {"type": "array", "items": {"type": "string"}},
                         "visual_artists_real": {"type": "array", "items": {"type": "string"}},
                         "manga_real": {"type": "array", "items": {"type": "string"}}, "films_real": {"type": "array", "items": {"type": "string"}},
                         "from_works": {"type": "string"},
                         "avoid": {"type": "array", "items": {"type": "string"}}, "collab_touch": {"type": "string"}}}


def card(a: dict) -> dict:
    """Claude に渡す組の要約（この Claude には実在の好きなアーティストも渡してよい。画像の指示には入れない）"""
    life, v, snd = a.get("life") or {}, a.get("visual") or {}, a.get("sound") or {}
    members = [{k: m.get(k) for k in ("gender", "age", "birthplace", "roots", "fashion", "likes", "like_colors", "dislike_colors",
                                      "fav_artists", "personality", "values", "holidays", "work_now") if m.get(k)} for m in life.get("members", [])]
    return {"formation": a.get("formation"), "members": members,
            "story": (a.get("persona") or {}).get("story"), "scene": (a.get("persona") or {}).get("scene") or (a.get("persona") or {}).get("drive_scene"),
            "music": {k: snd.get(k) for k in ("genres", "dna_tags") if snd.get(k)},
            "visual_sheet": {k: v.get(k) for k in ("space", "light", "materials", "palette", "camera", "cover_series_rule") if v.get(k)},
            "photo_taste": (v.get("photo_direction") or {}).get("taste", "")}


def rough(a: dict) -> dict:
    """鍵が無いとき：見た目の設定から仮の行"""
    v = a.get("visual") or {}
    pd = v.get("photo_direction") or {}
    return {"mediums": ["写真"], "imagery": "、".join(v.get("materials") or []), "taste": f"仮：{pd.get('taste', '')}",
            "graphic": "余白を広く取る", "type_feel": (v.get("logo") or {}).get("typography", ""), "colors": list(v.get("palette") or [])[:5],
            "visual_artists_real": [], "manga_real": [], "films_real": [], "from_works": "",
            "avoid": list((v.get("face_concealment") or {}).get("never") or [])[:4], "collab_touch": ""}


def draft(a: dict) -> tuple[dict, str]:
    import _claude
    if not _claude.available():
        return rough(a), "仮"
    res, st = _claude.call_json(SYSTEM, json.dumps(card(a), ensure_ascii=False), SCHEMA, effort="medium", max_tokens=6000)
    if not res:
        return rough(a), f"仮（Claude で書けませんでした：{st}）"
    return res, "Claude"


def missing(data: dict) -> list[str]:
    from _artist_book import adopted
    have = {r.get("act_id") for r in data.get("art", [])}   # 却下の行がある組も書き直さない
    return [x["id"] for x in adopted(data.get("acts", [])) if x.get("id") and x["id"] not in have]


def run(if_new: bool) -> int:
    try:
        from _artist_book import open_book
        book = open_book()
        data = book.read()
    except Exception as e:  # noqa: BLE001
        if if_new:
            return 0
        print(f"【結果】管理表を読めませんでした（{e}）。SSD をつないでから、もう一度動かしてください")
        return 2
    todo = missing(data)
    if not todo:
        if not if_new:
            print("【結果】アートの好みが無い組はありません")
        return 0
    from _artist_book import append_rows
    print(f"▶ アートの好みを下書きします：{len(todo)} 組")
    rows, today = [], date.today().isoformat()
    for i, aid in enumerate(todo, 1):
        a = load_artist(aid, missing_ok=True)
        if not a:
            continue
        print(f"【進み具合】{i}/{len(todo)}：{a.get('name')}", flush=True)
        r, how = draft(a)
        print(f"   {'・'.join(r.get('mediums') or [])}／{str(r.get('imagery', ''))[:40]}（{how}）")
        rows.append({"act_id": aid, "status": "採用", **{k: r.get(k, "") for k in SCHEMA["required"]}, "updated": today})
    where = append_rows(book, "art", rows)
    print(f"   管理表の「アートの好み」に {len(rows)} 行を足しました：{where}")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "artist_book.py"), "pull", "--if-exists"], capture_output=True)   # 設定書に入れ直す
    print(f"【結果】{len(rows)} 組のアートの好みを書きました（管理表で直してよい）")
    return 0


def show() -> None:
    for a in all_artists():
        t = (a.get("visual") or {}).get("art_taste") or {}
        print(f"■ {a.get('name')}")
        if not t:
            print("   －（まだ書いていません）")
            continue
        print(f"   画材：{'・'.join(t.get('mediums') or [])}\n   絵柄：{t.get('imagery', '')}\n   テイスト：{t.get('taste', '')}"
              f"\n   グラフィック：{t.get('graphic', '')}\n   文字：{t.get('type_feel', '')}\n   色：{'・'.join(t.get('colors') or [])}"
              f"\n   漫画・映画から借りる画づくり：{t.get('from_works', '')}"
              f"\n   避ける：{'・'.join(t.get('avoid') or [])}\n   コラボで渡す：{t.get('collab_touch', '')}")
        pr = a.get("profile") or {}
        if pr.get("favorite_manga_real") or pr.get("favorite_films_real"):
            print(f"   好きな漫画：{'・'.join(pr.get('favorite_manga_real') or [])}\n   好きな映画：{'・'.join(pr.get('favorite_films_real') or [])}")


def main() -> None:
    ap = argparse.ArgumentParser(description="組ごとのアートの好み")
    ap.add_argument("--if-new", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show:
        show()
        return
    sys.exit(run(a.if_new))


if __name__ == "__main__":
    main()

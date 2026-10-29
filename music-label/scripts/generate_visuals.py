#!/usr/bin/env python3
"""
ビジュアル自動生成：ロゴ・顔を出さないアーティスト写真・ジャケットの候補を作り、採点し、選ぶ

流れ：
  1. 設定書（visual / persona / profile）とブリーフから、画像の指示文（プロンプト）を組み立てる
     - 実在アーティスト名（favorite_artists_real、influences.name）は絶対に渡さない
     - 顔を出さない方法（face_concealment）を必ず指示文に含める
  2. 画像 API で候補を生成（OPENAI_API_KEY があれば OpenAI の画像モデル。gpt-image-1 は 2026-10-23 終了のため、
     後継を自動で選ぶ。無ければ指示文だけ書き出す＝ドライラン）
  3. Claude が画像を見て採点（5 観点 1〜5 点）し、顔・他社ロゴ・余計な文字などの禁止ルールに当たる候補を外す
     （ANTHROPIC_API_KEY が無いときは仮の点）
  4. 重み付きの合計点（templates/visual_criteria.json の重み）。5 回の判断がそろったら、Claude が重みと好みを学び直す
  5. 最初の 5 回はオーナーに聞く（review.md を書き出し、--choose で判断を記録 → 基準に反映）
     6 回目以降は自動選択。1 位と 2 位の差が小さいときだけ聞く

使い方：
  python3 scripts/generate_visuals.py --artist light --kind debut              # ロゴ 6 ＋ 写真 8（ドライラン可）
  python3 scripts/generate_visuals.py --artist light --kind cover --brief out/briefs/2026-10-05_light.json
  python3 scripts/generate_visuals.py --label sleep --artist sleep_drone --kind debut
  python3 scripts/generate_visuals.py --artist light --kind debut --choose logo:3 --reason "余白が多く、線が 1 本なのが良い"
  python3 scripts/generate_visuals.py --artist light --kind reshoot --pivot-reason "2 か月連続 down。朝の高速から『夕方の帰路』へ転換"

標準ライブラリだけで動く（画像 API 呼び出しは urllib）。
"""
from __future__ import annotations
import argparse, base64, json, os, re, sys, urllib.error, urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _life import real_names  # noqa: E402  管理表の実名（指示文に混ぜない）

ROOT = Path(__file__).resolve().parent.parent
CRITERIA_PATH = ROOT / "templates" / "visual_criteria.json"

def load_dotenv(path: Path) -> None:
    """music-label/.env があれば読み込んで環境変数にする（既に設定済みのものは上書きしない）"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")


def ssl_context():
    """
    HTTPS の証明書検証に使う設定。
    Mac の python.org 版 Python は証明書を持っていないことがあり、CERTIFICATE_VERIFY_FAILED になる。
    certifi（pip install certifi）が入っていればそれを使い、無ければ標準の設定を使う。
    """
    import ssl
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_HINT = ("証明書の問題です（鍵は関係ありません）。次のどちらかで直ります：\n"
            "       A) pip3 install certifi   を実行してから再実行\n"
            "       B) Finder の「アプリケーション → Python 3.x → Install Certificates.command」をダブルクリック")


# 候補の枚数
N_LOGO, N_PHOTO, N_COVER = 6, 8, 6

# 画像 API に渡してはいけない語（実名・商標に寄るのを防ぐ）
BANNED_IN_PROMPT = ["feat.", "Spotify", "Apple Music", "TikTok", "Instagram"]


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------
def load_artist(slug: str, label: str | None) -> dict:
    if label:
        lab = json.loads((ROOT / "templates" / "labels" / f"{label}.json").read_text(encoding="utf-8"))
        for a in lab["artists"]:
            if a["slug"] == slug:
                a["label_slug"] = label; a["label_visual"] = lab.get("visual", {}); return a
        sys.exit(f"[エラー] レーベル {label} に {slug} がありません")
    p = ROOT / "templates" / "artists" / f"{slug}.json"
    if not p.exists():
        sys.exit(f"[エラー] 設定書がありません: {p}")
    a = json.loads(p.read_text(encoding="utf-8")); a["label_slug"] = "drive"; return a


def load_criteria() -> dict:
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


def save_criteria(c: dict) -> None:
    CRITERIA_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 指示文（プロンプト）
# ---------------------------------------------------------------------------
def _names_of(artist: dict) -> list[str]:
    names = list(artist.get("profile", {}).get("favorite_artists_real", []))
    names += [i.get("name", "") for i in artist.get("persona", {}).get("influences", [])]
    names += real_names(artist)
    return [n for n in names + BANNED_IN_PROMPT if n and n not in ("（bio のみ）",)]


def strip_names(text: str, artist: dict) -> str:
    """Claude が書いた場面などに紛れた実名・禁止語を取り除く（scrub で処理ごと止まらないように。決定 117）"""
    for n in sorted(_names_of(artist), key=len, reverse=True):
        text = re.sub(re.escape(n), "", text, flags=re.I)
    return text


def scrub(text: str, artist: dict) -> str:
    """実名・禁止語を落とす。favorite_artists_real / influences.name は最初から使わないが、念のため検査する"""
    names = list(artist.get("profile", {}).get("favorite_artists_real", []))
    names += [i.get("name", "") for i in artist.get("persona", {}).get("influences", [])]
    names += real_names(artist)   # 管理表の『影響』『参考曲の候補』の実名
    for n in names + BANNED_IN_PROMPT:
        if n and n not in ("（bio のみ）",) and n in text:
            raise RuntimeError(f"指示文に渡してはいけない語が含まれています: {n}")
    return text


def visual_base(artist: dict) -> str:
    v = artist["visual"]
    pd = v.get("photo_direction", {})
    return (
        f"Setting: {v.get('space','')}. Light: {v.get('light','')}. Materials: {', '.join(v.get('materials', []))}. "
        f"Palette: {', '.join(v.get('palette', []))}. Camera: {v.get('camera','')}. "
        f"Taste: {pd.get('taste','')}. Composition: {pd.get('composition','')}. Brightness: {pd.get('brightness','')}, "
        f"contrast: {pd.get('contrast','')}, color temperature: {pd.get('color_temperature','')}. Background: {pd.get('background','')}."
    )


def appearance_clause(artist: dict) -> str:
    """管理表のメンバーの見た目（髪・服・体格・見える傷やタトゥー）。顔は描かないので、後ろ姿やシルエットで伝わる部分だけ"""
    members = (artist.get("life") or {}).get("members", [])
    marks = (artist.get("life") or {}).get("marks", [])
    parts = []
    for m in members:
        hair = " ".join(x for x in (m.get("hair_length"), m.get("hair_color"), m.get("hair_style")) if x)
        body = f"about {m['height']} cm tall" if m.get("height") else ""
        tat = [f"{k.get('what', '')} on the {k.get('where', '')}" for k in marks
               if k.get("member_id") == m.get("id") and k.get("kind") not in ("なし", "", None) and k.get("what")]
        bits = [x for x in (f"{m.get('gender', '')}, around {m['age']}" if m.get("age") else m.get("gender", ""),
                            body, f"hair: {hair}" if hair else "", f"clothes: {m.get('fashion', '')}" if m.get("fashion") else "",
                            f"visible mark: {'; '.join(tat)}" if tat else "") if x]
        if bits:
            parts.append("(" + "; ".join(bits) + ")")
    return ("People (describe through hair, posture, hands and clothes; the eyes stay hidden): " + " ".join(parts) + ". ") if parts else ""


def concealment_clause(method: str) -> str:
    return (f"The person's face is never visible: {method}. No recognizable facial features, no eyes, no identifiable likeness. "
            "No text, no logos, no brand marks, no watermarks. Not a real person.")


# アーティスト写真の決まり（docs/12 の決定 10・11。管理表の face_concealment の「後ろ姿・仮面は使わない」より優先する）
PHOTO_RULE = ("The person is the clear subject and fills the frame; never a small figure in a wide landscape, never only seen from behind. "
              "Hide the eyes and the centre of the face; the jawline, ears and hair may show.")
FRAMINGS = {"bust": "bust-up, from the chest to the top of the head",
            "hip": "hip-up, from the hips to the top of the head",
            "full": "head-to-toe full body, the person filling about 90% of the frame height"}
CONCEALS = {"hand": "one hand raised in front of the face so the palm and fingers cover the eyes",
            "prop": "holding {prop} up in front of the face so it covers the eyes",
            "glasses": "wearing sunglasses that completely hide the eyes",
            "mask": "an original full-head covering or mask designed for this act, not resembling any existing artist's helmet, mask or character",
            "shadow": "a hard shadow falling across the eyes from the key light ({light})"}
PHOTO_PLAN = [(FRAMINGS[f], CONCEALS[c]) for f, c in
              [("bust", "hand"), ("bust", "prop"), ("hip", "glasses"), ("hip", "shadow"),
               ("full", "mask"), ("full", "glasses"), ("bust", "mask"), ("hip", "prop")]]


def _prop(artist: dict) -> str:
    """その組らしい小物（設定書の materials の最初のもの）"""
    mats = artist.get("visual", {}).get("materials", [])
    return mats[0] if mats else "an object from their daily work"


def fc_hint(fc: dict) -> str:
    """管理表の隠し方の副案は、雰囲気の参考として添える（後ろ姿・仮面の禁止は使わない）"""
    sec = [m for m in fc.get("secondary", []) if m][:2]
    return f"Mood references from the act's notes: {'; '.join(sec)}. " if sec else ""


def photo_base(artist: dict) -> str:
    """写真用の場所・光・色。管理表の構図・立ち位置（引きの構図や後ろ姿の指定）は使わない"""
    v = artist["visual"]
    pd = v.get("photo_direction", {})
    return (
        f"Setting: {v.get('space','')}. Light: {v.get('light','')}. Materials: {', '.join(v.get('materials', []))}. "
        f"Palette: {', '.join(v.get('palette', []))}. Camera: {v.get('camera','')}. Taste: {pd.get('taste','')}. "
        f"Brightness: {pd.get('brightness','')}, contrast: {pd.get('contrast','')}, color temperature: {pd.get('color_temperature','')}. "
        f"Background: {pd.get('background','')}."
    )


def prompts_debut(artist: dict) -> list[dict]:
    v, lg = artist["visual"], artist["visual"].get("logo", {})
    fc = v.get("face_concealment", {})
    out = []
    # ロゴ：文字から起こす。既存ロゴは参照しない
    for i in range(N_LOGO):
        variant = ["minimal wordmark", "wordmark with a single geometric mark", "mark-only emblem, wordmark small beneath",
                   "wordmark with generous letter-spacing", "stacked wordmark", "wordmark integrated with the mark"][i]
        out.append({"kind": "logo", "no": i + 1, "prompt": scrub(
            f"Logo design for a music artist named '{artist['name']}'. {variant}. Concept: {lg.get('concept','')}. "
            f"Mark type: {lg.get('mark_type','')}. Color rule: {lg.get('color_rule','')}. Typography: {lg.get('typography','')}. "
            "Flat vector style, high contrast, legible at small size, centered on a plain background, no mockups, no extra text, "
            "original lettering (do not imitate any existing logo).", artist)})
    # 写真：人物が主役（決定 10・11）。構図 3 種類 × 顔の隠し方 5 種類を組み合わせて 8 枚
    for i, (framing, conceal) in enumerate(PHOTO_PLAN[:N_PHOTO]):
        method = conceal.format(prop=_prop(artist), light=v.get("light", "the single key light"))
        out.append({"kind": "photo", "no": i + 1, "method": method, "framing": framing, "prompt": scrub(
            f"Artist photograph for a music act ({artist['formation']}; {len(artist['persona'].get('members', [])) or 1} member(s)). "
            f"{photo_base(artist)} {appearance_clause(artist)}Framing: {framing}. {PHOTO_RULE} "
            f"How the face is hidden: {method}. {fc_hint(fc)}"
            "No text, no logos, no brand marks, no watermarks. Not a real person and not resembling any real person. "
            "Photorealistic editorial music press photo, medium-format film look, 85mm, shallow depth of field, "
            "crisp texture on skin, fabric and hair, high detail, square 1:1.", artist)})
    return out


# 実際に発売された CD・レコードのジャケットに見せる（AI っぽい見た目を避ける。オーナーの指示 2026-10-27。決定 114）
REAL_RELEASE = ("Make it look like the cover of a real album that a record label released and sold on CD and vinyl, not like AI art: "
                "either a real photograph (shot on film or a real camera, natural light falloff, real-world imperfections, visible grain, "
                "slightly imperfect focus and framing) or a real graphic-design layout (print texture, flat inks, paper tooth, restrained shapes). "
                "One clear idea, generous negative space, restrained detail, believable materials. "
                "Avoid the typical AI look: no hyper-detailed digital painting, no glossy plastic sheen, no airbrushed smoothness, "
                "no oversaturated neon gradients, no glowing particles or bokeh orbs, no dreamy fog, no surreal floating objects, "
                "no fantasy concept-art scenery, no perfect symmetry, no lens-flare spectacle.")


def art_taste_clause(artist: dict) -> str:
    """組のアートの好み（管理表の「アートの好み」。実在の名前は入っていない。決定 118）"""
    t = (artist.get("visual") or {}).get("art_taste") or {}
    if not t:
        return ""
    parts = [f"mediums they love: {', '.join(t['mediums'])}" if t.get("mediums") else "",
             f"favourite imagery: {t['imagery']}" if t.get("imagery") else "", f"taste: {t['taste']}" if t.get("taste") else "",
             f"graphic habits: {t['graphic']}" if t.get("graphic") else "", f"colours: {', '.join(t['colors'])}" if t.get("colors") else "",
             f"what they borrow from the comics and films they love: {t['from_works']}" if t.get("from_works") else "",
             f"never: {', '.join(t['avoid'])}" if t.get("avoid") else ""]
    return "This act's own taste in cover art (the cover must feel clearly theirs, not another act's): " + "; ".join(x for x in parts if x) + ". "


def guest_touch(brief: dict) -> str:
    """コラボの曲：客演する組の『コラボで渡す要素』を 3 割ほど（この組の見た目が先。決定 118）"""
    g = brief.get("featured_guest") or {}
    if not g.get("slug"):
        return ""
    try:
        ga = load_artist(g["slug"], None)
    except Exception:  # noqa: BLE001
        return ""
    t = (ga.get("visual") or {}).get("art_taste") or {}
    touch = t.get("collab_touch") or "; ".join(x for x in (t.get("taste"), ", ".join(t.get("mediums") or [])) if x)
    return (f"A guest act joins this song: bring in about a third of the guest's touch ({touch}) while keeping this act's own look first. "
            if touch else "")


def series_look(artist: dict) -> str:
    """組らしさ（色・カメラ・味・光の温度）だけ。場所や小物は入れない（場面は曲ごとに変える。決定 115）"""
    v = artist["visual"]
    pd = v.get("photo_direction", {})
    return (f"palette {', '.join(v.get('palette', []))}; camera feel: {v.get('camera', '')}; taste: {pd.get('taste', '')}; "
            f"contrast {pd.get('contrast', '')}; color temperature {pd.get('color_temperature', '')}")


def _text_room(t: dict) -> str:
    """文字を置く場所（候補ごとのデザイン）→ 画像の AI に空けておいてもらう場所"""
    if t.get("layout") == "split":
        return "a strip along the top edge and another along the bottom edge"
    v = {"top": "upper", "bottom": "lower", "middle": "central"}.get(t.get("place"), "upper or lower")
    h = {"left": "-left", "right": "-right"}.get(t.get("align"), "")
    return f"the {v}{h} part of the image (about a fifth of the height)"


def prompts_cover(artist: dict, brief: dict, brief_path: Path | None = None) -> list[dict]:
    """ジャケットの候補 6 つ = 曲の印象的なシーン 6 つ × 1 案ずつ（オーナーの指示 2026-10-29。決定 122）。
    いちばん先に曲のテーマとイメージ、次にそのシーン（本人・メンバー・曲の人物、寄り・切り取り・引き）、組の好み、実在の CD の作り方 1 枚、
    最後に文字を置く場所を空けておく指示。文字は画像の AI に描かせず、候補ごとのデザインで cover_text.py が入れる"""
    v = artist["visual"]
    rule = v.get("cover_series_rule", "")
    theme = ", ".join(artist.get("lyrics", {}).get("themes", [])[:3])
    seed = brief.get("cover_prompt_seed") or ""
    refs = []
    try:   # 実在の CD ジャケットの読み取り：候補 1 枚ごとに別の 1 枚の「作り方」を借りる（混ぜない。決定 35・114）
        import cover_refs
        import _claude
        if _claude.available():   # 組に合う参考が足りなければ、組のジャンルの人気アルバムのジャケットを先に読む（1 枚 数円）
            cover_refs.collect_genre(artist.get("slug", ""), quiet=True)
        refs = [cover_refs.direction_one(an) for an in cover_refs.analyses_for(artist.get("slug", ""), brief, N_COVER)]
        if not refs:
            seed += " " + cover_refs.art_direction(artist.get("slug", ""), brief)
    except Exception:  # noqa: BLE001
        pass
    scenes, song_img = [], ""
    try:   # 曲の印象的なシーン 6 つ（映画のワンカットのような場面。前の曲と重ならない。決定 115・122）
        import cover_scene
        scenes = cover_scene.scenes_for(brief, brief_path)
        song_img = cover_scene.song_image(brief)
    except Exception:  # noqa: BLE001
        pass
    out = []
    for i in range(N_COVER):
        ref = refs[i % len(refs)] if refs else ""
        sc = scenes[i % len(scenes)] if scenes else None   # 1 シーン = 1 案
        t = dict((sc or {}).get("type") or {})
        try:
            import cover_text
            t = {k: v for k, v in cover_text.merge_design(artist.get("slug", ""), t).items() if k in cover_text.KEYS}
        except Exception:  # noqa: BLE001
            pass
        room = (f"No text or lettering anywhere in the image. Keep {(sc or {}).get('text_space') or _text_room(t)} calm and uncluttered: "
                "the artist name and song title will be set there afterwards as part of the design")
        lead = (f"The song's theme and imagery come first - the cover must make a listener recognise this song: "
                f"{str(song_img or seed or theme).strip().rstrip('.')}. ")
        if sc:   # 曲のシーンが主役。組らしさは色と決まりごとで薄く（決定 115）
            who = ""
            if sc.get("who") == "artist":   # 本人（バンドならメンバー）：アーティスト写真と同じ人に見えるように（決定 117）
                fc = v.get("face_concealment") or {}
                who = (f"This person is the artist themself{(' - ' + sc['member']) if sc.get('member') else ''}, the same person as in "
                       f"the act's photos. {appearance_clause(artist)}"
                       f"Hide the face this way: {fc.get('primary', 'from behind or in silhouette')}. "
                       + (f"Never: {'; '.join(fc.get('never') or [])}. " if fc.get("never") else ""))
            elif sc.get("who") == "other":
                who = "This person is someone from the song's story, not the artist; their face is never shown. "
            body = (lead + cover_scene.scene_text(sc) + who + art_taste_clause(artist) + guest_touch(brief)
                    + (f"Mood notes from the song brief: {seed.strip()} " if seed.strip() and seed.strip() not in lead else "")
                    + f"The act's series look, kept subtle (the song and the scene come first): {series_look(artist)}. "
                    + (f"The act's series habit, only if it fits this scene naturally (the scene always wins): {rule}. " if rule else ""))
        else:
            body = f"{lead}{visual_base(artist)} {art_taste_clause(artist)}{guest_touch(brief)}Series rule: {rule}. Song theme: {theme}. {seed} "
        body, ref = strip_names(body, artist), strip_names(ref, artist)
        out.append({"kind": "cover", "no": i + 1, "ref": i % len(refs) + 1 if refs else None,
                    "scene": i % len(scenes) + 1 if scenes else None, "scene_ja": (sc or {}).get("why_ja", ""),
                    "who": (sc or {}).get("who", ""), "shot": (sc or {}).get("shot", ""), "member": (sc or {}).get("member", ""),
                    "type": t, "type_ja": (sc or {}).get("type_ja", ""), "prompt": scrub(
            f"Album cover artwork, square 1:1. {body}{ref} {room}"
            ". No people's faces, no URLs, no social handles, no prices, no third-party logos or characters, no real buildings or signs, "
            "not explicit. Original artwork, not a copy of any existing cover. " + REAL_RELEASE, artist)})
    return out


def text_previews(cands: list[dict], outdir: Path, artist: dict, brief_path: Path | None) -> int:
    """候補ごとに、そのデザインでアーティスト名と曲名を入れた見本（cover_NN_text.jpg）を作る（決定 122）。
    元の画像（cover_NN.png）は文字なしのまま残し、選んだあとの仕上げで、同じデザインで 3000×3000 に入れ直す"""
    import cover_text
    import _songs
    song = _songs.by_key(brief_path.stem) if brief_path else None
    brief = json.loads(brief_path.read_text(encoding="utf-8")) if brief_path and brief_path.exists() else {}
    title = (song or {}).get("title") or (brief.get("title_candidates") or [""])[0]
    name = (song or {}).get("artist") or artist.get("name", "")
    if not title or not name:
        return 0
    n = 0
    for c in cands:
        src = outdir / c["file"] if c.get("file") else None
        if c.get("kind") != "cover" or not src or not src.exists():
            continue
        prev = outdir / f"cover_{c['no']:02d}_text.jpg"
        r = cover_text.apply(src, name, title, artist.get("slug", ""), out=prev, design=c.get("type") or {}, keep_notext=False)
        if r.get("ok"):
            c["preview"] = prev.name
            c["type"] = r.get("design") or c.get("type")
            n += 1
    return n


def prompts_reshoot(artist: dict, pivot_reason: str) -> list[dict]:
    """方針転換後の撮り直し。設定書はすでに新しい visual に書き換えられている前提"""
    ps = prompts_debut(artist)
    for p in ps:
        p["pivot_reason"] = pivot_reason
    return [p for p in ps if p["kind"] == "photo"] + [p for p in ps if p["kind"] == "logo"][:2]   # 写真 8 ＋ ロゴ微調整 2


# ---------------------------------------------------------------------------
# 画像生成（ChatGPT 側の画像 API）
# ---------------------------------------------------------------------------
# gpt-image-1 は 2026-10-23 に提供終了。後継の候補を上から順に試し、使えたものを .env の OPENAI_IMAGE_MODEL に覚える
IMAGE_MODELS = ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-1.5", "gpt-image-1"]
_BAD_MODELS: set[str] = set()
_NO_QUALITY: set[str] = set()   # quality の指定を受け付けなかったモデル


def image_model_candidates() -> list[str]:
    first = os.environ.get("OPENAI_IMAGE_MODEL")
    return [m for m in dict.fromkeys(([first] if first else []) + IMAGE_MODELS) if m not in _BAD_MODELS]


def _remember_model(model: str) -> None:
    if os.environ.get("OPENAI_IMAGE_MODEL") == model:
        return
    os.environ["OPENAI_IMAGE_MODEL"] = model
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [l for l in lines if not l.startswith("OPENAI_IMAGE_MODEL=")] + [f"OPENAI_IMAGE_MODEL={model}"]
    env.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    print(f"    （画像モデル {model} を使います。.env の OPENAI_IMAGE_MODEL に記録しました）")


def generate_image(prompt: str, out_path: Path, size: str = "1024x1024") -> bool:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return False
    quality = os.environ.get("OPENAI_IMAGE_QUALITY", "high")   # 画質（high / medium / low）。high がいちばん細部まで描く
    for model in image_model_candidates():
        payload = {"model": model, "prompt": prompt, "size": size, "n": 1}
        if quality and model not in _NO_QUALITY:
            payload["quality"] = quality
        body = json.dumps(payload).encode()
        req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300, context=ssl_context()) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code == 400 and "quality" in msg.lower() and model not in _NO_QUALITY:   # 画質の指定に対応しないモデル → 指定なしでやり直す
                _NO_QUALITY.add(model)
                return generate_image(prompt, out_path, size)
            # モデルが無い・終了した・この鍵では使えない → 次の候補へ
            if e.code in (404, 400) and re.search(r"model|does not exist|not found|deprecated", msg, re.I) and "safety" not in msg.lower():
                _BAD_MODELS.add(model)
                print(f"    （画像モデル {model} は使えないので次の候補を試します）")
                continue
            raise RuntimeError(f"HTTP {e.code}: {msg}") from None
        _remember_model(model)
        out_path.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
        return True
    raise RuntimeError("使える画像モデルが見つかりません（OpenAI の Models 画面で利用できる画像モデルを確認し、.env の OPENAI_IMAGE_MODEL に書く）")


# ---------------------------------------------------------------------------
# チェックと採点
# ---------------------------------------------------------------------------
def compliance_checklist(kind: str) -> list[str]:
    common = ["顔が判別できない（目元と顔の中央は隠れている。輪郭・顎・耳・髪は見えてよい）", "第三者のロゴ・商標・キャラクターなし", "実在の建物・看板が特定できない", "露骨な性的・残虐表現なし"]
    if kind == "cover":
        return common + ["3000×3000 に拡大しても粗くない", "URL・SNS 名・価格・宣伝文なし", "文字があれば曲名 / アーティスト名と一致"]
    if kind == "logo":
        return ["既存ロゴに似ていない（文字から起こしている）", "小さくしても読める", "第三者の商標に似ていない"]
    return common + ["Instagram / TikTok で危険行為に見えない"]


SCORE_SYSTEM = """You are the art director of EtherArchi, a music label where AI-generated music meets spatial design.
Score each candidate image for the given artist on five criteria from 1 (poor) to 5 (excellent):
consistency (fits the artist's visual sheet), symbolism (memorable, says something about the act),
scene_fit (fits the listening scene / the song), safety (platform rules and the face-hiding rule),
craft (finish, and whether it looks like a real album cover a label released on CD - a real photograph or real graphic design.
Score craft 1-2 when it looks AI-generated: hyper-detailed, glossy or airbrushed surfaces, neon glow, floating surreal objects,
fantasy concept art, fog and light particles).
Then check every hard rule and the compliance list; list each violation you can SEE (a recognizable face, a third-party
logo or character, a URL / handle / price, text that is not exactly the song title or artist name, an identifiable real
building or sign, sexual or violent content). compliance_ok is false if there is any violation.
Apply the owner's learned preferences when scoring. comment_ja is one short Japanese sentence for the owner."""

SCORE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["candidates"],
    "properties": {"candidates": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["no", "scores", "violations", "compliance_ok", "comment_ja"],
        "properties": {
            "no": {"type": "integer"},
            "scores": {"type": "object", "additionalProperties": False,
                       "required": ["consistency", "symbolism", "scene_fit", "safety", "craft"],
                       "properties": {k: {"type": "integer", "minimum": 1, "maximum": 5}
                                      for k in ["consistency", "symbolism", "scene_fit", "safety", "craft"]}},
            "violations": {"type": "array", "items": {"type": "string"}},
            "compliance_ok": {"type": "boolean"},
            "comment_ja": {"type": "string"}}}}},
}


def thumbnail(src: Path) -> Path:
    """採点用に 768px の JPEG に縮める（送る量を減らす）。ffmpeg が無ければ元の画像"""
    import shutil, subprocess
    if not shutil.which("ffmpeg"):
        return src
    dst = src.with_name(src.stem + ".thumb.jpg")
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "scale=768:-2", "-q:v", "4", str(dst)], check=False)
    return dst if dst.exists() else src


def claude_score(cands: list[dict], outdir: Path, artist: dict, criteria: dict, brief: dict | None) -> int:
    """生成した画像を Claude が見て 5 観点で採点し、禁止ルールに当たる候補を外す。戻り値：採点した枚数"""
    import _claude  # 同じフォルダ。ANTHROPIC_API_KEY が無ければ何もしない
    if not _claude.available():
        return 0
    done = 0
    for kind in sorted({c["kind"] for c in cands}):
        group = [c for c in cands if c["kind"] == kind and c.get("file") and (outdir / c["file"]).exists()]
        if not group:
            continue
        safe_artist = {k: artist.get(k) for k in ("name", "formation", "visual")}
        safe_artist["scene"] = (artist.get("persona") or {}).get("drive_scene") or (artist.get("persona") or {}).get("scene")
        content: list = [{"type": "text", "text": json.dumps({
            "kind": kind, "artist": safe_artist,
            "song": {k: (brief or {}).get(k) for k in ("title", "cover_prompt_seed")} if kind == "cover" else None,
            "hard_rules": criteria.get("hard_rules", []), "compliance": compliance_checklist(kind),
            "owner_preferences": criteria.get("preferences", [])}, ensure_ascii=False)}]
        for c in group:
            content += [{"type": "text", "text": f"Candidate {c['no']}:"}, _claude.image_block(thumbnail(outdir / (c["preview"] if c.get("preview") and (outdir / c["preview"]).exists() else c["file"])))]
        print(f"  Claude が {kind} の候補 {len(group)} 枚を見て採点しています…")
        res, msg = _claude.call_json(SCORE_SYSTEM, content, SCORE_SCHEMA, effort="medium")
        if not res:
            print(f"    採点できませんでした（{msg}）。仮の点のままにします")
            continue
        by_no = {r["no"]: r for r in res["candidates"]}
        for c in group:
            r = by_no.get(c["no"])
            if not r:
                continue
            c["scores"] = {k: float(v) for k, v in r["scores"].items()}
            c["violations"], c["compliance_ok"], c["claude_note"] = r["violations"], r["compliance_ok"], r["comment_ja"]
            c.pop("scores_note", None)
            done += 1
    return done


LEARN_SYSTEM = """You maintain the scoring criteria for an art director's taste. From the owner's past choices
(what they picked, the scores the machine gave every candidate at the time, and the reason they wrote), adjust the five
weights so that the machine would have ranked the owner's pick first more often, and distill short Japanese preference
notes (each one concrete and checkable, e.g. 余白が多い構図を優先). Weights must be between 0.05 and 0.5 and sum to 1.0."""

LEARN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["weights", "preferences", "note_ja"],
    "properties": {
        "weights": {"type": "object", "additionalProperties": False,
                    "required": ["consistency", "symbolism", "scene_fit", "safety", "craft"],
                    "properties": {k: {"type": "number"} for k in ["consistency", "symbolism", "scene_fit", "safety", "craft"]}},
        "preferences": {"type": "array", "items": {"type": "string"}},
        "note_ja": {"type": "string"}},
}


def learn_criteria(criteria: dict) -> None:
    """オーナーの判断（最初の 5 回）から、採点の重みと好みのメモを Claude が更新する"""
    import _claude
    user = json.dumps({"current_weights": criteria["weights"], "decisions": criteria["decisions"],
                       "current_preferences": criteria.get("preferences", [])}, ensure_ascii=False, indent=1)
    if not _claude.available():
        f = ROOT / "out" / "visuals" / "learn_criteria.prompt.md"
        _claude.dry_run_file(f, LEARN_SYSTEM, user, LEARN_SCHEMA)
        print(f"  ANTHROPIC_API_KEY が無いので、基準の更新は依頼文だけ書き出しました: {f.relative_to(ROOT)}")
        return
    print("  オーナーの 5 回の判断から、採点の重みと好みを更新しています…")
    res, msg = _claude.call_json(LEARN_SYSTEM, user, LEARN_SCHEMA)
    if not res:
        print(f"  基準を更新できませんでした（{msg}）")
        return
    w = {k: min(0.5, max(0.05, float(v))) for k, v in res["weights"].items()}
    total = sum(w.values())
    old = dict(criteria["weights"])
    criteria["weights"] = {k: round(v / total, 3) for k, v in w.items()}
    criteria["preferences"] = list(dict.fromkeys(res["preferences"]))[:12]
    criteria["version"] = int(criteria.get("version", 1)) + 1
    criteria.setdefault("learning_log", []).append({"date": date.today().isoformat(), "from": old,
                                                    "to": criteria["weights"], "note": res["note_ja"]})
    save_criteria(criteria)
    print(f"  基準を更新しました（版 {criteria['version']}）：{res['note_ja']}")


def score_candidate(c: dict, criteria: dict) -> float:
    """
    採点。本番では Claude が画像を見て 5 観点に 1〜5 点をつける（c['scores'] に入れる）。
    ここでは scores が無ければ指示文の充実度から仮の点を入れる（ドライラン用）。
    """
    if "scores" not in c:
        base = 3.0 + min(1.5, len(c["prompt"]) / 600)
        c["scores"] = {k: round(base, 1) for k in criteria["weights"]}
        c["scores_note"] = "仮の点（画像未生成）。Claude が画像を見て付け直す"
    total = sum(criteria["weights"][k] * c["scores"].get(k, 0) for k in criteria["weights"])
    if c.get("compliance_ok") is False:
        total = 0.0      # 禁止ルールに当たる候補は除外（0 点）
    c["total"] = round(total, 3)
    return c["total"]


# ---------------------------------------------------------------------------
# 選択（最初の 5 回は聞く → 基準を学ぶ → 自動）
# ---------------------------------------------------------------------------
def decide(cands: list[dict], criteria: dict, ask_forced: bool = False) -> tuple[dict | None, str]:
    ranked = sorted([c for c in cands if c.get("compliance_ok") is not False], key=lambda c: -c["total"])
    if not ranked:
        return None, "全候補が禁止ルールに当たったので作り直しが必要"
    if len(ranked) < 2:
        return (ranked[0] if ranked else None), "候補が 1 つ"
    margin = ranked[0]["total"] - ranked[1]["total"]
    if not criteria["locked"] or ask_forced:
        return None, f"オーナーに聞く（{criteria['decisions_asked']}/{criteria['decisions_required_before_lock']} 回目）"
    if margin < criteria["low_confidence_margin"]:
        return None, f"確信度が低い（1 位と 2 位の差 {margin:.2f} < {criteria['low_confidence_margin']}）ので聞く"
    return ranked[0], f"自動選択（差 {margin:.2f}）"


def record_choice(criteria: dict, artist: str, kind: str, chosen_no: int, reason: str, cands: list[dict]) -> None:
    criteria["decisions"].append({"date": date.today().isoformat(), "artist": artist, "kind": kind, "chosen": chosen_no,
                                  "reason": reason, "scores_at_time": {c["no"]: c["total"] for c in cands if c["kind"] == kind}})
    if reason:
        criteria["preferences"].append(reason)
    criteria["decisions_asked"] += 1
    just_locked = False
    if criteria["decisions_asked"] >= criteria["decisions_required_before_lock"] and not criteria["locked"]:
        criteria["locked"] = True
        criteria["_locked_note"] = f"{date.today()} に 5 回の判断がそろったので自動選択に切り替え"
        just_locked = True
    save_criteria(criteria)
    if just_locked:
        learn_criteria(criteria)   # 5 回の判断から重みと好みを学ぶ


def write_review(path: Path, artist: dict, cands: list[dict], note: str) -> None:
    lines = [f"# ビジュアル候補レビュー：{artist['name']}（{artist['slug']}）", "", f"判断：{note}", ""]
    for kind in sorted({c["kind"] for c in cands}):
        lines += [f"## {kind}", "", "| # | 点 | 方法 / 変種 | ファイル | Claude の所見 | 規約チェック |", "|---|---|---|---|---|---|"]
        for c in sorted([c for c in cands if c["kind"] == kind], key=lambda c: -c["total"]):
            check = ("✕ " + "、".join(c.get("violations", []))) if c.get("compliance_ok") is False else (
                "○" if c.get("compliance_ok") else "／".join(compliance_checklist(kind)))
            lines.append(f"| {c['no']} | {c['total']} | {c.get('method', '')} | {c.get('file', '（未生成：指示文のみ）')} | "
                         f"{c.get('claude_note', c.get('scores_note', ''))} | {check} |")
        lines.append("")
    lines += ["## 選ぶとき", "", "`--choose <kind>:<番号> --reason \"一言\"` で記録してください。理由が基準に反映されます。", ""]
    lines += ["## 指示文（確認用）", ""] + [f"- **{c['kind']} {c['no']}**: {c['prompt']}" for c in cands]
    path.write_text("\n".join(lines), encoding="utf-8")


REVIEW_CSS = """
:root{--bg:#f6f4ef;--card:#fff;--ink:#22201c;--sub:#6f6a60;--line:#e2ddd2;--accent:#a07a3c;--ng:#b3412e}
@media (prefers-color-scheme: dark){:root{--bg:#181715;--card:#22211e;--ink:#ece8df;--sub:#a49e92;--line:#34322d;--accent:#d2ad6b;--ng:#e07a66}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 -apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 64px}h1{font-weight:500;letter-spacing:.04em;margin:0 0 4px}
.note{color:var(--sub);margin:0 0 28px}h2{font-weight:500;border-bottom:1px solid var(--line);padding-bottom:6px;margin:36px 0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(240px,100%),1fr));gap:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;aspect-ratio:1/1;object-fit:cover;display:block;background:var(--line)}
.empty{aspect-ratio:1/1;display:flex;align-items:center;justify-content:center;color:var(--sub);font-size:13px;padding:12px;text-align:center}
.body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:6px;flex:1}
.top{display:flex;justify-content:space-between;align-items:baseline}.no{font-weight:600}.score{color:var(--accent);font-variant-numeric:tabular-nums}
.meta{color:var(--sub);font-size:13px}.ng{color:var(--ng);font-size:13px}.chosen{outline:2px solid var(--accent)}
input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--ink);font:inherit;font-size:14px}
button{margin-top:auto;padding:9px 10px;border:1px solid var(--accent);background:transparent;color:var(--accent);border-radius:6px;font:inherit;cursor:pointer}
button:hover{background:var(--accent);color:var(--card)}code{font-size:12px;word-break:break-all;color:var(--sub)}
"""


def write_review_html(path: Path, artist: dict, cands: list[dict], note: str, kind_arg: str, brief: Path | None) -> None:
    """候補を並べて見比べ、理由を書いて『選ぶ』を押すとコマンドがコピーされるページ（ブラウザで開く）"""
    import html as _h
    base = f"python3 scripts/generate_visuals.py --artist {artist['slug']} --kind {kind_arg}"
    if artist.get("label_slug") and artist["label_slug"] != "drive":
        base += f" --label {artist['label_slug']}"
    if brief:
        try:
            base += f" --brief {brief.resolve().relative_to(ROOT)}"
        except ValueError:
            base += f" --brief {brief}"
    chosen = {}
    for sp in path.parent.glob("selection_*.json"):
        d = json.loads(sp.read_text(encoding="utf-8"))
        chosen[d.get("kind")] = int(d.get("chosen", 0))
    parts = [f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>候補 {_h.escape(artist['name'])}</title><style>{REVIEW_CSS}</style></head><body><main>",
             f"<h1>{_h.escape(artist['name'])}</h1><p class='note'>{_h.escape(note)}。理由を一言書いて「選ぶ」を押すと、"
             "記録用のコマンドがコピーされます。ターミナルに貼って Enter。</p>"]
    for kind in sorted({c["kind"] for c in cands}):
        parts.append(f"<h2>{_h.escape(kind)}</h2><div class='grid'>")
        for c in sorted([c for c in cands if c["kind"] == kind], key=lambda c: -c["total"]):
            img = (f"<img src='{_h.escape(c.get('preview') or c['file'])}' alt='{kind} {c['no']}'>" if c.get("file")
                   else "<div class='empty'>未生成（指示文のみ）</div>")
            ng = f"<div class='ng'>除外：{_h.escape('、'.join(c.get('violations', [])))}</div>" if c.get("compliance_ok") is False else ""
            cmd = f"{base} --choose {kind}:{c['no']} --reason"
            sel = " chosen" if chosen.get(kind) == c["no"] else ""
            parts.append(
                f"<div class='card{sel}'>{img}<div class='body'><div class='top'><span class='no'>#{c['no']}</span>"
                f"<span class='score'>{c['total']} 点</span></div>"
                f"<div class='meta'>{_h.escape(c.get('method', '') or '')}</div>"
                f"<div class='meta'>{_h.escape(c.get('claude_note', c.get('scores_note', '')) or '')}</div>{ng}"
                f"<input placeholder='選んだ理由（例：余白が多く線が細い）' data-cmd='{_h.escape(cmd)}'>"
                f"<button onclick='pick(this)'>{'選択済み' if sel else 'これを選ぶ'}</button></div></div>")
        parts.append("</div>")
    parts.append("""<script>
function pick(btn){const i=btn.parentElement.querySelector('input');const r=(i.value||'').replace(/"/g,'”');
const cmd=i.dataset.cmd+' "'+(r||'理由なし')+'"';const done=()=>{btn.textContent='コピーしました';setTimeout(()=>btn.textContent='これを選ぶ',2500)};
if(navigator.clipboard){navigator.clipboard.writeText(cmd).then(done,()=>fallback(cmd,done))}else{fallback(cmd,done)}}
function fallback(t,done){const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();
try{document.execCommand('copy');done()}catch(e){prompt('このコマンドをコピーしてください',t)}a.remove()}
</script></main></body></html>""")
    path.write_text("".join(parts), encoding="utf-8")
    write_review_index()


def write_review_index() -> None:
    """全組の候補ページへの入り口（out/visuals/index.html）"""
    import html as _h
    root = ROOT / "out" / "visuals"
    rows = []
    for page in sorted(root.glob("*/*/review.html")) + sorted(root.glob("*/cover/*/review.html")):
        rel = page.relative_to(root).as_posix()
        folder = page.parent
        kinds = {json.loads(p.read_text(encoding="utf-8")).get("kind") for p in folder.glob("selection_*.json")}
        cands = json.loads((folder / "candidates.json").read_text(encoding="utf-8")) if (folder / "candidates.json").exists() else []
        need = sorted({c["kind"] for c in cands} - kinds)
        state = "選択済み" if not need else f"未選択：{'、'.join(need)}"
        rows.append(f"<li><a href='{_h.escape(rel)}'>{_h.escape(rel.replace('/review.html', ''))}</a> <span class='meta'>— {state}</span></li>")
    (root / "index.html").write_text(
        f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>ビジュアル候補</title><style>{REVIEW_CSS} li{{margin:8px 0}} a{{color:var(--accent)}}</style></head><body><main>"
        f"<h1>ビジュアル候補</h1><p class='note'>組ごとのページで見比べて選びます。最初の 5 回の理由が、以後の自動採点の基準になります。</p>"
        f"<ul>{''.join(rows) or '<li>まだ候補がありません</li>'}</ul></main></body></html>", encoding="utf-8")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="ロゴ・顔を出さないアーティスト写真・ジャケットの候補を生成して選ぶ")
    ap.add_argument("--artist", required=True)
    ap.add_argument("--label", help="子レーベルの slug（子レーベルの組のとき）")
    ap.add_argument("--kind", choices=["debut", "cover", "reshoot"], required=True)
    ap.add_argument("--brief", type=Path, help="cover のときのブリーフ JSON")
    ap.add_argument("--pivot-reason", default="", help="reshoot のときの転換理由")
    ap.add_argument("--choose", help="オーナーの選択。例 logo:3 / photo:5 / cover:2")
    ap.add_argument("--reason", default="", help="選んだ理由（一言。基準に反映される）")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "visuals")
    ap.add_argument("--skip-name-check", action="store_true", help="名前の重複確認を飛ばす（テスト用。本番では使わない）")
    args = ap.parse_args()

    artist = load_artist(args.artist, args.label)
    # デビュー処理の最初に名前の重複確認。checked でなければロゴ・写真を作らない（名前が変わると全部作り直しになる）
    if args.kind == "debut" and not args.choose and not args.skip_name_check and artist.get("name_status") != "checked":
        sys.exit(f"[停止] {artist['name']} の名前はまだ重複確認が済んでいません（name_status={artist.get('name_status')}）。\n"
                 f"       先に python3 scripts/check_names.py --artist {artist['slug']}" + (f" --label {args.label}" if args.label else "") +
                 " --apply を実行し、report.md の手動確認（商標・SNS）も済ませてください。")
    criteria = load_criteria()
    outdir = args.out / artist["slug"] / args.kind
    if args.kind == "cover" and args.brief:
        # ジャケットは週ごとに分ける（前の週の候補を上書きしない）
        outdir = outdir / json.loads(args.brief.read_text(encoding="utf-8")).get("week_start", "undated")
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"=== ビジュアル生成：{artist['name']}（{args.kind}）===")
    print(f"  顔を見せない方法：主「{artist['visual'].get('face_concealment',{}).get('primary','')}」")

    # 既存の候補があれば読み（選択の記録用）、無ければ指示文を作る
    cand_path = outdir / "candidates.json"
    if args.choose and cand_path.exists():
        cands = json.loads(cand_path.read_text(encoding="utf-8"))
        kind, no = args.choose.split(":"); no = int(no)
        chosen = next((c for c in cands if c["kind"] == kind and c["no"] == no), None)
        if not chosen:
            sys.exit(f"[エラー] 候補 {args.choose} がありません")
        record_choice(criteria, artist["slug"], kind, no, args.reason, cands)
        (outdir / f"selection_{kind}.json").write_text(json.dumps({"kind": kind, "chosen": no, "reason": args.reason, "by": "owner",
                                                            "date": date.today().isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  選択を記録しました：{kind} #{no}（{args.reason}）")
        print(f"  基準の学習：{criteria['decisions_asked']}/{criteria['decisions_required_before_lock']} 回。"
              f"{'自動選択に切り替わりました' if criteria['locked'] else 'まだオーナーに聞きます'}")
        write_review_html(outdir / "review.html", artist, cands, "選択を記録しました", args.kind, args.brief)
        return

    if args.kind == "debut":
        cands = prompts_debut(artist)
    elif args.kind == "cover":
        if not args.brief:
            ap.error("--kind cover には --brief が必要です")
        cands = prompts_cover(artist, json.loads(args.brief.read_text(encoding="utf-8")), args.brief)
    else:
        cands = prompts_reshoot(artist, args.pivot_reason)
    print(f"  指示文を {len(cands)} 本作りました（実名・商標は含めていません）")

    generated, errors = 0, []
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    if not has_key:
        print("  OPENAI_API_KEY が見つかりません（music-label/.env か環境変数）。画像は生成せず、指示文だけ書き出します（ドライラン）")
    else:
        print(f"  画像を生成しています（{len(cands)} 枚。1 枚 10〜30 秒）…")
    for i, c in enumerate(cands, 1):
        f = outdir / f"{c['kind']}_{c['no']:02d}.png"
        try:
            if generate_image(c["prompt"], f):
                c["file"] = f.name; generated += 1
                print(f"    [{i}/{len(cands)}] {c['kind']} {c['no']:02d} 完了")
        except urllib.error.HTTPError as e:   # API からのエラー応答（鍵・残高・内容の拒否など）
            body = e.read().decode("utf-8", "replace")[:300]
            msg = f"HTTP {e.code}: {body}"
            c["error"] = msg; errors.append(msg)
            print(f"    [{i}/{len(cands)}] {c['kind']} {c['no']:02d} 失敗 → {msg}")
        except Exception as e:   # noqa: BLE001
            c["error"] = str(e); errors.append(str(e))
            print(f"    [{i}/{len(cands)}] {c['kind']} {c['no']:02d} 失敗 → {e}")
        if errors and len(errors) >= 3 and generated == 0:
            print("  3 枚続けて失敗したので中断します（同じ原因の可能性が高い）")
            break
    if has_key:
        print(f"  画像を {generated} 枚生成しました" + (f"（失敗 {len(errors)} 枚）" if errors else ""))
    if errors:
        first = errors[0]
        hint = (SSL_HINT if "CERTIFICATE_VERIFY_FAILED" in first or "SSL" in first else
                "鍵が無効です。.env の OPENAI_API_KEY を確認（前後の空白・改行、sk- で始まるか。`python3 scripts/set_key.py --check`）" if "401" in first else
                "OpenAI 側の残高不足か回数制限です。platform.openai.com の Billing を確認" if "429" in first else
                "画像モデルの利用が許可されていません。OpenAI の組織設定で画像モデルの利用（本人確認）を確認" if "403" in first or "verif" in first.lower() else
                "指示文が内容ポリシーで拒否されました。該当候補だけ除外して続行できます" if "safety" in first.lower() or "content_policy" in first.lower() else
                "ネットワークか API の一時的な問題の可能性。少し待って再実行")
        print(f"  → 対処のヒント: {hint}")

    if generated and args.kind == "cover":   # 候補ごとのデザインで、アーティスト名と曲名を入れた見本を作る（採点も選ぶのも文字入りで。決定 122）
        try:
            n_tx = text_previews(cands, outdir, artist, args.brief)
            if n_tx:
                print(f"  {n_tx} 枚に、アーティスト名と曲名を候補ごとのデザインで入れた見本を作りました")
        except Exception as e:  # noqa: BLE001
            print(f"  － 文字入りの見本を作れませんでした（{e}）。仕上げで文字を入れます")
    n_scored = claude_score(cands, outdir, artist, criteria,
                            json.loads(args.brief.read_text(encoding="utf-8")) if args.brief else None) if generated else 0
    if n_scored:
        print(f"  Claude が {n_scored} 枚を採点しました")
    for c in cands:
        score_candidate(c, criteria)
    cand_path.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")

    for kind in sorted({c["kind"] for c in cands}):
        sub = [c for c in cands if c["kind"] == kind]
        chosen, note = decide(sub, criteria, ask_forced=(args.kind == "reshoot"))
        print(f"  {kind}: {note}")
        if chosen:
            (outdir / f"selection_{kind}.json").write_text(json.dumps({"kind": kind, "chosen": chosen["no"], "by": "auto",
                "total": chosen["total"], "date": date.today().isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")
    note = "候補を確認して選んでください" if not criteria["locked"] else "自動選択済み（確信度が低いものだけ確認）"
    write_review(outdir / "review.md", artist, cands, note)
    write_review_html(outdir / "review.html", artist, cands, note, args.kind, args.brief)
    print(f"  → ブラウザで見比べる：open {(outdir / 'review.html').relative_to(ROOT)}（全組の一覧は out/visuals/index.html）")
    print(f"  → {outdir.relative_to(ROOT)}/review.md を書き出しました")
    print("=== 完了 ===")


if __name__ == "__main__":
    main()

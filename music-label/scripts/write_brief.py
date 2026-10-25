#!/usr/bin/env python3
"""
ブリーフ生成の本体：枠の割り当て（骨組み）に、Suno 用の指示文・歌詞・タイトル候補を Claude が書き足す

入力：select_references.py が出した骨組み JSON（out/briefs/<週>_<組>.json）
出力：同じファイルを上書き（suno_style_prompt / suno_lyrics / title_candidates / vocal_blend / phrase_transform 候補 /
      cover_prompt_seed / notes_ja が埋まる）。あわせて <同名>.prompt.md に Claude へ渡した内容を保存する

使い方：
  python3 scripts/write_brief.py out/briefs/2026-10-05_light.json
  python3 scripts/write_brief.py out/briefs/2026-10-05_*.json --references out/references/   # 解析シートがあれば枠ごとに渡す
  python3 scripts/write_brief.py out/briefs/2026-10-05_light.json --dry-run                   # API を呼ばず指示文だけ書き出す

ANTHROPIC_API_KEY が無いときは自動でドライランになる（.prompt.md を claude.ai に貼れば手動でも進められる）。
必要なもの：pip install anthropic
"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = "claude-opus-5-5"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _life import life_context, real_names  # noqa: E402  管理表の人生（実名は伏せた要約）


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")

SLOT_SECTION = {   # 枠 → 解析シートのどの欄だけを渡すか（1 曲 1 枠：他の欄は渡さない）
    "lyrics": ["lyrics"], "worldview": ["worldview"], "instruments": ["instruments"], "performance": ["performance"],
    "structure": ["structure"], "harmony": ["harmony"], "groove": ["groove"], "phrase": ["phrases"],
    "vocal_main": ["vocal"], "vocal_sub1": ["vocal"], "vocal_sub2": ["vocal"],
}

# Claude に返してもらう JSON の形（構造化出力）
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title_candidates", "suno_style_prompt", "suno_lyrics", "vocal_instruction",
                 "phrase_transform_options", "cover_prompt_seed", "notes_ja", "self_check", "core", "suno_lyric_prompt", "episode"],
    "properties": {
        "title_candidates": {"type": "array", "minItems": 3, "maxItems": 5, "items": {"type": "string"}},
        "suno_style_prompt": {"type": "string", "description": "Suno の Style 欄。英語。ジャンル・テンポ・楽器・声の質感・場面。実在アーティスト名を含めない。900 文字以内"},
        "suno_lyrics": {"type": "string", "description": "Suno の Lyrics 欄。[Intro] [Verse 1] [Chorus] [Bridge] [Outro] などのセクションタグと、[Humming] [Whisper] [Shout] [la la la] などの歌い方タグを含む"},
        "vocal_instruction": {"type": "string", "description": "表現層 6:2:2 を合成した歌唱指示（英語 1〜3 文）。音名ではなく描写で"},
        "phrase_transform_options": {
            "type": "array", "minItems": 3, "maxItems": 3,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["kept", "changed", "contour_interval_changed", "description"],
                      "properties": {
                          "kept": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": ["rhythm", "contour", "instrument", "position"]}},
                          "changed": {"type": "array", "items": {"type": "string"}},
                          "contour_interval_changed": {"type": "boolean"},
                          "description": {"type": "string"}}}},
        "cover_prompt_seed": {"type": "string", "description": "ジャケット生成の種（英語 3〜5 行）。色調・光・構図・質感・モチーフ。人の顔・文字・ロゴなし"},
        "episode": {
            "type": "object", "additionalProperties": False,
            "description": "この曲のエピソード（決定 33）。アーティストがどんな場面で、何を見て・聞いて・考えて・感じて、何を思ったか。歌詞はここから書く。日本語（story_en だけ英語）。実在の名前なし",
            "required": ["theme", "when_where", "saw", "heard", "thought", "felt", "realized", "story_ja", "story_en", "source"],
            "properties": {
                "theme": {"type": "string", "description": "何のテーマの曲か（1 行）"},
                "when_where": {"type": "string", "description": "いつ・どこで（季節・時刻・場所。具体的に）"},
                "saw": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "string"}, "description": "見たもの（具体的な物・光・人の動き）"},
                "heard": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "string"}, "description": "聞いたもの（音・声・言葉）"},
                "thought": {"type": "string", "description": "考えたこと（頭の中の言葉）"},
                "felt": {"type": "string", "description": "感じたこと（体と気持ち）"},
                "realized": {"type": "string", "description": "そして何を思ったか（たどり着いた思い。サビの芯になる）"},
                "story_ja": {"type": "string", "description": "エピソードの文章（日本語 200〜400 字。アーティスト本人の目線で、控えめに具体的に）"},
                "story_en": {"type": "string", "description": "同じエピソードの英語の短い紹介（2〜3 文。SNS・曲紹介に使う。三人称）"},
                "source": {"type": "string", "description": "どの近況・歌の種・ベースの曲から生まれたか（日本語 1 行）"}
            }
        },
        "notes_ja": {"type": "string", "description": "オーナー向けの日本語メモ：この曲で何を狙ったか、どの枠から何を借りたか、迷った点"},
        "core": {
            "type": "object", "additionalProperties": False,
            "description": "この曲の印象を決める『核』。core_fixed モードでは固定され、Suno は変更できない。full / instrumental では空でよい",
            "required": ["chorus_lines", "hook_line", "title_placement", "fixed_tags", "syllables_per_line_target"],
            "properties": {
                "chorus_lines": {"type": "array", "items": {"type": "string"}, "description": "サビ全文（4〜6 行）"},
                "hook_line": {"type": "string", "description": "決め台詞（タイトル語を含むか連想させる 1 行）"},
                "bridge_lines": {"type": "array", "items": {"type": "string"}, "description": "ブリッジを固定する場合のみ"},
                "title_placement": {"type": "string", "description": "タイトル語をどこで言うか（サビ末 など）"},
                "fixed_tags": {"type": "array", "items": {"type": "string"}, "description": "位置込みの歌い方タグ（例：'[Outro] [Humming]'）"},
                "trend_language_line": {"type": "string", "description": "トレンド言語の 1 行（使う場合）。無ければ空文字"},
                "syllables_per_line_target": {"type": "integer", "description": "節（Verse）の 1 行あたり音節数の目標。Suno に書かせる節をサビと揃えるため"}
            }
        },
        "suno_lyric_prompt": {"type": "string", "description": "Suno の歌詞生成（Write Lyrics）に貼る英文指示。節（Verse 1 / Verse 2 / Pre-Chorus）だけを書かせる。テーマ・視点・風景語・1 行の音節数・行数・禁止事項を含み、サビは含めない（核はこちらで固定）。実名なし。full / instrumental では空文字"},
        "self_check": {"type": "object", "additionalProperties": False,
                       "required": ["no_real_artist_names", "hook_within_limit", "tics_included", "forbidden_avoided"],
                       "properties": {k: {"type": "boolean"} for k in ["no_real_artist_names", "hook_within_limit", "tics_included", "forbidden_avoided"]}},
    },
}

SYSTEM = """You write production briefs for an AI-generated music label. Each brief turns a "skeleton" (which reference
songs feed which slot) plus a fixed artist profile into: a Suno style prompt, full lyrics with section tags, title
candidates, a vocal instruction, three phrase-transformation options, and a cover-art seed.

Hard rules (never break):
1. One source per slot. Borrow ONLY the named dimension from each reference (e.g. from the "lyrics" reference take
   theme/arc/rhyme/syllable structure - never its words; from "phrase" take rhythm skeleton or contour - never pitches).
2. Never copy lyrics lines, melodies, or distinctive phrases from any reference. Never name a real artist, band, song,
   or brand anywhere in the output (Suno rejects them and it is a likeness/IP risk). Describe by attributes instead.
3. The artist's fixed constraints (composition habits, voice spec, signature techniques, tics, landscape words,
   forbidden list, BPM range, drive/scene spec) override anything a reference suggests. Write for the act's listening
   scene (persona.drive_scene / persona.scene and sound.scene_spec.notes): a live-house act needs a chorus a crowd can
   sing and a band arrangement; a party act needs a steady danceable groove and DJ-friendly intro/outro; a personal-
   listening act can use intimate detail, quiet dynamics and space; a driving act stays clear over road noise.
   If the brief has a "scene" object, it is THIS song's listening scene and it wins over the act's usual scene and
   over drive_spec for length, hook timing and breaks. The act's identity never changes with the scene: the same voice,
   timbre, palette, tics, landscape words and themes - only the arrangement, energy and form adapt to the place.
   Forbidden items that exist only for driving (sudden silence, big volume jumps) are relaxed when
   scene.no_sudden_silence is false; every other forbidden item still applies.
   If the brief has "scene_base", it is THIS act's best-growing song in THIS scene (power = streams per day). Write a
   sibling that thickens what made it grow: keep its strongest elements (tempo feel, groove, arrangement density, hook
   placement and length, vocal treatment, energy curve; see its style and what it borrowed) and push one or two of them
   further. Change the theme, title, melody and every lyric line - never reuse its lyric lines or melody, and do not
   make a copy; listeners should hear the same act at its best, not the same song.
4. Vocal: convert note names to descriptions ("the chorus peak sits just below where the voice would flip to
   falsetto, sung in full voice"). Blend the three vocal references 6:2:2 for EXPRESSION only (breath, ad-libs,
   layering, phrasing) - the timbre is fixed by the artist's Suno Persona.
5. Phrase transformation: keep at most 2 of {rhythm, contour, instrument, position}; words always change; if contour
   is kept, intervals/key must change (contour_interval_changed=true).
6. Hook/chorus element within the hook_within_sec limit; total length inside length_sec range; no sudden silence
   unless the spec has no_sudden_silence=false (then deliberate breaks/stops are allowed);
   nothing from forbidden/forbidden_topics; lyrics in the primary language; if trend_language is given and the
   artist allows it, use it only for one hook line or call-out, within the max ratio.
7. Tics with frequency every_song MUST appear in the lyrics as section/performance tags (e.g. [Humming], [Whisper],
   [Shout], [la la la]) at the stated position.
8. Suno style prompt: English, under 900 characters, comma-separated descriptors (genre, era feel, tempo/BPM,
   instruments, vocal timbre and delivery, mood, scene), no artist names, no lyrics.
9. notes_ja is written in Japanese for the owner; everything else in English unless the brief says otherwise.
10. Collaboration (brief has featured_guest): this song is released under THIS artist's name with the guest in the
   Featured Artist field - never write "feat." in any title. Suno can load only one Persona, so describe the guest's
   voice with featured_guest.guest_description (verbatim adjectives) inside suno_style_prompt and give the guest a
   clearly marked part in the lyrics (e.g. "[Verse 2 - guest vocal]" or call-and-response in the bridge) following
   featured_guest.collab_style. The guest's own name never appears in the lyrics or the style prompt.
11. Remix (brief has remix_of): this is the remixer's reconstruction of the label's own earlier song. Reuse the
   original's hook/chorus words from remix_of.core (it is our own song, so this is allowed) but rebuild everything else
   in this artist's sound (tempo, groove, instruments, structure). The first title candidate must be exactly
   remix_of.title_rule; the others may vary only after the original title. State in notes_ja that the owner can use
   Suno's cover/remix of the original take if they prefer.
12. Life moment (brief has life_moment): the act is a person (or group) living a specific life, and this song comes
   from that moment - an event from their recent life ("近況") or a song seed from their past ("歌の種"). Write the
   song as THEIR feeling at that moment, in their point of view, with concrete things from artist.life_context
   (places, objects, people's roles, their way of speaking). Keep it understated and specific, never melodramatic.
   If life_moment.owner_sketch exists, the owner adopted that sketch: keep its title idea and build the chorus from
   its lines (you may tighten wording and meter). Explain in notes_ja which moment you used and how.
13. Episode first (every song): before writing lyrics, decide the song's episode - its theme, and the concrete
   scene the act lived through: when and where, what they SAW, what they HEARD, what they THOUGHT, what they FELT,
   and what they finally came to think (realized). Ground it in artist.life_context, the life_moment if any, and the
   song's listening scene (brief.scene) - e.g. a live-house song can come from a night at a small venue, a party song
   from a party, a personal song from a room at 2 a.m. Then write the lyrics FROM the episode: verses use the seen and
   heard details (changed into the act's own words), the chorus carries "realized". Keep it quiet and specific, never
   melodramatic; no real names, brands or places that identify real people. Fill "episode" (Japanese, story_en in
   English) - the owner reads it, and it is used for song introductions and social posts.
14. Owner request (brief has owner_request): the owner asked for this one-off song by hand. Treat
   owner_request.memo_ja (Japanese, may be empty) as the direction for theme, mood or story, inside the act's fixed
   constraints. owner_request.rejected_ideas are earlier proposals for this song that the owner turned down, each
   with the owner's reason (may be empty): never reuse their titles, and do not repeat their theme, story or overall
   direction; fix exactly what each reason points at. Make the new proposal clearly different from every rejected
   idea. Say in notes_ja (Japanese) how you followed the memo and what you changed from the rejected ideas.

Lyrics modes (artist profile -> lyrics.mode):
- "full": you write every line of suno_lyrics.
- "core_fixed" (default): you decide and FIX the song's core - the chorus (full text), the hook line, where the
  title lands, the performance tags for the artist's tics, the trend-language line if any, and the section
  structure. Verses and pre-chorus are left to Suno's lyric writer: in suno_lyrics put the placeholders
  {{SUNO_VERSE_1}}, {{SUNO_PRE_CHORUS}}, {{SUNO_VERSE_2}} (and {{SUNO_BRIDGE}} only if the bridge is not fixed) on
  their own lines under the section tags, and write suno_lyric_prompt - the instruction the owner pastes into
  Suno's lyric writer - asking only for those sections, with the theme, point of view, landscape words, the exact
  syllables-per-line target (match the chorus), the number of lines per section, the forbidden topics, and
  "do not write a chorus; do not name any real person, brand or artist". Roughly 40-50% of the final word count
  should come from Suno. The core must be strong enough to define the song on its own.
- "topic_only" (occasional): Suno writes ALL the lyrics. You write only suno_lyric_prompt: a complete brief for
  Suno's lyric writer - theme and point of view, the scene/landscape words, the full section structure with line
  counts and the syllables-per-line target, where the title should land, which performance tags to include at
  which positions (the artist's tics), the trend-language line rule if any, and the forbidden topics; plus
  "do not name any real person, brand or artist". suno_lyrics is the empty string. core keeps only fixed_tags,
  title_placement and syllables_per_line_target (chorus_lines empty, hook_line empty) so the merge step can
  verify Suno's output and add missing tags. Use this mode to let fresh phrasing in; the result is checked,
  never copied into later cores.
- "instrumental": suno_lyrics is "[Instrumental]" plus any vowel/one-word fragments the artist uses; core and
  suno_lyric_prompt are empty.
Return only the JSON object described by the schema."""


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def load_artist(slug: str, label_slug: str) -> dict:
    if label_slug and label_slug != "drive":
        lab = load_json(ROOT / "templates" / "labels" / f"{label_slug}.json")
        return next(a for a in lab["artists"] if a["slug"] == slug)
    return load_json(ROOT / "templates" / "artists" / f"{slug}.json")


def slot_excerpts(brief: dict, refs_dir: Path | None) -> dict[str, dict]:
    """枠ごとに、参考曲の解析シートから『その枠の欄だけ』を抜き出す。歌詞の語（key_words.word）は落とす"""
    out = {}
    for slot, src in brief["slots"].items():
        # 参考曲のアーティスト実名は渡さない（曲名と枠の中身だけ。実名は模倣の方向に引っ張る）
        entry = {"reference_title": src["title"], "weight": src.get("weight", 1.0)}
        sheet = None
        if refs_dir:
            for cand in (refs_dir / f"{src['reference_id']}.json",):
                if cand.exists():
                    sheet = load_json(cand)
        if sheet:
            for sec in SLOT_SECTION.get(slot, []):
                data = json.loads(json.dumps(sheet.get(sec, {})))
                if sec == "lyrics" and isinstance(data, dict) and "key_words" in data:
                    data["key_words"] = [{"category": k.get("category")} for k in data["key_words"]]   # 語そのものは渡さない
                if sec == "lyrics" and isinstance(data, dict):   # 聞き取った歌詞の文そのものは渡さない（形だけ。決定 86）
                    data.pop("transcript", None)
                    data.pop("web_check", None)
                entry[sec] = data
        else:
            entry["note"] = "解析シートなし。タイトルと組の固定制約から推定すること（具体的な模倣は禁止）"
        out[slot] = entry
    return out


def public_safe_artist(artist: dict) -> dict:
    """Claude に渡す設定書。実在アーティスト名（favorite_artists_real / influences.name）は落とす"""
    a = json.loads(json.dumps(artist))
    a.pop("profile", None); a.pop("distribution", None); a.pop("name_check", None); a.pop("concept_history", None)
    a.pop("life", None); a.pop("source", None)
    ctx = life_context(artist)   # 管理表の人生の要約（影響・参考曲の候補＝実名は入れない）
    if ctx:
        a["life_context"] = ctx
    for inf in a.get("persona", {}).get("influences", []):
        inf.pop("name", None); inf.pop("reference_id", None)
    for k in list(a.keys()):
        if k.startswith("_"):
            a.pop(k)
    return a


DB_DIGEST = ROOT / "out" / "reference_db" / "digest.json"   # reference_db.py：SSD の参考データベースの傾向


def db_digest() -> dict:
    """オーナーの参考データベースの傾向（実名は伏せてある。real_names は Claude に渡さない）"""
    if not DB_DIGEST.exists():
        return {}
    d = load_json(DB_DIGEST)
    return {k: v for k, v in d.items() if k not in ("real_names", "updated")} if d.get("songs") else {}


def artist_db(slug: str) -> dict:
    """参考データベースのうち、この組に近い曲の傾向（reference_db.py の組ごとの要約。id の一覧は渡さない）"""
    p = DB_DIGEST.parent / "artists" / f"{slug}.json"
    if not p.exists():
        return {}
    d = load_json(p)
    return {k: v for k, v in d.items() if k not in ("close", "updated", "slug", "name")} if d.get("close_count") else {}


def build_user_message(brief: dict, artist: dict, excerpts: dict) -> str:
    db = db_digest()
    db_part = ("\n\n## Owner's reference database (overall tendencies the owner collected; real names removed)\n"
               "Use it as taste guidance (mood, tempo range, themes, owner's notes) on top of the per-slot references. "
               "Never imitate a specific song, and stay inside the artist's fixed constraints.\n"
               + json.dumps(db, ensure_ascii=False, indent=1)) if db else ""
    near = artist_db(artist.get("slug", ""))
    if near:
        db_part += ("\n\n## Songs in the owner's database closest to THIS artist (tendencies only; real names removed)\n"
                    "Weigh these above the overall tendencies: they are what the owner's taste looks like inside this artist's sound. "
                    "owner_assigned counts songs the owner explicitly tagged for this artist. "
                    "style_hints are optional extra English style words; use them only if they fit the fixed constraints.\n"
                    + json.dumps(near, ensure_ascii=False, indent=1))
    return (
        "## Artist profile (fixed constraints; names of real artists already removed)\n"
        + json.dumps(public_safe_artist(artist), ensure_ascii=False, indent=1)
        + "\n\n## Brief skeleton (slots -> references, growth hints, trend language)\n"
        + json.dumps({k: v for k, v in brief.items() if k not in ("slots",)}, ensure_ascii=False, indent=1)
        + "\n\n## Per-slot reference excerpts (only the borrowed dimension of each reference)\n"
        + json.dumps(excerpts, ensure_ascii=False, indent=1)
        + db_part
        + "\n\nWrite the brief now."
    )


def banned_terms(artist: dict) -> list[str]:
    return [t for t, _ in _real_terms(artist)]


def _real_terms(artist: dict) -> list[tuple[str, bool]]:
    """実在の名前の一覧：(名前, アーティスト名か)。管理表の影響・好きなアーティスト・参考データベースのアーティスト名と曲名"""
    terms = [(t, True) for t in artist.get("profile", {}).get("favorite_artists_real", []) or []]
    terms += [(i.get("name", ""), True) for i in artist.get("persona", {}).get("influences", [])]
    terms += [(t, True) for t in real_names(artist)]   # 管理表の『影響』『参考曲の候補』の実名
    if DB_DIGEST.exists():        # 参考データベースのアーティスト名・曲名（3 文字以上。短い語は普通の言葉と重なるため）
        d = load_json(DB_DIGEST)
        arts = set(d.get("real_artists") or [])
        terms += [(n, n in arts or not d.get("real_artists")) for n in d.get("real_names", []) if len(n) >= 3]
    seen, out = set(), []
    for t, a in terms:
        if t and t not in ("（bio のみ）",) and t.lower() not in seen:
            seen.add(t.lower())
            out.append((t, a))
    return out


_WORDS: set[str] | None = None
# 辞書が無いとき（Mac 以外）の、よく使う英語の言葉
_COMMON = set("""a about after again against all alone always am an and angel another any are around as at away baby back bad be beautiful
because bed been before begin being better between big black bleed blood blue body born boy break breathe bright broken burn but by call
can change city close cold come crazy crowd cry dance dark day dead dear deep desire do down dream drive drop dust easy echo else empty end
energy ever every eyes face fade fall far fast feel feeling fever fire first flick fly for forever free friend from game ghost girl give go
gold gone good gravity happy have he heart heaven hello help her here high hold home hope horizon hot how human i if in inside intro into
is it just keep key kid kill kiss know last late leave let lie life light like line little live lonely long lose loud love lover low lucky
mad make man me melody memory midnight mind miss money moon more morning move my name need never new next night no not nothing now ocean of
off oh old on one only open or other our out over pain paradise party people play please pop power pretty rain real red remember ride right
rise river road rock run sad same say sea see shadow shadows shed shine show sky sleep slow smile so some something song soul space stand
star stars stay still stop storm story strange summer sun sweet take talk tell than that the then there these they thing think this those
through time to together tonight too touch town true try turn two under up us voice wait wake walk want war water wave way we what when where
which while white who why wild will wind winter with without wonder world yeah yes yesterday you young your""".split())


def _dictionary() -> set[str]:
    """英語の辞書（Mac の /usr/share/dict/words。小文字の見出し語だけ＝人名・地名は入らない）"""
    global _WORDS
    if _WORDS is None:
        _WORDS = set(_COMMON)
        for p in (Path("/usr/share/dict/words"), Path("/usr/share/dict/web2")):
            try:
                _WORDS |= {w.strip() for w in p.read_text(encoding="utf-8", errors="ignore").splitlines() if w.strip().islower()}
                break
            except OSError:
                continue
    return _WORDS


def _ordinary_word(w: str) -> bool:
    w = w.lower().strip("'")
    d = _dictionary()
    if w in d:
        return True
    for suf, add in (("s", ""), ("es", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"), ("'s", ""), ("ies", "y")):
        if w.endswith(suf) and (w[: -len(suf)] + add) in d:
            return True
    return False


def ordinary(term: str) -> bool:
    """ふつうの言葉だけでできているか（英字の言葉で、どれも辞書にある。数字・記号・日本語・造語を含めば False）"""
    words = term.split()
    return bool(words) and all(re.fullmatch(r"[A-Za-z']+", w) and _ordinary_word(w) for w in words)


def real_name_hits(artist: dict, text: str) -> list[str]:
    """文の中の、実在の名前（Suno が断る・まねの心配）。オーナーの指示（決定 82）：
    ふつうの言葉だけでできた名前は、1 語でも 2 語以上でも止めない（Time・LAST ONE など）。造語・人名（辞書にふつうの言葉として無いもの）を含めば止める。
    日本語は、アーティスト名だけ止める（曲名は止めない）。
    言葉の途中には当てない（[Intro] の intro は名前として数えない）。止めるのは固有名詞（人・グループの名前）だけ、が目安"""
    hits = []
    for t, is_artist in _real_terms(artist):
        if ordinary(t):
            continue   # ふつうの言葉だけ（1 語でも 2 語以上でも）は止めない。オーナーの指示（決定 82）
        if not is_artist and not t.isascii():
            continue   # 日本語の曲名（さよなら・夜明け など）は、ふつうの言葉と見分けにくいので止めない。日本語のアーティスト名は止める
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", text, re.I):
            hits.append(t)
    return sorted(set(hits))


def _grams(text: str) -> set[str]:
    """言い回しを比べる単位：英語は 5 語続き、日本語などは 8 文字続き"""
    out = set()
    words = re.findall(r"[a-z']+", text.lower())
    out |= {" ".join(words[i:i + 5]) for i in range(len(words) - 4)}
    cjk = re.sub(r"[\sA-Za-z0-9'\W_]", "", text)
    out |= {cjk[i:i + 8] for i in range(len(cjk) - 7)}
    return out


def copied_phrases(lyrics: str, brief: dict) -> list[str]:
    """新しい歌詞に、参考曲の聞き取った歌詞と同じ言い回し（英語 5 語・日本語 8 文字以上の続き）が無いか（決定 86）"""
    refs_dir = Path(brief["references_dir"]) if brief.get("references_dir") else None
    if not refs_dir or not lyrics:
        return []
    mine = _grams(lyrics)
    hits = []
    for src in (brief.get("slots") or {}).values():
        p = refs_dir / f"{src.get('reference_id')}.json"
        try:
            t = ((load_json(p).get("lyrics") or {}).get("transcript") or "") if p.exists() else ""
        except (OSError, ValueError):
            t = ""
        if t:
            hits += sorted(mine & _grams(t))
    return list(dict.fromkeys(hits))[:5]


def validate(result: dict, artist: dict, brief: dict) -> list[str]:
    problems = []
    same = copied_phrases(result.get("suno_lyrics", ""), brief)
    if same:
        problems.append("参考曲の歌詞と同じ言い回しがある（DistroKid・配信サイトで止められる心配。書き換える）：" + " ／ ".join(same))
    text = json.dumps(result, ensure_ascii=False)
    for t in real_name_hits(artist, text):
        problems.append(f"実在アーティスト名が含まれている: {t}")
    if len(result["suno_style_prompt"]) > 900:
        problems.append(f"Suno のスタイル指示が長すぎる（{len(result['suno_style_prompt'])} 文字 > 900）")
    for opt in result["phrase_transform_options"]:
        if len(opt["kept"]) > 2:
            problems.append("フレーズ変形で残す要素が 3 つ以上")
        if "contour" in opt["kept"] and not opt["contour_interval_changed"]:
            problems.append("輪郭を残しているのに音程の幅を変えていない")
    if any(re.search(r"\b(feat|ft)\.?\s", t, re.I) for t in result["title_candidates"]):
        problems.append("曲名に feat. が入っている（フィーチャリング欄で登録する運用）")
    guest = (brief.get("featured_guest") or {}).get("name")
    if guest and guest.lower() in (result["suno_style_prompt"] + result["suno_lyrics"]).lower():
        problems.append(f"客演の名前（{guest}）が Suno への指示文か歌詞に入っている（声は形容詞で描写する）")
    rule = (brief.get("remix_of") or {}).get("title_rule")
    if rule and (not result["title_candidates"] or result["title_candidates"][0] != rule):
        problems.append(f"リミックスの 1 番目のタイトル候補が「{rule}」になっていない")
    tics = [t for t in artist.get("vocal", {}).get("signature_techniques", {}).get("tics", []) if t.get("frequency") == "every_song"]
    mode = artist.get("lyrics", {}).get("mode", "core_fixed")
    if mode == "topic_only" and len(result.get("suno_lyric_prompt", "")) < 200:
        problems.append("topic_only なのに Suno への指示文が短すぎる")
    if mode == "core_fixed" and "{{SUNO_" not in result["suno_lyrics"]:
        problems.append("core_fixed なのに Suno 用の差し込み位置（{{SUNO_VERSE_1}} など）が無い")
    if mode == "core_fixed" and not result.get("core", {}).get("chorus_lines"):
        problems.append("core_fixed なのに核（サビ）が空")
    if mode != "topic_only" and tics and "[" not in result["suno_lyrics"]:
        problems.append("歌詞にセクション／歌い方タグが無い（毎曲の癖が入っていない可能性）")
    if not all(result["self_check"].values()):
        problems.append(f"Claude の自己チェックで未達: {[k for k, v in result['self_check'].items() if not v]}")
    return problems


def call_claude(system: str, user: str) -> tuple[dict | None, str]:
    """Claude を呼んで JSON を受け取る。(結果, 状態メッセージ)。呼び方は _claude.py と同じ（決定 80）：
    受け付けない制約（数の上限など）は外して送る・長い答えでも切れないよう max_tokens を大きく・拒否時はサーバー側で別モデルに自動で切り替え"""
    try:
        import anthropic
    except ImportError:
        return None, "anthropic パッケージが無い（python3 -m pip install -r requirements.txt）"
    import _claude
    client = anthropic.Anthropic()
    try:
        msg = _claude._stream(client, max_tokens=64000,
                              system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],   # 固定部分はキャッシュ
                              messages=[{"role": "user", "content": user}],
                              output_config={"effort": "high", "format": {"type": "json_schema", "schema": _claude.api_schema(OUTPUT_SCHEMA)}})
    except anthropic.AuthenticationError:
        return None, "ANTHROPIC_API_KEY が無効（401）。setup_keys.py --only anthropic で入れ直す"
    except anthropic.PermissionDeniedError as e:
        return None, f"この鍵では使えません（403）: {e.message}"
    except anthropic.RateLimitError as e:
        return None, f"回数制限か残高不足（429）。console.anthropic.com の Billing で残高を確かめる: {e}"
    except anthropic.APIStatusError as e:
        return None, f"API エラー {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        return None, f"接続できない（ネットワーク／証明書）: {e}"
    except Exception as e:  # noqa: BLE001  部品が古いなど
        return None, f"送れませんでした（{type(e).__name__}: {str(e)[:200]}）。ターミナルで  python3 -m pip install -U anthropic  を実行してください"
    if msg.stop_reason == "refusal":
        cat = getattr(getattr(msg, "stop_details", None), "category", None)
        return None, f"安全上の理由で生成が拒否された（{cat}）。歌詞テーマや語を見直す"
    if msg.stop_reason == "max_tokens":
        return None, "答えが長すぎて途中で切れた（max_tokens 不足）"
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(text), f"OK（入力 {msg.usage.input_tokens} / 出力 {msg.usage.output_tokens} トークン、モデル {msg.model}）"
    except json.JSONDecodeError:
        return None, "返答が JSON として読めない"


def main() -> None:
    ap = argparse.ArgumentParser(description="ブリーフに Suno 用の指示文・歌詞・タイトルを書き足す")
    ap.add_argument("briefs", nargs="+", type=Path, help="骨組み JSON（複数可）")
    ap.add_argument("--references", type=Path, help="解析シート JSON のフォルダ（<reference_id>.json）")
    ap.add_argument("--dry-run", action="store_true", help="API を呼ばず、Claude に渡す内容だけ書き出す")
    ap.add_argument("--lyrics-mode", choices=["full", "core_fixed", "topic_only", "instrumental"],
                    help="歌詞モードを手動で指定（省略時は設定書の mode。topic_only_ratio により『たまに』自動で topic_only になる）")
    args = ap.parse_args()

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    dry = args.dry_run or not has_key
    print("=== ブリーフ生成を始めます ===")
    if dry:
        print("  " + ("--dry-run 指定" if args.dry_run else "ANTHROPIC_API_KEY が無い") + "ため、Claude には送らず指示文（.prompt.md）だけ書き出します")
        print("  → .prompt.md の内容を claude.ai に貼れば、手動でも同じ結果が得られます")

    def rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(ROOT))
        except ValueError:
            return str(p)

    for bp in args.briefs:
        bp = bp.resolve()
        if not bp.exists():
            print(f"\n[停止] 骨組みファイルがありません: {rel(bp)}")
            print("       先に  python3 scripts/select_references.py --all --demo --week <月曜の日付>  を実行して骨組みを作ってください")
            print("       （実際の参考曲 DB があるときは --demo の代わりに --references <解析シートのフォルダ>）")
            continue
        brief = load_json(bp)
        artist = load_artist(brief["artist_slug"], brief.get("label_slug", "drive"))
        print(f"\n--- {bp.name}（{artist['name']}）---")
        # 歌詞モードの決定：手動指定 > 『たまに』の自動選択（週と組で決まる乱数）> 設定書の既定
        import random as _random
        base_mode = artist.get("lyrics", {}).get("mode", "core_fixed")
        mode = args.lyrics_mode or base_mode
        if not args.lyrics_mode and base_mode in ("core_fixed", "full"):
            ratio = float(artist.get("lyrics", {}).get("topic_only_ratio", 0) or 0)
            rng = _random.Random(f"{brief.get('week_start')}|{artist['slug']}|topic_only")
            if ratio > 0 and rng.random() < ratio:
                mode = "topic_only"
        artist = json.loads(json.dumps(artist)); artist.setdefault("lyrics", {})["mode"] = mode
        brief["lyrics_mode"] = mode
        print(f"  歌詞モード: {mode}" + ("（『お題だけ』の回。歌詞は全部 Suno が書き、こちらは検査とタグ補完）" if mode == "topic_only" else ""))
        refs_dir = args.references or (Path(brief["references_dir"]) if brief.get("references_dir") else None)
        excerpts = slot_excerpts(brief, refs_dir)
        n_sheets = sum(1 for e in excerpts.values() if "note" not in e)
        print(f"  参考曲の解析シート: {n_sheets}/{len(excerpts)} 枠ぶんを渡します（無い枠はタイトルだけ）")
        user = build_user_message(brief, artist, excerpts)
        prompt_path = bp.with_suffix(".prompt.md")
        prompt_path.write_text("# System\n\n" + SYSTEM + "\n\n# User\n\n" + user + "\n\n# 期待する返答の形（JSON Schema）\n\n```json\n"
                               + json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=1) + "\n```\n", encoding="utf-8")
        print(f"  Claude に渡す内容を保存: {rel(prompt_path)}（約 {len(SYSTEM) + len(user):,} 文字）")
        if dry:
            continue

        print(f"  Claude（{MODEL}）に送っています…")
        result, status = call_claude(SYSTEM, user)
        print(f"  応答：{status}")
        if not result:
            continue
        problems = validate(result, artist, brief)
        if problems:
            print("  [要確認] " + " / ".join(problems))
        brief["title_candidates"] = result["title_candidates"]
        brief["suno_style_prompt"] = result["suno_style_prompt"]
        brief["suno_lyrics"] = result["suno_lyrics"]
        brief["vocal_blend"]["synthesized_instruction"] = result["vocal_instruction"]
        brief["phrase_transform"]["options"] = result["phrase_transform_options"]
        brief["phrase_transform"]["description"] = "options から 1 つ選び、kept / changed / contour_interval_changed を上に写す"
        brief["cover_prompt_seed"] = result["cover_prompt_seed"]
        brief["notes_ja"] = result["notes_ja"]
        brief["episode"] = result.get("episode") or {}   # この曲のエピソード（決定 33）
        brief["lyrics_mode"] = artist.get("lyrics", {}).get("mode", "core_fixed")
        brief["core"] = result.get("core", {})
        brief["suno_lyric_prompt"] = result.get("suno_lyric_prompt", "")
        if brief["lyrics_mode"] == "topic_only":
            print("  歌詞モード topic_only：suno_lyric_prompt を Suno の Write Lyrics に貼り、出てきた歌詞全文を保存して")
            print("    python3 scripts/merge_lyrics.py <このブリーフ> --verses <保存したテキスト>  で検査・タグ補完してください")
        elif brief["lyrics_mode"] == "core_fixed" and "{{SUNO_" in brief["suno_lyrics"]:
            print("  歌詞モード core_fixed：核（サビ・決め台詞・タグ）は固定済み。節は Suno の Write Lyrics に suno_lyric_prompt を貼って作り、")
            print("    python3 scripts/merge_lyrics.py <このブリーフ> --verses <Suno の出力を保存したテキスト>  で合体してください")
        brief["validation"] = {"problems": problems, "checked_at": date.today().isoformat()}
        brief["generated_by"] = f"select_references.py + write_brief.py ({MODEL})"
        bp.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  タイトル候補: {' / '.join(result['title_candidates'][:3])}")
        print(f"  → {rel(bp)} を更新しました")

    print("\n=== 完了 ===" + ("" if dry else "。次：Suno に suno_style_prompt と suno_lyrics を貼って 4〜6 テイク生成"))


if __name__ == "__main__":
    main()

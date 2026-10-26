#!/usr/bin/env python3
"""
配信サイトに載せる歌詞を、Suno に入れた歌詞から作り直す（決定 52）

Suno の歌詞には、曲の作りの札（[Verse]・[Chorus]）や、音の指示（[Bright synth arpeggio …]・[Phone call voice]）が入っている。
配信サイト（DistroKid の歌詞の画面 → Apple Music。Lyric Blaster を付ければ Spotify・Instagram なども）では、
これらがあると弾かれる。DistroKid・Apple Music の決まり（2026-10 に調べたもの）に合わせて、次のように作り直す：
  ・[ ] の札と指示は消す。札のところで段落を分ける（段落の間だけ 1 行あける。段落の中ではあけない）
  ・( ) の中が「歌っている言葉」（コーラス・掛け合い）なら残す。(whispered)・(x2)・(spoken) などの指示は消す
  ・*雑音* や { } の中も消す（音の指示）
  ・「x2」「(Repeat x3)」のような繰り返しの略は、その行を書き出す。[Chorus x2] は段落を書き出す
  ・行の終わりの . , ; : 、 。 … ― は消す（! ? と引用の記号は残す）
  ・行の頭を大文字にする（英語などのアルファベット）
  ・1〜10 の数字は英語の言葉で書く（one … ten。時刻「2:40」や「401」号線などはそのまま）
  ・標準の綴りにする（tryna → trying to・gonna → going to・runnin' → running など。決定 93）。歌い手の名前・SNS の住所・URL の行は消す
  ・札なしの「Chorus 2x」「Verse 1:」も消し、中身の書いていないサビ（[Chorus] だけ）は前のサビを書き出す。1 行に 2 文あれば分ける（決定 93）
  ・前後の空白・続いた空白を整える
直したあとも気をつけること（自動では直さない。確かめる一覧に出す）：
  ・1 行が長い（13 語を超える）／伏せ字（f**k など。音で伏せていなければ伏せずに書く）／ya・ain't などのくだけた言い方
  ・Suno が歌詞と違う言葉で歌っていることがある。配信サイトは「聞こえるとおり」を求めるので、1 回聞いて違うところを直す

使い方：
  python3 scripts/store_lyrics.py PDR-003          # 作り直した歌詞と、確かめる一覧を出す
  python3 scripts/store_lyrics.py PDR-003 --diff   # Suno の歌詞と並べて、どこを直したか
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lyric_rules import cap as _cap, end_punct, numbers as _numbers, split_sentences, standard_spelling, unshout as _unshout  # noqa: E402

# ( ) の中がこれだけなら、歌っている言葉ではなく指示
DIRECTION = re.compile(r"^\s*(x\s*\d+|×\s*\d+|\d+\s*x|repeat.*|rpt.*|whisper(ed|ing)?|spoken|speaking|shout(ed|ing)?|sung|singing|"
                       r"laugh(s|ing)?|sigh(s|ing)?|breath(es|ing)?|inhale|exhale|instrumental|inst\.?|interlude|fade( out| in)?|"
                       r"echo(es|ing)?|reverb|harmon(y|ies)|ad[- ]?libs?|backing( vocals?)?|bgv|background( vocals?)?|"
                       r"male|female|both|all|choir|crowd|guitar( solo)?|solo|beat drop|drop|silence|pause|"
                       r"intro|outro|verse.*|chorus.*|pre-?chorus|post-?chorus|bridge|hook|refrain)(\s+(break|solo|part|section|only|fades?( out| in)?|ends?|continues?))?\s*$", re.I)
REPEAT_TAIL = re.compile(r"\s*[\(\[]?\s*(?:repeat\s*)?(?:[x×]\s*(\d)|(\d)\s*[x×])\s*[\)\]]?\s*$|\s*[\(\[]\s*repeat\s*[\)\]]\s*$", re.I)
TAG_REPEAT = re.compile(r"[x×]\s*(\d)|(\d)\s*[x×]\b|repeat\s*(\d)|(\d)\s*times", re.I)
SECTION = re.compile(r"(pre-?chorus|post-?chorus|final chorus|chorus|verse|bridge|hook|refrain|intro|outro|interlude|breakdown|drop)", re.I)
# 札（[ ]）なしで書かれた構成の行：「Chorus」「Chorus 2x」「(Chorus)」「Verse 1:」「Repeat chorus」など（歌詞ではない余分な文字）
SECTION_LINE = re.compile(r"^[\(\[]?\s*(?:repeat\s+)?(?:the\s+)?(pre-?chorus|post-?chorus|final chorus|chorus|verse(?:\s*\d+)?|bridge|hook|refrain|intro|outro|interlude)"
                          r"(?:\s*(?:[x×]\s*\d|\d\s*[x×]|\d\s*times|repeat))?\s*[\)\]]?\s*[:：]?\s*$", re.I)
REPEATABLE = {"chorus", "final chorus", "hook", "refrain", "pre-chorus", "post-chorus"}
SLANG_NOTE = re.compile(r"\b(ya|y'all|ain't)\b", re.I)
JUNK_LINE = re.compile(r"https?://|www\.|@\w|#\w|^\s*(vocals?|vocalist|singer|lyrics|written|produced|feat\.?)\s*[:：]", re.I)


def _clean_parens(line: str) -> str:
    def keep(m):
        inner = m.group(1).strip()
        return "" if not inner or DIRECTION.match(inner) or not re.search(r"\w", inner) else f"({inner})"
    return re.sub(r"\(([^()]*)\)", keep, line)


def _section_of(tags: list[str]) -> tuple[str | None, int]:
    """札から（段落の種類, 繰り返す回数）"""
    name, k = None, 1
    for t in tags:
        m = SECTION.search(t)
        if m and name is None:
            name = m.group(1).lower().replace("prechorus", "pre-chorus").replace("postchorus", "post-chorus")
        r = TAG_REPEAT.search(t)
        if r:
            k = int(next(g for g in r.groups() if g))
    return name, k


def to_store(text: str, latin: bool = True, names: set[str] | None = None) -> tuple[str, list[str]]:
    """Suno の歌詞 → 配信サイトに載せる歌詞（決定 52・93）。(歌詞, 直したことの一覧)。names：歌い手・メンバーの名前（行の頭の「名前:」を消す）"""
    names = {n for n in (names or set()) if n}
    who = re.compile(r"^(" + "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True)) + r")\s*[:：]\s*", re.I) if names else None
    changes: list[str] = []
    sections: list[dict] = [{"name": None, "times": 1, "lines": []}]
    n_tags = 0

    def new_section(name, times):
        if sections[-1]["lines"] or sections[-1]["name"]:
            sections.append({"name": name, "times": times, "lines": []})
        else:
            sections[-1].update(name=name, times=times)

    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.strip()
        tags = re.findall(r"\[([^\]]*)\]", line)
        rest = re.sub(r"\[[^\]]*\]", "", line).strip()
        if tags and not rest:   # 札だけの行：ここで段落を分ける（[Chorus x2] は段落を 2 回）
            n_tags += len(tags)
            new_section(*_section_of(tags))
            continue
        if rest and SECTION_LINE.match(rest):   # 札なしの「Chorus 2x」「Verse 1:」：余分な文字なので消し、段落を分ける
            changes.append(f"構成の文字を消した：{rest[:30]}")
            new_section(*_section_of([rest]))
            continue
        if tags:
            changes.append(f"行の中の指示を消した：[{'] ['.join(tags)}]")
        if not rest:   # 空の行：段落の区切り
            if sections[-1]["lines"]:
                new_section(None, 1)
            continue
        if JUNK_LINE.search(rest):
            changes.append(f"歌詞ではない行を消した：{rest[:40]}")
            continue
        if who and who.match(rest):   # 「名前: 歌詞」の名前（DistroKid の決まり 1）
            changes.append(f"歌い手の名前を消した：{rest[:30]}")
            rest = who.sub("", rest)
        rest = re.sub(r"\*[^*]+\*|\{[^}]*\}", "", rest)
        n = 1
        m = REPEAT_TAIL.search(rest)
        if m:
            n = int(m.group(1) or m.group(2) or 2)
            rest = rest[:m.start()]
            changes.append(f"繰り返しの略を書き出した（{n} 回）：{rest[:40]}")
        before = rest
        rest = _clean_parens(rest)
        if rest != before:
            changes.append(f"( ) の指示を消した：{before[:50]}")
        rest, fixed = standard_spelling(re.sub(r"\s{2,}", " ", rest).strip())
        if fixed:
            changes.append(f"標準の綴りにした（{'・'.join(dict.fromkeys(fixed))}）：{rest[:40]}")
        pieces = split_sentences(rest)
        if len(pieces) > 1:
            changes.append(f"1 行 1 文に分けた：{rest[:40]}")
        for piece in pieces:
            piece = end_punct(piece).strip()
            if re.match(r"^[\s(\"'¿¡]*[A-Za-z]", piece):
                shout = _unshout(piece, names)
                if shout != piece:
                    changes.append(f"全部大文字の行をふつうの書き方にした：{piece[:40]}")
                piece = _cap(shout)
            piece = _numbers(piece)
            if piece and re.search(r"\w", piece):
                sections[-1]["lines"].extend([piece] * n)
    # 札だけで中身の無いサビ（[Chorus] だけ・Chorus 2x だけ）は、前の同じ段落を書き出す。[Chorus x2] は段落を書き出す
    out: list[list[str]] = []
    seen: dict[str, list[str]] = {}
    for sec in sections:
        lines, name = sec["lines"], sec["name"]
        if not lines and name in REPEATABLE and seen.get(name):
            lines = seen[name]
            changes.append(f"書いていなかった繰り返しの段落（{name}）を書き出した")
        if lines and name:
            seen.setdefault(name, lines)
        if lines:
            if sec["times"] > 1:
                changes.append(f"段落の繰り返しを書き出した（{sec['times']} 回）：{lines[0][:40]}")
            out.extend([lines] * sec["times"])
    if n_tags:
        changes.insert(0, f"曲の作りの札・音の指示（[Verse]・[Chorus]・[…の音] など）を {n_tags} 個消し、そこで段落を分けた")
    return "\n\n".join("\n".join(s) for s in out).strip(), list(dict.fromkeys(changes))


def check(store: str) -> list[str]:
    """作り直したあとも、目で確かめたいところ"""
    notes = []
    for ln in store.splitlines():
        if len(ln.split()) > 13:
            notes.append(f"1 行が長い（{len(ln.split())} 語。歌の切れ目で 2 行に分けると通りやすい）：{ln[:50]}")
        if re.search(r"\w\*+\w|\w#+\w", ln):
            notes.append(f"伏せ字がある（音で伏せていなければ、伏せずに書く）：{ln[:50]}")
        if SLANG_NOTE.search(ln):
            notes.append(f"くだけた言い方（標準の綴りにできるなら直す）：{ln[:50]}")
        if re.search(r"[\[\]{}*]", ln):
            notes.append(f"記号が残っている：{ln[:50]}")
    if not store.strip():
        notes.append("歌詞が空です（インストの曲なら、歌詞は出しません）")
    return list(dict.fromkeys(notes))


def is_latin(song: dict) -> bool:
    lang = str(song.get("language") or "").lower()
    return not lang or not any(k in lang for k in ("japanese", "日本", "ja", "korean", "韓国", "chinese", "中国"))


def singer_names(song: dict) -> set[str]:
    """歌い手・メンバーの名前の候補（アーティスト名と、設定書の人物の欄にあるローマ字の名前）"""
    import json
    names = {song.get("artist", "")}
    try:
        from _common import load_artist
        a = load_artist(song["slug"], missing_ok=True) or {}
        txt = json.dumps(a.get("persona") or {}, ensure_ascii=False)
        names |= set(re.findall(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+){0,2}\b", txt))
        names |= {w for n in list(names) for w in n.split()}
    except Exception:  # noqa: BLE001
        pass
    return {n for n in names if len(n) >= 3}


def for_song(song: dict) -> dict:
    """{store: 載せる歌詞, changes: 直したこと, notes: 確かめること}。インスト・歌詞なしは store が空"""
    if song.get("instrumental") or not str(song.get("lyrics") or "").strip():
        return {"store": "", "changes": [], "notes": []}
    store, changes = to_store(song["lyrics"], is_latin(song), singer_names(song))
    return {"store": store, "changes": changes,
            "notes": check(store) + ["Suno が歌詞と違う言葉で歌っているところがあれば直す（配信サイトは聞こえるとおりの歌詞を求めます）"]}


def store_file(song: dict) -> Path:
    import publish
    return publish.PUB / song["key"] / "歌詞_配信用.txt"


def write_for_song(song: dict) -> dict:
    """公開セットに 歌詞_配信用.txt と 歌詞_確かめること.txt を書く。Gon さんが 歌詞_配信用.txt を直していたら、上書きしない
    （聞こえるとおりに直したものを守るため）。{store, path, edited, notes}"""
    import hashlib
    r = for_song(song)
    p = store_file(song)
    mark = p.with_suffix(".auto")   # 自動で作った版の目印（中身の指紋）
    if not r["store"]:
        return {**r, "path": None, "edited": False}
    p.parent.mkdir(parents=True, exist_ok=True)
    h = lambda t: hashlib.sha1(t.encode("utf-8")).hexdigest()  # noqa: E731
    edited = p.exists() and mark.exists() and h(p.read_text(encoding="utf-8")) != mark.read_text().strip()
    if not edited:
        p.write_text(r["store"] + "\n", encoding="utf-8")
        mark.write_text(h(r["store"] + "\n"))
    notes = (["（Gon さんが直した歌詞をそのまま使います）"] if edited else []) + r["notes"]
    p.with_name("歌詞_確かめること.txt").write_text(
        "配信サイトに出す歌詞（歌詞_配信用.txt）について\n\n直したこと：\n" + ("\n".join("・" + c for c in r["changes"]) or "なし")
        + "\n\n確かめること：\n" + "\n".join("・" + n for n in notes)
        + "\n\n歌詞_配信用.txt を直したら、その内容がそのまま DistroKid に入ります（作り直しで上書きされません）。\n", encoding="utf-8")
    return {**r, "store": p.read_text(encoding="utf-8").strip(), "path": p, "edited": edited, "notes": notes}


def main() -> None:
    import _songs
    ap = argparse.ArgumentParser(description="配信サイトに載せる歌詞を作り直す")
    ap.add_argument("id")
    ap.add_argument("--diff", action="store_true")
    a = ap.parse_args()
    song = _songs.by_id(a.id)
    if song is None:
        sys.exit(f"[エラー] 曲 {a.id} が見つかりません")
    r = for_song(song)
    if a.diff:
        import difflib
        print("\n".join(difflib.unified_diff(song["lyrics"].splitlines(), r["store"].splitlines(), "Suno", "配信サイト", lineterm="")))
    else:
        print(r["store"])
    print("\n— 直したこと —\n" + ("\n".join("・" + c for c in r["changes"]) or "なし"))
    print("— 確かめること —\n" + "\n".join("・" + n for n in r["notes"]))


if __name__ == "__main__":
    main()

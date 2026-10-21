#!/usr/bin/env python3
"""
Suno 設計図：作曲のイメージ（アーティストの設定書・ブリーフ／作業セット）を、Suno の作曲画面で入れられる全部の軸に写す

Suno の作曲画面（「高度な」）で入れられる軸と、イメージのどこから決めるか：
   1 歌詞              … 曲の歌詞。セクション（[Verse] など）と、毎曲の癖（語り・ハミングなど）のタグが入っているかを確かめる
   2 スタイル          … 前に書いた言葉ほど強く効くので、ジャンル → テンポ（BPM）→ 声 → 楽器・音色 → 質感・場面 の順。
                         設定書の英語の特徴語（dna_tags）で入っていないものを足す（1000 文字まで）
   3 除外するスタイル  … 曲ごとの指定＋設定書の禁止事項（templates/suno_options.json の exclude）。スタイルと食い違う言葉は外す
   4 ボーカルの性別    … 組ごとの設定（suno_options.json）
   5 Weirdness         … 同上（意外さ）
   6 Style Influence   … 同上（スタイルの効き具合）
   7 インスト          … 歌の無い曲だけオン
   8 モデル            … Suno のバージョン（全組共通：suno_options.json の default）
   9 ペルソナ          … 声を毎回同じにする（Suno で作ったペルソナの名前。作るまでは空）
  10 曲のタイトル

使い方：
  python3 scripts/suno_design.py              # 作曲待ちの全曲の設計図と、イメージの写し具合（再現度）
  python3 scripts/suno_design.py PDR-001      # この曲だけ
  python3 scripts/suno_design.py --json PDR-001
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_artist  # noqa: E402
import _songs  # noqa: E402

STYLE_MAX = 1000     # Suno のスタイル欄の上限（文字）
EXCLUDE_MAX = 1000
GENDER_WORD = {"male": r"\bmale\b|baritone|tenor|\bman\b", "female": r"\bfemale\b|\bwoman\b|soprano|alto"}


def _split(text: str) -> list[str]:
    return [t.strip() for t in re.split(r",\s*", str(text or "")) if t.strip()]


def _has(text: str, word: str) -> bool:
    return word.strip().lower() in text.lower()


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for t in items:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _tic_tags(artist: dict) -> list[tuple[str, str, str]]:
    """毎曲の癖のうち、歌詞に [タグ] として入れるもの：(タグ, 日本語の説明, 入れる位置)"""
    out = []
    for t in ((artist.get("vocal") or {}).get("signature_techniques") or {}).get("tics", []):
        if t.get("frequency") != "every_song":
            continue
        m = re.search(r"\[([^\]]+)\]", t.get("technique", ""))
        if m:
            out.append((m.group(1), t["technique"].split("（")[0][:40], t.get("where", "")))
    return out


GENERIC = {"vocal", "vocals", "voice", "last", "word", "words", "line", "lines", "bright", "chant", "into", "with", "the",
           "and", "from", "part", "only", "slow", "soft", "whole", "song", "verse", "chorus", "bridge", "intro", "outro"}


def _keys(tag: str) -> list[str]:
    """タグの意味を決める言葉（4 文字以上・ありふれた言葉を除く）を、語尾を落として"""
    words = [w for w in re.findall(r"[a-z]+", tag.lower()) if len(w) >= 4 and w not in GENERIC]
    return [re.sub(r"(ing|ed|es|s)$", "", w)[:6] for w in words]


def _tag_present(lyrics: str, tag: str) -> bool:
    """同じ意味のタグがもう歌詞にあるか（[Spoken] と [Spoken Word] のように言い方が違っても可）"""
    inside = " ".join(re.findall(r"\[([^\]]+)\]", lyrics)).lower()
    if tag.lower() in inside:
        return True
    keys = _keys(tag)
    return bool(keys) and sum(k in inside for k in keys) >= max(1, -(-len(keys) // 2))   # 意味の言葉の半分以上


HEADER = re.compile(r"^\s*\[(intro|verse|pre-chorus|chorus|final chorus|post-chorus|bridge|outro|hook|groove|drop|break)[^\]]*\]\s*$", re.I)
CHORUS = re.compile(r"^\s*\[(final )?chorus[^\]]*\]\s*$", re.I)


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """セクションの見出しの行番号と、そのセクションの終わり（次の見出しの手前）"""
    heads = [i for i, ln in enumerate(lines) if HEADER.match(ln)]
    return [(h, (heads[k + 1] if k + 1 < len(heads) else len(lines))) for k, h in enumerate(heads)]


def _last_sung(lines: list[str], start: int, end: int) -> int | None:
    for i in range(end - 1, start, -1):
        if lines[i].strip() and not lines[i].strip().startswith("["):
            return i
    return None


def insert_tag(lyrics: str, tag: str, where: str) -> tuple[str, str | None]:
    """毎曲の癖のタグを、設定書の「位置」に合わせて差し込む。(新しい歌詞, 入れた位置の説明 or None)"""
    lines = lyrics.splitlines()
    blocks = _blocks(lines)
    ins: list[int] = []   # この行の手前に [tag] を入れる
    how = None
    choruses = [(h, e) for h, e in blocks if CHORUS.match(lines[h])]
    if "アウトロ" in where:
        ins = [h + 1 for h, e in blocks if lines[h].strip().lower().startswith("[outro")][:1]
        how = "アウトロの頭"
    elif "イントロ" in where and any(w in where for w in ("最後", "直前")):
        ins = [h for h, e in blocks if re.match(r"^\s*\[verse", lines[h], re.I)][:1]
        how = "イントロの最後（1 番の直前）"
    elif "イントロ" in where:
        ins = [h + 1 for h, e in blocks if lines[h].strip().lower().startswith("[intro")][:1]
        how = "イントロの頭"
    elif "ブリッジの前" in where:
        ins = [h for h, e in blocks if lines[h].strip().lower().startswith("[bridge")][:1]
        how = "ブリッジの直前"
    elif "サビ" in where and "直後" in where:
        ins = [e for h, e in choruses]
        how = "各サビの直後"
    elif "サビ" in where and ("直前" in where) or "プリコーラスの最後" in where:
        ins = [h for h, e in choruses]
        how = "各サビの直前"
    elif re.search(r"1 ?番", where) and "サビ" in where:
        ln = _last_sung(lines, *choruses[0]) if choruses else None
        ins, how = ([ln] if ln is not None else []), "1 番のサビの最後の行"
    elif "サビ" in where and any(w in where for w in ("最終行", "最後の行", "最後の語")):
        ins = [x for x in (_last_sung(lines, h, e) for h, e in choruses) if x is not None]
        how = "各サビの最後の行"
    elif "ブリッジ" in where:
        ins = [h + 1 for h, e in blocks if lines[h].strip().lower().startswith("[bridge")][:1]
        how = "ブリッジの頭"
    elif "真ん中" in where and blocks:
        mid = len(lines) / 2
        ins, how = [min((h for h, e in blocks), key=lambda h: abs(h - mid))], "曲の真ん中のセクションの手前"
    if not ins:
        return lyrics, None
    for i in sorted(set(ins), reverse=True):
        lines.insert(i, f"[{tag}]")
    return "\n".join(lines), how


def banned_terms(artist: dict) -> list[str]:
    try:
        import write_brief
        return write_brief.banned_terms(artist)
    except Exception:  # noqa: BLE001
        return list((artist.get("profile") or {}).get("favorite_artists_real", []))


def design(song: dict) -> dict:
    """曲 1 つの設計図。{軸: 値…, 'checks': [(軸, 状態 ok/warn/info, 説明)], 'map': [(軸, 値, イメージのどこから)]}"""
    artist = load_artist(song["slug"], song.get("label_slug"), missing_ok=True) or {}
    sound = artist.get("sound") or {}
    opt = _songs.suno_options(song)
    checks, mapping = [], []

    # --- 2 スタイル：ジャンル → テンポ → 声 → 楽器・音色 → 質感・場面（前ほど強く効く）
    base = _split(song.get("style", ""))
    text = ", ".join(base)
    front = []
    genres = list(sound.get("genres") or [])
    if genres and not any(_has(text, g) for g in genres[:2]):
        front.append(genres[0])
        checks.append(("スタイル", "info", f"先頭にジャンル「{genres[0]}」を足しました"))
    bpm = song.get("bpm")
    if bpm and not re.search(r"\bbpm\b", text, re.I):
        front.append(f"{bpm} BPM")
        checks.append(("スタイル", "info", f"テンポ「{bpm} BPM」を足しました"))
    want_gender = opt.get("vocal_gender")
    vt = opt.get("vocal_tag")
    if not song.get("instrumental") and vt:
        need = (want_gender in GENDER_WORD and not re.search(GENDER_WORD[want_gender], text, re.I)) or \
               (want_gender is None and not any(_has(text, w) for w in _split(vt)))
        if need:
            front.append(vt)
            checks.append(("スタイル", "info", f"声の説明「{vt}」を足しました"))
    tail = [t for t in (sound.get("dna_tags") or []) if not _has(text, t)]
    style = ", ".join(_dedupe(front + base))   # 足りないジャンル・テンポ・声は先頭に（前ほど強く効く）
    added = []
    for t in tail:
        cand = f"{style}, {t}" if style else t
        if len(cand) > STYLE_MAX:
            checks.append(("スタイル", "warn", f"文字数の上限で「{t}」を入れられませんでした"))
            continue
        style, added = cand, added + [t]
    if added:
        checks.append(("スタイル", "info", "設定書の特徴語を足しました：" + "・".join(added)))
    if len(style) > STYLE_MAX:
        checks.append(("スタイル", "warn", f"スタイルが {len(style)} 文字で、上限 {STYLE_MAX} 文字を超えています（後ろが切れます）"))
    have = [t for t in (sound.get("dna_tags") or []) if _has(style, t)]
    if sound.get("dna_tags"):
        checks.append(("スタイル", "ok" if len(have) == len(sound["dna_tags"]) else "warn",
                       f"設定書の特徴語 {len(have)}／{len(sound['dna_tags'])} が入っています"))
    mapping.append(("スタイル", style, "設定書の音（ジャンル・テンポ・楽器・特徴語）と声 ＋ ブリーフのスタイル"))

    # --- 3 除外するスタイル
    ex = _dedupe(_split(song.get("exclude", "")) + list(opt.get("exclude") or []))
    clash = [e for e in ex if _has(style, e)]
    if clash:
        ex = [e for e in ex if e not in clash]
        checks.append(("除外", "warn", "スタイルにも入っている言葉は除外から外しました：" + "・".join(clash)))
    exclude = ", ".join(ex)
    while len(exclude) > EXCLUDE_MAX and ex:
        ex.pop()
        exclude = ", ".join(ex)
    mapping.append(("除外するスタイル", exclude, "設定書の禁止事項（never・avoid）＋ 曲ごとの指定"))

    # --- 1 歌詞
    lyrics = "" if song.get("instrumental") else str(song.get("lyrics") or "")
    if lyrics:
        secs = re.findall(r"^\s*\[(intro|verse|pre-chorus|chorus|bridge|outro|post-chorus|final chorus|hook)[^\]]*\]", lyrics, re.I | re.M)
        checks.append(("歌詞", "ok" if len(secs) >= 3 else "warn", f"セクションのタグ {len(secs)} 個（Verse・Chorus など）"))
        extra = [(t["tag"], t.get("why", t["tag"])[:40], t.get("where", "")) for t in (opt.get("extra_tags") or [])]
        for tag, ja, where in _tic_tags(artist) + extra:
            if _tag_present(lyrics, tag):
                checks.append(("歌詞", "ok", f"毎曲の癖「{ja}」のタグが入っています"))
                continue
            new, how = insert_tag(lyrics, tag, where)
            if how:
                lyrics = new
                checks.append(("歌詞", "ok", f"毎曲の癖「{ja}」のタグ [{tag}] を {how} に足しました（言葉は変えていません）"))
            else:
                checks.append(("歌詞", "warn", f"毎曲の癖「{ja}」のタグ [{tag}] が無く、入れる位置（{where}）を決められませんでした"))
        if "{{SUNO_" in lyrics:
            checks.append(("歌詞", "warn", "Suno に書かせる部分（{{SUNO_…}}）が残っています。歌詞の差し込みを先に済ませてください"))
    mapping.append(("歌詞", f"{len(lyrics.splitlines())} 行" if lyrics else "（なし：インスト）", "ブリーフ／作業セットの歌詞と、設定書の毎曲の癖"))

    # --- 4〜9 その他のオプション
    if "vocal_gender" in opt:
        mapping.append(("ボーカルの性別", _songs.GENDER_JA.get(want_gender, str(want_gender)), "設定書の声（性別・編成）"))
    for k, ja in (("weirdness", "Weirdness"), ("style_influence", "Style Influence")):
        if opt.get(k) is not None:
            mapping.append((ja, f"{opt[k]}%", "組ごとの設定（suno_options.json の理由を参照）"))
    mapping.append(("インスト", "オン" if song.get("instrumental") else "オフ", "曲の言語（歌の有無）"))
    if opt.get("model"):
        mapping.append(("モデル", opt["model"], "全組共通（suno_options.json の default）"))
    if opt.get("persona"):
        mapping.append(("ペルソナ", opt["persona"], "声を毎回同じにする"))
    elif (artist.get("vocal") or {}).get("fixed_timbre") and not song.get("instrumental"):
        checks.append(("ペルソナ", "info", "声を毎回同じにするなら、気に入ったテイクから Suno でペルソナを作り、"
                                            "suno_options.json の persona に名前を書いてください"))
    mapping.append(("曲のタイトル", song.get("title", ""), "作業セット／ブリーフの曲名"))

    # --- 実在の名前が入っていないか（Suno に実名は入れない）
    blob = " ".join([style, exclude, lyrics]).lower()
    hits = [t for t in banned_terms(artist) if len(t) >= 3 and t.lower() in blob]
    if hits:
        checks.append(("全体", "warn", "実在の名前が入っています：" + "・".join(hits[:5])))

    return {"title": song.get("title", ""), "lyrics": lyrics, "style": style, "exclude": exclude,
            "vocal_gender": want_gender if "vocal_gender" in opt else "unset",
            "weirdness": opt.get("weirdness"), "style_influence": opt.get("style_influence"),
            "instrumental": bool(song.get("instrumental")), "model": opt.get("model"), "persona": opt.get("persona"),
            "checks": checks, "map": mapping}


def score(d: dict) -> tuple[int, int]:
    """(確かめて OK の数, 確かめた数)"""
    rated = [c for c in d["checks"] if c[1] in ("ok", "warn")]
    return sum(c[1] == "ok" for c in rated), len(rated)


def report(song: dict, d: dict | None = None) -> str:
    d = d or design(song)
    ok, n = score(d)
    mark = {"ok": "✓", "warn": "⚠", "info": "＋"}
    lines = [f"■ {song['artist']}「{song['title']}」（{song['id']}）　イメージの写し具合：{ok}／{n}"]
    lines += [f"  {k}：{v if len(str(v)) < 90 else str(v)[:87] + '…'}　← {src}" for k, v, src in d["map"]]
    lines += [f"  {mark[s]} {k}：{t}" for k, s, t in d["checks"]]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Suno 設計図（曲のイメージを Suno の全部の軸に写す）")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.ids:
        songs = [s for s in (_songs.by_id(i) for i in a.ids) if s]
    else:
        import publish
        songs = publish.suno_todo()
    for s in songs:
        d = design(s)
        print(json.dumps(d, ensure_ascii=False, indent=1) if a.json else report(s, d) + "\n")


if __name__ == "__main__":
    main()

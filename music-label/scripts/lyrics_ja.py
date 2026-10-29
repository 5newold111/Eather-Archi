#!/usr/bin/env python3
"""
歌詞の日本語訳（オーナーの指示 2026-10-27。決定 107）

  作詞のたびに、歌詞の日本語訳を作って載せる。見るのは Gon さんだけ：Suno と DistroKid には入れない（歌詞は歌詞だけ。決定 93）。
  ・訳は 1 行ずつ（元の歌詞と同じ行数）。[Chorus] などの札は [サビ] のように日本語にし、( ) の掛け声も訳す
  ・歌詞の中身ごとに保存（out/lyrics_ja/<歌詞の指紋>.json）。歌詞を直すと指紋が変わるので、次に要るときに作り直す（同じ歌詞なら作り直さない）
  ・日本語の歌詞・インスト・Suno がまだ書いていない歌詞（{{SUNO_…}} が残っている）は訳さない
  ・Claude（効き目の低い設定・答えも短い）。1 曲 数円。鍵が無いときは作らない（「訳はまだ」と出す）

載せる場所：曲の案のポップアップ（不定期）・公開の準備のページ・公開セットの「歌詞_日本語訳.txt」・楽曲管理シートの「歌詞の日本語訳」
  （どこも、英語の歌詞を全部 → その下の別枠に日本語訳だけ。オーナーの指示 2026-10-27）

使い方：
  python3 scripts/lyrics_ja.py song <曲 ID>      # その曲の訳を作る（あれば作らない）
  python3 scripts/lyrics_ja.py brief <ブリーフ.json>   # ブリーフ（不定期の曲の案も）の歌詞の訳を作る
  python3 scripts/lyrics_ja.py all               # 訳がまだの曲を全部
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, read_json, write_json  # noqa: E402

CACHE = OUT / "lyrics_ja"
TAG = re.compile(r"^\s*\[[^\]]+\]\s*$")

SYSTEM = """You translate song lyrics into natural Japanese for the songwriter (a Japanese reader) to understand the meaning.
Rules:
- Return exactly one output line per input line, in the same order (the same number of lines). Empty input lines stay empty strings.
- Section tags in square brackets become Japanese section names in square brackets: [Verse 1] -> [1番], [Pre-Chorus] -> [サビ前],
  [Chorus] -> [サビ], [Post-Chorus] -> [サビ後], [Bridge] -> [ブリッジ], [Intro] -> [イントロ], [Outro] -> [アウトロ],
  [Hook] -> [フック], [Instrumental] -> [間奏]; performance tags like [Whisper] [Humming] [Fade Out] become short Japanese in brackets.
- Words in parentheses (backing vocals, ad-libs) are translated inside parentheses.
- Natural, readable Japanese that keeps the imagery and tone; not word-for-word, but do not add or remove meaning. No notes or explanations.
- Vocal sounds (oh, la la, mm) may stay as katakana or as they are."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["lines"],
          "properties": {"lines": {"type": "array", "items": {"type": "string"}}}}


def _norm(lyrics: str) -> str:
    return "\n".join(ln.rstrip() for ln in str(lyrics or "").strip().splitlines())


def fingerprint(lyrics: str) -> str:
    return hashlib.sha1(_norm(lyrics).encode("utf-8")).hexdigest()[:16]


def needed(lyrics: str, language: str | None = None) -> bool:
    """訳が要る歌詞か（日本語の歌詞・インスト・Suno の書く節が残っている歌詞は要らない）"""
    t = _norm(lyrics)
    if not t or "{{SUNO_" in t:
        return False
    if str(language or "").lower() in ("ja", "japanese", "none", "instrumental"):
        return False
    words = [ln for ln in t.splitlines() if ln.strip() and not TAG.match(ln)]
    if not words:
        return False   # 札だけ（インスト）
    kana = sum(1 for ch in "".join(words) if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿")
    return kana < len("".join(words)) * 0.3   # ほとんど日本語なら訳さない


def get(lyrics: str) -> dict | None:
    """保存してある訳（無ければ None）。{lines, text, at, by}"""
    p = CACHE / f"{fingerprint(lyrics)}.json"
    try:
        return read_json(p) if p.exists() else None
    except (OSError, ValueError):
        return None


def ensure(lyrics: str, language: str | None = None, quiet: bool = False) -> dict | None:
    """訳を用意する（あれば作らない）。作れなかったら None"""
    if not needed(lyrics, language):
        return None
    have = get(lyrics)
    if have:
        return have
    import _claude
    if not _claude.available():
        if not quiet:
            print("   － 歌詞の日本語訳：Claude の鍵が無いので作っていません")
        return None
    src = _norm(lyrics).splitlines()
    user = "Translate these lyrics. Input lines (JSON array):\n" + json.dumps(src, ensure_ascii=False)
    res, st = _claude.call_json(SYSTEM, user, SCHEMA, effort="low", max_tokens=8000)
    if not res or not isinstance(res.get("lines"), list):
        if not quiet:
            print(f"   － 歌詞の日本語訳を作れませんでした（{st}）")
        return None
    lines = [str(x) for x in res["lines"]]
    if len(lines) != len(src):   # 行の数がずれたとき：空行を除いた数が合えば、元の空行の位置に合わせ直す（合わなければ対訳にしない）
        body = [x for x in lines if x.strip()]
        if len(body) == sum(1 for x in src if x.strip()):
            it = iter(body)
            lines = ["" if not x.strip() else next(it) for x in src]
    out = {"lines": lines, "text": "\n".join(lines), "paired": len(lines) == len(src), "source": "\n".join(src),
           "at": datetime.now().isoformat(timespec="seconds"), "by": "Claude"}
    write_json(CACHE / f"{fingerprint(lyrics)}.json", out)
    if not quiet:
        print(f"   ○ 歌詞の日本語訳を作りました（{len(lines)} 行）")
    return out


def pairs(lyrics: str, tr: dict | None) -> list[tuple[str, str]]:
    """（元の行, 訳の行）の並び。訳が無い・行がずれているときは訳を空に"""
    src = _norm(lyrics).splitlines()
    if not tr or not tr.get("paired"):
        return [(s, "") for s in src]
    return list(zip(src, tr["lines"]))


def pair_text(lyrics: str, tr: dict | None) -> str:
    """公開セットの 歌詞_日本語訳.txt：英語の歌詞を全部 → 区切り → 日本語訳だけ（オーナーの指示 2026-10-27）"""
    if not tr:
        return ""
    return _norm(lyrics) + "\n\n" + "――――― 日本語訳（読む用。配信には入れない） ―――――" + "\n\n" + str(tr.get("text", "")).strip() + "\n"


def pair_html(lyrics: str, tr: dict | None, esc) -> str:
    """ページに載せる形：英語の歌詞を全部 → 下の別枠に日本語訳だけ（作曲の案のポップアップと同じ形）"""
    if not tr:
        return ""
    return ('<div style="white-space:pre-wrap;font-size:13px;line-height:1.6">' + esc(_norm(lyrics)) + "</div>"
            '<div style="margin:10px 0 4px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg)">'
            '<b>日本語訳</b> <span style="color:var(--sub);font-size:12px">（読む用。Suno・DistroKid には上の英語の歌詞だけを入れます）</span>'
            '<div style="white-space:pre-wrap;font-size:13px;line-height:1.6;margin-top:6px">' + esc(str(tr.get("text", "")).strip()) + "</div></div>")


# ---------------------------------------------------------------------------
def for_song(song: dict, quiet: bool = False, make: bool = True) -> dict | None:
    lang = song.get("language")
    if song.get("instrumental"):
        return None
    return ensure(song.get("lyrics", ""), lang, quiet) if make else (get(song.get("lyrics", "")) if needed(song.get("lyrics", ""), lang) else None)


def for_brief(path: Path, quiet: bool = False) -> dict | None:
    b = read_json(path)
    final = path.with_suffix(".lyrics_final.txt")
    lyr = final.read_text(encoding="utf-8") if final.exists() else str(b.get("suno_lyrics_final") or b.get("suno_lyrics") or "")
    if b.get("language") == "none" or b.get("lyrics_mode") == "instrumental":
        return None
    return ensure(lyr, b.get("language"), quiet)


def main() -> None:
    ap = argparse.ArgumentParser(description="歌詞の日本語訳")
    ap.add_argument("cmd", choices=["song", "brief", "all"])
    ap.add_argument("target", nargs="?")
    a = ap.parse_args()
    import _songs
    if a.cmd == "song":
        s = _songs.by_id(a.target or "")
        if not s:
            sys.exit(f"[エラー] 曲 {a.target} が見つかりません")
        tr = for_song(s)
        print("【結果】" + (f"{s['artist']}「{s['title']}」の日本語訳があります（{len(tr['lines'])} 行）" if tr else "訳は要りません（日本語・インスト）か、作れませんでした"))
    elif a.cmd == "brief":
        tr = for_brief(Path(a.target or ""))
        print("【結果】" + ("日本語訳があります" if tr else "訳は要りません（日本語・インスト・Suno がまだ書いていない歌詞）か、作れませんでした"))
    else:
        n = 0
        for s in _songs.all_songs():
            if str(s.get("week", "")).startswith("2000-") or not needed(s.get("lyrics", ""), s.get("language")) or get(s.get("lyrics", "")):
                continue
            print(f"【進み具合】{s['artist']}「{s['title']}」", flush=True)
            if for_song(s):
                n += 1
        print(f"【結果】{n} 曲の日本語訳を作りました")


if __name__ == "__main__":
    main()

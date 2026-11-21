"""
歌詞の書き方の決まり（DistroKid・Apple Music の歌詞の決まり。オーナーの指示 2026-10-26。決定 93。決定 92 を広げたもの）

歌詞を「作るとき」（Claude・Suno の歌詞づくり・Suno に入れる直前）も、「配信サイトに出すとき」（store_lyrics.py）も、同じ決まりで整える。
  ・行の頭は大文字（英語などのアルファベット）。全部大文字・全部小文字にしない（i → I）
  ・行末に句読点を付けない（! と ? だけは残す。… や ... も消す）
  ・行の頭と終わりに空白を入れない。空の行は段落（Verse・Chorus など）の間だけ
  ・1〜10 の数字は言葉で書く（英語の行は one〜ten、日本語の行は 一〜十。時刻「2:40」や「401」はそのまま）
  ・標準の綴り（tryna → trying to、gonna → going to、runnin' → running など）
  ・繰り返しは書き出す（「Chorus 2x」と書かない）・歌い手の名前や余分な文字（「Chorus」・SNS）を入れない・伏せ字にしない
    → 作るときは Claude・Suno への決まり（RULES_EN）で守らせ、配信サイトに出すときは store_lyrics.py が直す
  ・1 行 1 文（長すぎる行にしない）→ 配信サイトに出すときは、行の途中の文の切れ目で行を分ける（split_sentences）
Suno に入れる歌詞では [Verse 1] などの札（Suno への構成の指示）は残す。配信サイトに出す歌詞では消す（store_lyrics.py）
"""
from __future__ import annotations

import re

# Claude と Suno の歌詞づくりに渡す決まり（英語）
RULES_EN = (
    "Lyric format rules (the owner's rules, from the DistroKid / Apple Music lyric guidelines): "
    "lyrics only - never a singer's name, speaker labels, notes or links inside the lines; "
    "write out every repeated line and every repeated chorus in full (never 'Chorus 2x', 'repeat', 'x2'); "
    "begin each line with a capital letter, normal sentence case (never all caps or all lowercase; 'I' is capitalized); "
    "no punctuation at the end of a line except ! or ? (no commas, periods, ellipses, semicolons, colons; no 、 or 。); "
    "one sentence per line, no overly long lines; no leading or trailing spaces; blank lines only between sections; "
    "write numbers one to ten as words (two, not 2; in Japanese 二, not 2); "
    "use standard spelling (trying to, going to, want to, got to, kind of, because, running - not tryna, gonna, wanna, "
    "gotta, kinda, cuz, runnin'); never censor with asterisks."
)
NO_END_PUNCT_NOTE = RULES_EN   # 決定 92 の名前のまま使っている所のため

NUM_EN = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten"}
NUM_JA = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七", "8": "八", "9": "九", "10": "十"}
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")

# 標準の綴り（DistroKid の例：Tryna → Trying to）。ain't・y'all・ya は辞書にもある言い方なので直さない
SLANG = [(r"tryna", "trying to"), (r"gonna", "going to"), (r"wanna", "want to"), (r"gotta", "got to"), (r"kinda", "kind of"),
         (r"sorta", "sort of"), (r"outta", "out of"), (r"lemme", "let me"), (r"gimme", "give me"), (r"dunno", "don't know"),
         (r"i'?mma", "I'm going to"), (r"cuz|'cause|'cos", "because"), (r"'til|til", "until"), (r"nothin'", "nothing"), (r"somethin'", "something")]
_END = re.compile(r"(?:\s*(?:\.\.\.|[、。，．,.;:；：…‥\-–—]))+(?=[」』）)\"'”’]*\s*$)")
_TAG_LINE = re.compile(r"\s*(\[[^\]]*\]\s*)+")


def _keep_case(src: str, rep: str) -> str:
    return rep[0].upper() + rep[1:] if src[:1].isupper() else rep


def standard_spelling(line: str) -> tuple[str, list[str]]:
    """くだけた綴りを標準の綴りに。(直した行, 直した語)"""
    fixed = []
    for pat, rep in SLANG:
        def sub(m, rep=rep):
            fixed.append(m.group(0))
            return _keep_case(m.group(0), rep) if not rep.startswith("I'm") else rep
        line = re.sub(rf"(?<![\w'])(?:{pat})(?![\w'])", sub, line, flags=re.I)
    def g_drop(m):   # runnin' → running（goin' doin' など）
        fixed.append(m.group(0))
        return m.group(1) + "ing"
    line = re.sub(r"\b([A-Za-z]{2,}?)in'(?![\w'])", g_drop, line)
    return line, fixed


def end_punct(line: str) -> str:
    """行末の句読点を消す（! ? と、U.S.A. のような略語は残す）"""
    body = line.rstrip()
    if re.search(r"(?:\b[A-Za-z]\.){2,}$", body):
        return body
    return _END.sub("", body)


def numbers(line: str) -> str:
    """単独の 1〜10 を言葉に（英語の行は one〜ten、日本語の行は 一〜十）。2:40・401・1st・10% は触らない"""
    if CJK.search(line):
        return re.sub(r"(?<![0-9０-９:.,/])(10|[1-9])(?![0-9０-９:.,/%])", lambda m: NUM_JA[m.group(0)], line)
    def word(m):
        w = NUM_EN[m.group(0)]
        return w.capitalize() if not line[:m.start()].strip(" (\"'") else w
    return re.sub(r"(?<![\w:.,/-])(10|[1-9])(?![\w:.,/%-])", word, line)


def cap(line: str) -> str:
    """行の頭（( の中で始まるときはその中の頭）のアルファベットを大文字に。i・i'm・i'll は I に"""
    line = re.sub(r"(?<![\w'])i(?=(?:'[a-z]+)?(?![\w']))", "I", line)
    m = re.match(r"^([\s(\"'¿¡]*)([a-z])", line)
    return line if not m else line[:m.end(1)] + m.group(2).upper() + line[m.end(2):]


ABBR = {"mr", "mrs", "ms", "dr", "st", "jr", "sr", "mt", "vs"}


def split_sentences(line: str) -> list[str]:
    """1 行に 2 文以上あれば、文の切れ目で分ける（. ! ? の後に大文字、または 。！？ の後。Mr. や U.S. などの略語では分けない）"""
    cuts = []
    for m in re.finditer(r"([.!?])\s+(?=[\"'(]?[A-Z])|([。！？])\s*(?=\S)", line):
        if m.group(1) == ".":
            word = re.search(r"([A-Za-z.]+)\.$", line[:m.start() + 1])
            w = word.group(1).lower() if word else ""
            if w in ABBR or re.fullmatch(r"(?:[a-z]\.)*[a-z]", w):   # Mr. / U.S. / A.
                continue
        cuts.append(m.end())
    parts, start = [], 0
    for c in cuts:
        parts.append(line[start:c]); start = c
    parts.append(line[start:])
    return [p for p in (x.strip() for x in parts) if p]


PROPER = {w.lower(): w for w in "Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July "
          "August September October November December God".split()}


def unshout(line: str, names: set[str] | None = None) -> str:
    """全部大文字の英語の行を、ふつうの書き方（頭だけ大文字・I と曜日・月・名前は大文字）にする。そうでない行はそのまま"""
    letters = re.sub(r"[^A-Za-z]", "", line)
    if len(letters) < 4 or not letters.isupper():
        return line
    low = line.lower()
    for w in set(re.findall(r"[a-z][a-z']*", low)):
        fix = PROPER.get(w) or next((n for n in (names or ()) if n.lower() == w), None)
        if fix:
            low = re.sub(rf"\b{re.escape(w)}\b", fix, low)
    return cap(low)


def tidy_line(line: str) -> str:
    """歌詞の 1 行を決まりどおりに整える（札・差し込み位置の行はそのまま）"""
    s = line.strip()
    if not s or _TAG_LINE.fullmatch(s) or "{{SUNO_" in s:
        return s
    s = re.sub(r"\s{2,}", " ", s)
    s, _ = standard_spelling(s)
    s = end_punct(s)
    s = numbers(s)
    return cap(unshout(s)) if re.match(r"^[\s(\"'¿¡]*[A-Za-z]", s) else s


def tidy(text: str) -> str:
    """作った歌詞（Suno に入れる形。札は残す）を決まりどおりに整える。空の行は段落の間（次が札の行）だけ 1 行"""
    if not text:
        return text
    lines = [tidy_line(x) for x in str(text).replace("\r", "").split("\n")]
    has_tags = any(_TAG_LINE.fullmatch(x) for x in lines if x)
    out: list[str] = []
    for i, x in enumerate(lines):
        if x:
            out.append(x)
            continue
        nxt = next((y for y in lines[i + 1:] if y), None)
        if out and out[-1] and nxt is not None and (not has_tags or _TAG_LINE.fullmatch(nxt) or "{{SUNO_" in nxt):
            out.append("")
    return "\n".join(out).strip("\n")

#!/usr/bin/env python3
"""
曲を「聞いて」声・楽器・ジャンル・ムードを判定する（Mac の中。無料。決定 103）

  Claude の API（1 曲 20〜50 円）を使わずに、解析シートの「音から分かる項目」を埋める部品。
  使うのは音楽判定モデル（AudioSet という 527 種類の音の名札で学んだ AST というモデル）。初回に約 350 MB を自動で取ってくる。

  流れ（1 曲 15〜40 秒）：
    1 曲の 25%・50%・75% の位置から 10 秒ずつ切り出す（イントロ・アウトロだけで決めない）
    2 3 か所をモデルに聞かせ、名札ごとの「それらしさ」（0〜1）を平均する
    3 名札を解析シートの欄に直す：
        声の性別（男性の歌・女性の歌・子どもの歌・合唱）・歌い方（ラップ・合唱・ハミング）・声の高さ（メロディーを起こした曲は音域から）
        楽器（名前と役割：主役・伴奏・リズム・色づけ）・編成の感じ・ジャンル・ムード

  分からないもの（歌のテーマ・世界観・アーティストの背景・歌詞の聞き違い）は、Claude Code（月額プラン）で埋める（reference_db.py --enrich-sub）

必要なもの：python3 -m pip install torch transformers（約 1 GB。ダッシュボードの「曲を聞いて判定する部品を入れる」でも入る）
使い方：python3 scripts/audio_tags.py <音源>   # 1 曲だけ判定して表示（試し）
        python3 scripts/audio_tags.py --check    # 部品とモデルが使えるか・名札の対応表がモデルと合っているかの点検
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000
WIN_SEC = 10.0

_M = None   # (特徴の取り出し器, モデル, 名札)


def ready() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _model():
    global _M
    if _M is None:
        import torch
        from transformers import ASTForAudioClassification, AutoFeatureExtractor
        torch.set_num_threads(max(1, (__import__("os").cpu_count() or 2) - 1))
        fe = AutoFeatureExtractor.from_pretrained(MODEL)
        m = ASTForAudioClassification.from_pretrained(MODEL).eval()
        _M = (fe, m, m.config.id2label)
    return _M


def windows(path: Path) -> list:
    """曲の 25%・50%・75% から 10 秒ずつ（短い曲は全体を 1 つ）"""
    import librosa
    y, _ = librosa.load(str(path), sr=SR, mono=True)
    n, w = len(y), int(WIN_SEC * SR)
    if n <= w * 1.5:
        return [y]
    out = []
    for c in (0.25, 0.5, 0.75):
        a = max(0, min(n - w, int(n * c) - w // 2))
        out.append(y[a:a + w])
    return out


def scores(path: Path) -> dict[str, float]:
    """名札ごとの「それらしさ」（0〜1。3 か所の平均）"""
    import torch
    fe, m, labels = _model()
    ws = windows(path)
    inp = fe(ws, sampling_rate=SR, return_tensors="pt")
    with torch.no_grad():
        p = torch.sigmoid(m(**inp).logits).mean(dim=0)
    return {labels[i]: round(float(v), 4) for i, v in enumerate(p.tolist())}


# ---------------------------------------------------------------------------
# 名札 → 解析シートの欄
VOICE = {"Male singing": "male", "Female singing": "female", "Child singing": "child"}
SINGING = ("Singing", "Male singing", "Female singing", "Child singing", "Choir", "Rapping", "Humming", "Yodeling",
           "Chant", "Vocal music", "A capella", "Beatboxing")
DELIVERY = {"Rapping": "ラップ", "Choir": "合唱", "Humming": "ハミング", "A capella": "アカペラ", "Chant": "唱えるような歌い方",
            "Beatboxing": "ビートボックス", "Yodeling": "ヨーデル"}
# 名札：(楽器の名前, 役割)  役割：lead 主役 / accompaniment 伴奏 / rhythm リズム / color 色づけ
INSTR = {
    "Drum kit": ("drum kit", "rhythm"), "Drum machine": ("drum machine", "rhythm"), "Snare drum": ("snare drum", "rhythm"),
    "Bass drum": ("kick drum", "rhythm"), "Hi-hat": ("hi-hat", "rhythm"), "Cymbal": ("cymbals", "rhythm"),
    "Percussion": ("percussion", "rhythm"), "Tambourine": ("tambourine", "rhythm"), "Tabla": ("tabla", "rhythm"),
    "Bass guitar": ("bass guitar", "rhythm"), "Double bass": ("upright bass", "rhythm"),
    "Electric guitar": ("electric guitar", "accompaniment"), "Acoustic guitar": ("acoustic guitar", "accompaniment"),
    "Steel guitar, slide guitar": ("slide guitar", "color"), "Bass (instrument role)": ("bass", "rhythm"),
    "Piano": ("piano", "accompaniment"), "Electric piano": ("electric piano", "accompaniment"), "Keyboard (musical)": ("keyboard", "accompaniment"),
    "Organ": ("organ", "accompaniment"), "Hammond organ": ("hammond organ", "accompaniment"), "Synthesizer": ("synthesizer", "accompaniment"),
    "Sampler": ("sampler", "color"), "Harpsichord": ("harpsichord", "accompaniment"),
    "String section": ("string section", "accompaniment"), "Orchestra": ("orchestra", "accompaniment"),
    "Violin, fiddle": ("violin", "color"), "Cello": ("cello", "color"), "Harp": ("harp", "color"),
    "Brass instrument": ("brass", "color"), "Trumpet": ("trumpet", "color"), "Trombone": ("trombone", "color"),
    "French horn": ("french horn", "color"), "Saxophone": ("saxophone", "color"), "Flute": ("flute", "color"),
    "Clarinet": ("clarinet", "color"), "Harmonica": ("harmonica", "color"), "Accordion": ("accordion", "color"),
    "Ukulele": ("ukulele", "accompaniment"), "Banjo": ("banjo", "accompaniment"), "Mandolin": ("mandolin", "accompaniment"),
    "Marimba, xylophone": ("marimba", "color"), "Vibraphone": ("vibraphone", "color"), "Glockenspiel": ("glockenspiel", "color"),
    "Bell": ("bells", "color"), "Sitar": ("sitar", "color"), "Scratching (performance technique)": ("turntable scratch", "color"),
}
GENRE = {
    "Pop music": "pop", "Rock music": "rock", "Hip hop music": "hip hop", "Rhythm and blues": "r&b", "Soul music": "soul",
    "Electronic music": "electronic", "Electronica": "electronica", "Electronic dance music": "edm", "House music": "house",
    "Techno": "techno", "Dubstep": "dubstep", "Drum and bass": "drum and bass", "Trance music": "trance", "Ambient music": "ambient",
    "Jazz": "jazz", "Blues": "blues", "Funk": "funk", "Disco": "disco", "Reggae": "reggae", "Ska": "ska", "Country": "country",
    "Folk music": "folk", "Bluegrass": "bluegrass", "Classical music": "classical", "Opera": "opera", "New-age music": "new age",
    "Heavy metal": "metal", "Punk rock": "punk", "Grunge": "grunge", "Progressive rock": "progressive rock",
    "Psychedelic rock": "psychedelic rock", "Rock and roll": "rock and roll", "Independent music": "indie", "Gospel music": "gospel",
    "Music of Latin America": "latin", "Salsa music": "salsa", "Flamenco": "flamenco", "Afrobeat": "afrobeat",
    "Swing music": "swing", "Vocal music": "vocal", "A capella": "a cappella", "Music for children": "children's",
    "Christmas music": "christmas", "Lullaby": "lullaby", "Soundtrack music": "soundtrack", "Video game music": "game music",
}
MOOD = {"Happy music": "明るい", "Sad music": "切ない", "Tender music": "やさしい", "Exciting music": "高揚感のある",
        "Angry music": "激しい", "Scary music": "不穏な", "Funny music": "おどけた"}
T_VOICE, T_INSTR, T_GENRE, T_MOOD = 0.12, 0.10, 0.08, 0.10


def _midi(name: str) -> int | None:
    m = re.match(r"([A-G]#?)(-?\d)", name or "")
    if not m:
        return None
    return ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"].index(m.group(1)) + (int(m.group(2)) + 1) * 12


def voice_range(sex: str, melody: dict | None) -> str:
    """声の高さ（メロディーを起こした曲だけ。よく使う高さの真ん中で決める）"""
    core = ((melody or {}).get("range") or {}).get("core") or ""
    ns = [x for x in (_midi(t) for t in re.findall(r"[A-G]#?-?\d", core)) if x is not None]
    if not (melody or {}).get("usable") or len(ns) < 2 or sex not in ("male", "female", "child"):
        return "unknown"
    mid = sum(ns[:2]) / 2
    lo, hi = {"male": (52, 59), "female": (62, 70), "child": (64, 72)}[sex]   # 男性 E3〜B3・女性 D4〜A#4 をふつうの高さとみる
    return "low" if mid < lo else "high" if mid > hi else "mid"


def to_sheet(sc: dict[str, float], melody: dict | None = None) -> dict:
    """名札の点数 → 解析シートに入れる形（reference_db._merge_tags が使う）"""
    get = lambda k: sc.get(k, 0.0)  # noqa: E731
    sing = max(get(k) for k in SINGING)
    voices = {v: get(k) for k, v in VOICE.items() if get(k) >= T_VOICE}
    if get("Choir") >= T_VOICE:
        voices.setdefault("mixed", get("Choir"))
    if sing < T_VOICE and not voices:
        sex = "none"   # 歌がほとんど無い（インスト）
    elif {"male", "female"} <= set(voices) and min(voices["male"], voices["female"]) >= 0.5 * max(voices["male"], voices["female"]):
        sex = "mixed"
    elif voices:
        sex = max(voices, key=voices.get)
        sex = "female" if sex == "child" else sex   # 子どもの声は高い女声の扱い（シートの決まりに child が無い）
    else:
        sex = "unknown"
    delivery = [v for k, v in DELIVERY.items() if get(k) >= T_VOICE]
    ins = sorted(((get(k), *v) for k, v in INSTR.items() if get(k) >= T_INSTR), reverse=True)
    seen, inst = set(), []
    for s, name, role in ins:
        if name in seen:
            continue
        seen.add(name)
        inst.append({"name": name, "role": role, "score": round(s, 2)})
    if sex == "none" and inst:   # インスト：いちばんはっきり聞こえる旋律楽器を主役に
        lead = next((x for x in inst if x["role"] != "rhythm"), None)
        if lead:
            lead["role"] = "lead"
    names = {x["name"] for x in inst}
    if {"drum kit", "bass guitar"} & names and {"electric guitar", "acoustic guitar"} & names:
        feel = "バンド編成"
    elif {"synthesizer", "drum machine", "sampler"} & names and not {"electric guitar", "drum kit"} & names:
        feel = "打ち込み中心"
    elif {"orchestra", "string section"} & names:
        feel = "オーケストラ・弦が中心"
    elif "acoustic guitar" in names and len(names) <= 3:
        feel = "アコースティックギターの弾き語り寄り"
    elif "piano" in names and len(names) <= 3:
        feel = "ピアノが中心の少人数"
    else:
        feel = "・".join(x["name"] for x in inst[:3]) + " が中心" if inst else ""
    genres = [v for _, v in sorted(((get(k), v) for k, v in GENRE.items() if get(k) >= T_GENRE), reverse=True)][:4]
    moods = [v for _, v in sorted(((get(k), v) for k, v in MOOD.items() if get(k) >= T_MOOD), reverse=True)][:2]
    top = sorted(sc.items(), key=lambda x: -x[1])[:15]
    return {"vocal": {"sex": sex, "range": voice_range(sex, melody) if sex != "none" else "unknown", "delivery": delivery,
                      "singing": round(sing, 2)},
            "instruments": inst, "formation_feel": feel, "genre_tags": genres, "mood": "・".join(moods),
            "top": [[k, round(v, 3)] for k, v in top]}


def tag(path: Path, melody: dict | None = None) -> dict:
    return to_sheet(scores(path), melody)


def check() -> int:
    """部品・モデルの取り込み・名札の対応表を点検する（料金なし）"""
    if not ready():
        print("【結果】✕ 部品（torch・transformers）が入っていません。ターミナルで  python3 -m pip install torch transformers  を実行してください")
        return 2
    print(f"▶ 判定モデル {MODEL} を読み込みます（初回は約 350 MB を取ってきます）", flush=True)
    labels = set(_model()[2].values())
    mapped = set(VOICE) | set(SINGING) | set(DELIVERY) | set(INSTR) | set(GENRE) | set(MOOD)
    miss = sorted(mapped - labels)
    print(f"   モデルの名札 {len(labels)} 種類・こちらの対応表 {len(mapped)} 種類のうち、モデルに無いもの {len(miss)} 種類"
          + (f"：{'・'.join(miss)}" if miss else ""))
    print("【結果】" + ("○ 使えます" if len(miss) <= len(mapped) // 10 else "△ 対応表の名前がモデルと合っていないものが多い（上の一覧を Claude に見せて直してもらう）"))
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    if sys.argv[1] == "--check":
        sys.exit(check())
    if not ready():
        print("【結果】曲を聞いて判定する部品が入っていません。ターミナルで  python3 -m pip install torch transformers  を実行してください")
        sys.exit(2)
    r = tag(Path(sys.argv[1]))
    v = r["vocal"]
    print(f"声：{v['sex']}（高さ {v['range']}）{'・' + '・'.join(v['delivery']) if v['delivery'] else ''}")
    print("楽器：" + "・".join(f"{x['name']}（{x['role']}）" for x in r["instruments"]))
    print(f"編成：{r['formation_feel']}／ジャンル：{', '.join(r['genre_tags'])}／ムード：{r['mood']}")
    print("よく当てはまった名札：" + "・".join(f"{k} {s}" for k, s in r["top"][:8]))


if __name__ == "__main__":
    main()

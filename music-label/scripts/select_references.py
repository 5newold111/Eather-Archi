#!/usr/bin/env python3
"""
参考曲を「枠（スロット）」ごとに自動で割り当てる

docs/04_borrowing_rules.md のルール 1「1 曲 1 枠」とルール 2「ボーカル 6:2:2」を
人の注意力に頼らず機械的に守るためのスクリプト。

使い方：
  # デモ（架空のアーティスト 1 組と架空の参考曲 15 曲を内蔵。Supabase なしで動く）
  python3 scripts/select_references.py --demo

  # 自分で書いた設定書（templates/artists/<slug>.json）1 組ぶん。参考曲は解析シートのフォルダから
  python3 scripts/select_references.py --artist <slug> --references ./out/references/

  # 設定書すべてをまとめて（火曜朝の実行を想定）
  python3 scripts/select_references.py --all --references ./out/references/ --week 2026-10-05

出力：
  templates/brief.schema.json の「slots」部分を満たすブリーフの骨組み（JSON）。
  このあと Claude が Suno 用の指示文・歌詞案・タイトル案を書き足して、ブリーフが完成する。

標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import production_status  # noqa: E402  その週に作る組かどうか（デビュー前・隔週・休止）
from _life import choice_boost, pick_moment  # noqa: E402  管理表の人生（歌のもとになる瞬間・その組が選んだ参考曲）
ARTISTS_DIR = ROOT / "templates" / "artists"     # 本体レーベル（ドライブ）の設定書
LABELS_DIR = ROOT / "templates" / "labels"       # 子レーベル（設定書を artists 配列で内包）

# 借用の枠。順番は「埋めにくい枠から先に」。
# ボーカル枠は候補が少ない（音域・性別で絞る）ので最初に埋める。
SLOT_ORDER = [
    "vocal_main", "vocal_sub1", "vocal_sub2",
    "phrase", "groove", "harmony", "structure",
    "performance", "instruments", "worldview", "lyrics",
]
VOCAL_SLOTS = {"vocal_main": 0.6, "vocal_sub1": 0.2, "vocal_sub2": 0.2}

# 枠ごとの「借りたもの」の説明（ブリーフに書き込む一文の雛形）
SLOT_LABEL = {
    "lyrics":      "歌詞の構造（テーマ・起伏・韻・音節数・トーン）",
    "worldview":   "世界観（舞台・内面・時代感・背景）",
    "instruments": "楽器編成と音響（楽器リスト・環境音・エフェクト・バランス）",
    "performance": "演奏のクセ（音符の置き方・奏法）",
    "structure":   "構成（イントロ / アウトロ / サビの設計）",
    "harmony":     "和声と転調（転調の数・傾向・コード感）",
    "groove":      "グルーヴとテンポ（ノリ・BPM・微妙なタイミング）",
    "phrase":      "フレーズの骨格（リズムと形。変形ルール付き）",
    "vocal_main":  "ボーカルの表現（6 割）",
    "vocal_sub1":  "ボーカルの表現（2 割）",
    "vocal_sub2":  "ボーカルの表現（2 割）",
}


@dataclass
class Reference:
    """参考曲 1 曲ぶん（解析シートから選択に必要な部分だけ抜き出したもの）"""
    id: str
    title: str
    artist_name: str
    tags: set[str]
    bpm: float | None
    vocal_sex: str | None      # female / male / mixed / none
    vocal_range: str | None    # low / mid / high
    is_trend: bool = False     # 今週のトレンド曲か
    weight: float = 1.0        # 成績から来る重み（1.0〜3.0）。ルール 5
    usable_slots: set[str] | None = None   # 音源なしの解析シート（話題曲の Web 調査だけ）は、使える枠が限られる
    trend_week: str | None = None          # 話題曲として取り込んだ週
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def load_artist(slug: str) -> dict:
    path = ARTISTS_DIR / f"{slug}.json"
    if not path.exists():
        sys.exit(
            f"[エラー] アーティスト設定書が見つかりません: {path}\n"
            f"        templates/artists/_template.json をコピーして {slug}.json を作ってください。"
        )
    sheet = json.loads(path.read_text(encoding="utf-8"))
    # axis（軸）は 2026-10-09 に廃止したので、必須にはしない（古い設定書に残っていても無視する）
    missing = [k for k in ("slug", "name", "formation", "vocal", "sound", "lyrics") if not sheet.get(k)]
    if missing:
        sys.exit(f"[エラー] {path.name} に未記入の項目があります: {', '.join(missing)}")
    return sheet


def list_artist_slugs() -> list[str]:
    """templates/artists/ にある設定書（_template.json 以外）の slug を返す"""
    return sorted(p.stem for p in ARTISTS_DIR.glob("*.json") if not p.name.startswith("_"))


def load_label(slug: str) -> dict:
    """子レーベルの設定（templates/labels/<slug>.json）。代表アーティストの設定書を内包している"""
    path = LABELS_DIR / f"{slug}.json"
    if not path.exists():
        sys.exit(f"[エラー] 子レーベルの設定が見つかりません: {path}\n        templates/labels/_template.json をコピーして作ってください。")
    label = json.loads(path.read_text(encoding="utf-8"))
    if not label.get("artists"):
        sys.exit(f"[エラー] {path.name} に artists（代表アーティスト）がありません")
    # 子レーベルの共通仕様（場面の BPM など）を、各組の設定に不足分だけ補う
    spec = label.get("scene_spec", {})
    for a in label["artists"]:
        a.setdefault("sound", {})
        a["sound"].setdefault("bpm_min", spec.get("bpm_min"))
        a["sound"].setdefault("bpm_max", spec.get("bpm_max"))
        a["label_slug"] = label["slug"]
        a.setdefault("release_weekday", label.get("release_weekday", "wednesday"))
        a.setdefault("release_hour_et", label.get("release_hour_et", 17))
    print(f"  子レーベル「{label['slug']}」（場面：{label.get('scene','')}）の {len(label['artists'])} 組を読み込みました"
          f"（配信 {label.get('release_weekday', 'wednesday')} {label.get('release_hour_et', 17)}:00 ET）")
    return label


def demo_artist() -> dict:
    """動作確認用の架空のアーティスト。実際の 5 組の設定はオーナーが決める。"""
    return {
        "slug": "demo",
        "axis": "time",
        "name": "DEMO ARTIST（架空）",
        "formation": "solo",
        "vocal": {
            "sex": "male", "range": "low", "fixed_timbre": True,
            "voice_spec": {"comfortable_low": "G2", "comfortable_high": "D4", "falsetto_switch_point": "E4",
                           "falsetto_quality": "息多め", "strength": "低音の響き", "weakness": "高音を張ると硬くなる"},
            "signature_techniques": {"primary": "語尾のフォール（下げて落とす）", "secondary": ["囁くようなウィスパー", "ハミング"],
                                     "avoid": ["シャウト／ラウド", "ベルティング"], "wordless_style": "ハミング",
                                     "tics": [{"technique": "ハミング", "where": "アウトロ", "frequency": "every_song"},
                                              {"technique": "囁き", "where": "サビ前の 1 拍", "frequency": "most"}]},
            "melodic_tendency": {"motion": "順次進行が多い", "chorus_peak": "切り替え点のすぐ下で地声", "verse_register": "地声の低音で語る"},
        },
        "sound": {"bpm_min": 95, "bpm_max": 105, "dna_tags": ["night", "retro", "synth", "drive"], "forbidden": ["急な無音"]},
        "lyrics": {"primary_language": "en", "trend_language_ok": False},
        "persona": {"origin": {"landscape_words": ["港の湿った空気", "街灯の反復"]},
                    "influences": [{"name": "（bio のみ）", "era": "80s", "genre": "シンセポップ", "what_was_taken": "ゲートリバーブの空間感"}]},
        "composition_habits": {"modulation": "ラスサビで半音上げ", "intro": "4 小節以内に歌", "phrase_endings": "下げて落とす"},
    }


def load_references_from_dir(folder: Path) -> list[Reference]:
    """解析シート（analysis_sheet.schema.json 形式）の JSON をフォルダから読む"""
    refs: list[Reference] = []
    for p in sorted(folder.glob("*.json")):
        if p.name.endswith(".measure.json"):   # analyze_track.py の計測値（解析シートではない）
            continue
        sheet = json.loads(p.read_text(encoding="utf-8"))
        r = sheet.get("reference", {})
        v = sheet.get("vocal", {})
        g = sheet.get("groove", {})
        refs.append(Reference(
            id=sheet.get("id") or p.stem,
            title=r.get("title", p.stem),
            artist_name=r.get("artist_name", "?"),
            tags=set(r.get("tags", [])),
            bpm=g.get("bpm"),
            vocal_sex=v.get("sex"),
            vocal_range=v.get("range"),
            is_trend=(r.get("source") == "trend"),
            weight=float(sheet.get("weight", 1.0)),
            usable_slots=set(sheet["usable_slots"]) if sheet.get("usable_slots") else None,
            trend_week=sheet.get("trend_week"),
            raw=sheet,
        ))
    print(f"  解析シートを {len(refs)} 件読み込みました（{folder}）")
    return refs


def demo_references(seed: int) -> list[Reference]:
    """架空の参考曲 15 曲。実在の曲ではない。動作確認用。"""
    rng = random.Random(seed)
    sexes = ["female", "male", "mixed", "none"]
    ranges = ["low", "mid", "high"]
    tag_pool = [
        "morning", "night", "day", "rain", "coast", "highway", "drive",
        "synth", "retro", "acoustic", "band", "groove", "house", "lofi",
        "bright", "warm", "nostalgia", "texture", "uplift", "funk",
    ]
    refs = []
    for i in range(1, 21):
        refs.append(Reference(
            id=f"demo-{i:02d}",
            title=f"架空の曲 {i:02d}",
            artist_name=f"架空アーティスト {chr(64 + i)}",
            tags=set(rng.sample(tag_pool, 4)) | {"drive"},
            bpm=rng.choice([92, 98, 102, 108, 112, 118, 122]),
            vocal_sex=rng.choice(sexes),
            vocal_range=rng.choice(ranges),
            is_trend=(i >= 18),            # 18〜20 は「今週のトレンド曲」扱い
        ))
    print(f"  デモ用の架空の参考曲を {len(refs)} 件用意しました（実在の曲ではありません）")
    return refs


# ---------------------------------------------------------------------------
# 選択ロジック
# ---------------------------------------------------------------------------

def vocal_compatible(ref: Reference, artist: dict, strict: bool = True) -> bool:
    """
    ボーカル枠の候補として、音域・性別がアーティスト設定と合うか。
    strict=True  : 性別と音域の両方が合う曲だけ
    strict=False : 性別だけ合えばよい（厳しい条件で候補が足りないときの緩和）
    """
    av = artist["vocal"]
    if av["sex"] == "none":
        # 歌なし（インスト主体）の組は声を素材として使うので、声があれば何でもよい
        return ref.vocal_sex not in (None, "none")
    if ref.vocal_sex in (None, "none"):
        return False
    if av["sex"] == "mixed":
        sex_ok = True                      # 男女デュオは男女どちらの参考も可
    else:
        sex_ok = ref.vocal_sex in (av["sex"], "mixed")
    if not strict:
        return sex_ok
    range_ok = (ref.vocal_range is None) or (av.get("range") is None) or (ref.vocal_range == av["range"])
    return sex_ok and range_ok


def dna_score(ref: Reference, artist: dict) -> float:
    """アーティストの DNA タグとの重なり（0〜1）。選択の重みに使う"""
    dna = set(artist["sound"].get("dna_tags", []))
    if not dna:
        return 0.5
    return len(ref.tags & dna) / len(dna)


def weighted_pick(rng: random.Random, candidates: list[Reference], artist: dict,
                  slot: str, slot_weights: dict[str, float]) -> Reference:
    """DNA の重なり・成績の重み・トレンドかどうかを掛け合わせてランダムに 1 曲選ぶ"""
    weights = []
    for c in candidates:
        w = 0.2 + dna_score(c, artist)      # タグが全く重ならなくても 0 にはしない（多様性のため）
        # ルール 5：成績の重み（analyze_growth.py の weights.json「枠|参考曲ID」）。最大 3 倍まで
        w *= min(slot_weights.get(f"{slot}|{c.id}", c.weight), 3.0)
        w *= choice_boost(artist, c.title, c.artist_name, slot)   # その組が自分で選んだ参考曲（管理表の『参考曲の候補』）
        if c.is_trend:
            w *= 1.5                        # 今週のトレンド曲は少し優先
        weights.append(w)
    return rng.choices(candidates, weights=weights, k=1)[0]


def assign_slots(artist: dict, refs: list[Reference], rng: random.Random, trend_quota: int = 2,
                 slot_weights: dict[str, float] | None = None) -> dict[str, Reference]:
    """
    枠ごとに参考曲を 1 曲ずつ割り当てる。
    - 同じ参考曲は 2 枠に使わない（1 曲 1 枠）
    - ボーカル枠は音域・性別が合う曲だけ
    - トレンド曲を最低 trend_quota 曲は混ぜる（候補があれば）
    """
    used: set[str] = set()
    result: dict[str, Reference] = {}
    slot_weights = slot_weights or {}

    for slot in SLOT_ORDER:
        pool = [r for r in refs if r.id not in used and (not r.usable_slots or slot in r.usable_slots)]
        if slot in VOCAL_SLOTS:
            vocal_slots_left = sum(1 for s in VOCAL_SLOTS if s not in result)
            strict_pool = [r for r in pool if vocal_compatible(r, artist, strict=True)]
            if len(strict_pool) >= vocal_slots_left:
                pool = strict_pool
            else:
                # 音域まで合う曲が足りない → 性別だけ合う曲まで広げる
                pool = [r for r in pool if vocal_compatible(r, artist, strict=False)]
                print(f"    [注意] 音域まで合う参考曲が {len(strict_pool)} 件しかないため、枠「{slot}」は性別だけ合う曲から選びます")
        if not pool:
            raise RuntimeError(
                f"枠「{slot}」に使える参考曲がありません。"
                f"参考曲 DB を増やすか、アーティストの音域・性別の条件を見直してください。"
            )

        # トレンド曲の最低本数を確保する：まだ足りなければトレンド曲だけから選ぶ
        trend_used = sum(1 for r in result.values() if r.is_trend)
        remaining_slots = len(SLOT_ORDER) - len(result)
        trend_pool = [r for r in pool if r.is_trend]
        if trend_pool and (trend_quota - trend_used) >= remaining_slots:
            pool = trend_pool

        chosen = weighted_pick(rng, pool, artist, slot, slot_weights)
        result[slot] = chosen
        used.add(chosen.id)
        mark = "（トレンド）" if chosen.is_trend else ""
        print(f"    {slot:<12} ← {chosen.title}／{chosen.artist_name}{mark}")

    # 念のための自己点検（データベースの一意制約と同じ条件）
    ids = [r.id for r in result.values()]
    assert len(ids) == len(set(ids)), "内部エラー：同じ参考曲が 2 枠に入っています"
    return result


# ---------------------------------------------------------------------------
# 配信日時（2 週間ベルトコンベア）
# ---------------------------------------------------------------------------

WEEKDAY_OFFSET = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def release_at_for_production_week(week_monday: date, weekday: str = "wednesday", hour_et: int = 17) -> datetime:
    """制作週の月曜 → 2 週間後の週の配信曜日・時刻（米国東部時間）を UTC に変換して返す。
    本体は水曜 17:00。子レーベルは設定書の release_weekday / release_hour_et（例：集中は火曜 8:00）"""
    release_day = week_monday + timedelta(days=14 + WEEKDAY_OFFSET.get(str(weekday).lower(), 2))
    local = datetime.combine(release_day, time(int(hour_et), 0), tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(ZoneInfo("UTC"))


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------------------
# ブリーフの骨組みを作る
# ---------------------------------------------------------------------------

def artist_constraints(artist: dict) -> dict:
    """
    設定書から『固定の制約』を写し取る。参考曲より優先される。
    影響源は名前を落として era / genre / what_was_taken だけにする（Suno に実名を渡さない）。
    """
    persona = artist.get("persona", {})
    vocal = artist.get("vocal", {})
    sound = artist.get("sound", {})
    influence_traits = []
    for inf in persona.get("influences", []):
        parts = [inf.get("era"), inf.get("genre"), inf.get("what_was_taken")]
        text = " / ".join(p for p in parts if p)
        if text:
            influence_traits.append(text)
    return {
        "composition_habits": {k: v for k, v in artist.get("composition_habits", {}).items() if not k.startswith("_") and v},
        "voice_spec": {k: v for k, v in vocal.get("voice_spec", {}).items() if not k.startswith("_") and v},
        "signature_techniques": {k: v for k, v in vocal.get("signature_techniques", {}).items() if not k.startswith("_") and v},
        "melodic_tendency": {k: v for k, v in vocal.get("melodic_tendency", {}).items() if not k.startswith("_") and v},
        "landscape_words": persona.get("origin", {}).get("landscape_words", []),
        "influence_traits": influence_traits,
        "bpm_range": [sound.get("bpm_min"), sound.get("bpm_max")],
        "forbidden": sound.get("forbidden", []),
    }


def build_brief_skeleton(artist: dict, slots: dict[str, Reference], week_monday: date,
                         trend_language: str | None, hints: dict | None = None) -> dict:
    release_at = release_at_for_production_week(week_monday, artist.get("release_weekday", "wednesday"),
                                                 artist.get("release_hour_et", 17))
    return {
        "week_start": week_monday.isoformat(),
        "label_slug": artist.get("label_slug", "drive"),
        "artist_slug": artist["slug"],
        "featured_artist_slug": None,
        "release_at": release_at.isoformat().replace("+00:00", "Z"),
        "trend_language": trend_language if artist["lyrics"].get("trend_language_ok") else None,
        "slots": {
            slot: {
                "reference_id": ref.id,
                "title": ref.title,
                "artist_name": ref.artist_name,
                "weight": VOCAL_SLOTS.get(slot, 1.0),
                "borrowed_summary": SLOT_LABEL[slot],
            }
            for slot, ref in slots.items()
        },
        "vocal_blend": {
            "main_traits": [], "sub1_traits": [], "sub2_traits": [],
            "synthesized_instruction": "（Claude が vocal_main / sub1 / sub2 の解析シートから 6:2:2 で合成する）",
        },
        "phrase_transform": {
            "source_phrase_name": "",
            "kept": [],
            "changed": [],
            "contour_interval_changed": False,
            "description": "（Claude が変形案を 3 つ出し、人が 1 つ選ぶ。kept は最大 2 要素）",
        },
        "artist_constraints": artist_constraints(artist),
        "growth_hints": hints or {"artist_trend": "new", "top_slots": [], "notes": ["成績データなし"]},
        "title_candidates": [],
        "suno_style_prompt": "",
        "suno_lyrics": "",
        "cover_prompt_seed": "",
        "generated_by": "select_references.py",
    }


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="参考曲を枠ごとに自動で割り当てる（1 曲 1 枠）")
    ap.add_argument("--artist", help="アーティストの slug（templates/artists/<slug>.json）。--demo だけなら省略可")
    ap.add_argument("--all", action="store_true", help="templates/artists/ にある設定書すべてをまとめて実行する")
    ap.add_argument("--label", help="子レーベルの slug（templates/labels/<slug>.json）。その代表アーティスト全組ぶんを実行する")
    ap.add_argument("--references", type=Path, help="解析シート JSON が入ったフォルダ")
    ap.add_argument("--demo", action="store_true", help="架空の参考曲で動作確認する")
    ap.add_argument("--week", help="制作週の日付（YYYY-MM-DD）。省略時は今日の週")
    ap.add_argument("--trend-language", help="今週のトレンド言語（例：es）。質 だけが使う")
    ap.add_argument("--weights", type=Path, help="analyze_growth.py が出した weights.json（成績由来の重み）")
    ap.add_argument("--hints", type=Path, help="analyze_growth.py が出した hints.json（組ごとのヒント）")
    ap.add_argument("--seed", type=int, help="乱数の種（同じ結果を再現したいとき）")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "briefs", help="出力フォルダ")
    ap.add_argument("--force", action="store_true", help="デビュー前・隔週の休み・休止の組も作る")
    ap.add_argument("--overwrite", action="store_true",
                    help="Claude が書き足し済みのブリーフも作り直す（既定では守る）")
    args = ap.parse_args()

    if not args.artist and not args.all and not args.demo and not args.label:
        ap.error("--artist <slug> / --all / --label <slug> / --demo のどれかを指定してください")
    if not args.demo and not args.references:
        ap.error("--references <フォルダ> か --demo を指定してください")

    week_monday = monday_of(date.fromisoformat(args.week)) if args.week else monday_of(date.today())
    seed = args.seed if args.seed is not None else int(week_monday.strftime("%Y%m%d"))
    rng = random.Random(seed)

    print("=== 参考曲の割り当てを始めます ===")
    print(f"  制作週（月曜）  : {week_monday}")
    release_at = release_at_for_production_week(week_monday)
    print(f"  配信日時        : {release_at.astimezone(ZoneInfo('America/New_York')):%Y-%m-%d %H:%M %Z}"
          f"（日本時間 {release_at.astimezone(ZoneInfo('Asia/Tokyo')):%m/%d %H:%M}）")
    print(f"  乱数の種        : {seed}")

    slot_weights: dict[str, float] = {}
    hints_all: dict[str, dict] = {}
    # 今週の話題曲レポート（fetch_trends.py）があれば、トレンド言語をそこから読む
    trend_file = ROOT / "out" / "trends" / f"{week_monday}.json"
    if not args.trend_language and trend_file.exists():
        args.trend_language = json.loads(trend_file.read_text(encoding="utf-8")).get("trend_language")
        print(f"  今週のトレンド言語: {args.trend_language or 'なし'}（{trend_file.relative_to(ROOT)} から）")
    if args.weights and args.weights.exists():
        slot_weights = json.loads(args.weights.read_text(encoding="utf-8"))
        print(f"  成績由来の重みを {len(slot_weights)} 件読み込みました（{args.weights}）")
    if args.hints and args.hints.exists():
        hints_all = json.loads(args.hints.read_text(encoding="utf-8"))
        print(f"  組ごとのヒントを {len(hints_all)} 件読み込みました（{args.hints}）")

    refs = demo_references(seed) if args.demo else load_references_from_dir(args.references)
    # 話題曲は取り込んだ週から 2 週間だけ「トレンド」として優先する（それ以降は普通の参考曲）
    for r in refs:
        if r.is_trend and r.trend_week:
            age = (week_monday - date.fromisoformat(r.trend_week)).days
            if age > 14 or age < 0:
                r.is_trend = False
    if len(refs) < len(SLOT_ORDER):
        print(f"  [注意] 参考曲が {len(refs)} 件しかありません。11 枠を埋めるには 11 件以上必要です。")

    # 対象のアーティスト：--all ならフォルダ内の設定書すべて、--artist なら 1 組、
    # どちらも無く --demo だけなら架空のアーティスト 1 組
    label_artists: dict[str, dict] = {}
    if args.label:
        label = load_label(args.label)
        label_artists = {a["slug"]: a for a in label["artists"]}
        slugs = list(label_artists)
    elif args.all:
        slugs = list_artist_slugs()
        if not slugs:
            print("  [注意] templates/artists/ に設定書がありません。_template.json をコピーして作ってください。")
            if args.demo:
                print("  → 代わりに架空のアーティストで動作確認します。")
                slugs = ["demo"]
    elif args.artist:
        slugs = [args.artist]
    else:
        slugs = ["demo"]
    args.out.mkdir(parents=True, exist_ok=True)

    failed: list[str] = []
    for slug in slugs:
        if slug in label_artists:
            artist = label_artists[slug]
        else:
            artist = demo_artist() if slug == "demo" else load_artist(slug)
        print(f"\n--- {slug}（{artist['name']}）---")
        if slug != "demo" and not args.force:
            go, why = production_status(artist, week_monday)
            if not go:
                print(f"  今週は作りません：{why}")
                continue
        out_path = args.out / f"{week_monday}_{slug}.json"
        if out_path.exists() and not args.overwrite:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            if old.get("suno_style_prompt") or old.get("title_candidates"):
                print(f"  既に Claude が書き足したブリーフがあるので守ります（作り直すときは --overwrite）: {out_path.relative_to(ROOT)}")
                continue
        try:
            # 成長分析のヒント：横ばい（flat）や方針転換レベル 1 の組は、トレンド曲の枠を 2 → 3 に増やす
            h = hints_all.get(slug) or {}
            quota = 3 if h.get("artist_trend") == "flat" or (h.get("pivot") or {}).get("level") == 1 else 2
            slots = assign_slots(artist, refs, rng, trend_quota=quota, slot_weights=slot_weights)
        except RuntimeError as e:
            print(f"  [失敗] {e}")
            failed.append(slug)
            continue
        brief = build_brief_skeleton(artist, slots, week_monday, args.trend_language, hints_all.get(slug))
        brief["references_dir"] = str(args.references.resolve()) if args.references else None   # write_brief.py が解析シートを探す場所
        moment = pick_moment(artist, week_monday, args.out)   # 今週の歌のもとになる瞬間（近況 → 歌の種）
        if moment:
            brief["life_moment"] = moment
            print(f"    今週の歌のもと：{moment['kind']}「{(moment.get('event') or moment.get('idea') or '')[:40]}」")
        c = brief["artist_constraints"]
        print(f"    固定の制約: 作曲の癖 {len(c['composition_habits'])} 項目 / 声の仕様 {len(c['voice_spec'])} 項目 / 得意な歌い方 {len(c['signature_techniques'])} 項目")
        out_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → ブリーフの骨組みを書き出しました: {out_path.relative_to(ROOT)}")

    if failed:
        print(f"\n=== 一部失敗：{', '.join(failed)}。参考曲 DB を増やしてから再実行してください ===")
        sys.exit(1)
    print("\n=== 完了。次は Claude がこの骨組みに Suno 用の指示文・歌詞案・タイトル案を書き足します ===")


if __name__ == "__main__":
    main()

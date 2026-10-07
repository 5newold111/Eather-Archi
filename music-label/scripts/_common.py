"""
新しいスクリプト（select_takes / master_track / distrokid_sheet / post_social / supabase_sync）で共通に使う小道具。

- .env の読み込み（鍵はここからだけ読む。画面やログには出さない）
- 設定書（本体レーベル / 子レーベル）の読み込み
- HTTPS 用の証明書設定（Mac の python.org 版 Python 対策）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
MAIN_LABEL = "drive"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """music-label/.env があれば読み込んで環境変数にする（既に設定済みのものは上書きしない）"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ssl_context():
    import ssl
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_label(slug: str | None) -> dict:
    """子レーベルの設定書。本体（drive）は設定書ファイルが無いので既定値を返す"""
    if not slug or slug == MAIN_LABEL:
        return {"slug": MAIN_LABEL, "name": "EtherArchi", "automation_tier": "A",
                "release_hour_et": 17, "release_weekday": "wednesday", "accounts": {}}
    p = ROOT / "templates" / "labels" / f"{slug}.json"
    if not p.exists():
        sys.exit(f"[エラー] レーベルの設定書がありません: {p}")
    return read_json(p)


def load_artist(slug: str, label: str | None = None) -> dict:
    """アーティスト設定書を探す。label が無ければ本体 → 子レーベルの順に探す"""
    if not label or label == MAIN_LABEL:
        p = ROOT / "templates" / "artists" / f"{slug}.json"
        if p.exists():
            a = read_json(p)
            a["label_slug"] = MAIN_LABEL
            return a
    labels = [label] if label and label != MAIN_LABEL else [
        p.stem for p in sorted((ROOT / "templates" / "labels").glob("*.json")) if not p.stem.startswith("_")]
    for lab in labels:
        d = load_label(lab)
        for a in d.get("artists", []):
            if a.get("slug") == slug:
                a["label_slug"] = lab
                return a
    sys.exit(f"[エラー] アーティスト {slug} の設定書が見つかりません（templates/artists か templates/labels を確認）")


def all_artists() -> list[dict]:
    """本体 5 組 + 子レーベルの全アーティスト"""
    out = []
    for p in sorted((ROOT / "templates" / "artists").glob("*.json")):
        if p.stem.startswith("_"):
            continue
        a = read_json(p)
        a["label_slug"] = MAIN_LABEL
        out.append(a)
    for p in sorted((ROOT / "templates" / "labels").glob("*.json")):
        if p.stem.startswith("_"):
            continue
        for a in read_json(p).get("artists", []):
            a["label_slug"] = p.stem
            out.append(a)
    return out


def length_spec(artist: dict, label: dict) -> dict:
    """曲の尺・サビ位置などの仕様。本体は drive_spec、子レーベルは scene_spec"""
    spec = dict(label.get("scene_spec") or {})
    spec.update((artist.get("sound") or {}).get("drive_spec") or {})
    spec.setdefault("length_sec_min", 150)
    spec.setdefault("length_sec_max", 210)
    spec.setdefault("hook_within_sec", 30)
    spec.setdefault("no_sudden_silence", True)
    return spec


def step(msg: str) -> None:
    """いま何をしているかを日本語で表示する"""
    print(f"▶ {msg}", flush=True)


# ---------------------------------------------------------------------------
# 設定書の書き戻し（debut / expand_label / apply_pivot / plan_collabs が使う）
# ---------------------------------------------------------------------------
TRANSIENT_KEYS = ("label_slug", "label_visual")   # 読み込み時に足した一時的な項目。保存しない


def save_artist(artist: dict) -> Path:
    """設定書を元のファイルに書き戻す。本体は templates/artists/<slug>.json、子レーベルはレーベルの設定書の中"""
    a = {k: v for k, v in artist.items() if k not in TRANSIENT_KEYS}
    label = artist.get("label_slug") or MAIN_LABEL
    if label == MAIN_LABEL:
        p = ROOT / "templates" / "artists" / f"{a['slug']}.json"
        write_json(p, a)
        return p
    p = ROOT / "templates" / "labels" / f"{label}.json"
    lab = read_json(p)
    for i, x in enumerate(lab.get("artists", [])):
        if x.get("slug") == a["slug"]:
            lab["artists"][i] = a
            break
    else:
        lab.setdefault("artists", []).append(a)
    write_json(p, lab)
    return p


def save_label(label: dict) -> Path:
    p = ROOT / "templates" / "labels" / f"{label['slug']}.json"
    write_json(p, label)
    return p


# ---------------------------------------------------------------------------
# 週と配信日時
# ---------------------------------------------------------------------------
LEAD_WEEKS = 2
WEEKDAY_OFFSET = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def monday_of(d):
    from datetime import timedelta
    return d - timedelta(days=d.weekday())


def as_date(v):
    from datetime import date
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def release_schedule(artist: dict, label: dict) -> tuple[str, int]:
    """配信の曜日と時刻（ET）。本体は水曜 17:00、子レーベルは設定書の値"""
    wd = artist.get("release_weekday") or label.get("release_weekday") or "wednesday"
    hr = artist.get("release_hour_et") or label.get("release_hour_et") or 17
    return str(wd).lower(), int(hr)


def release_at_utc(production_monday, weekday: str = "wednesday", hour_et: int = 17):
    """制作週の月曜 → 2 週間後の週の配信曜日・時刻（ET）を UTC で返す"""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo
    day = production_monday + timedelta(days=7 * LEAD_WEEKS + WEEKDAY_OFFSET.get(weekday, 2))
    return datetime.combine(day, time(hour_et, 0), tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("UTC"))


def production_status(artist: dict, production_monday) -> tuple[bool, str]:
    """
    その制作週にこの組の曲を作るか。
      debut_week はデビュー曲が配信される週の月曜。制作はその 2 週間前から始まる
      cadence=biweekly の組は、デビュー週から数えて偶数週だけ作る
      cadence=paused の組は作らない
    戻り値：(作るか, 理由)
    """
    from datetime import timedelta
    debut = as_date(artist.get("debut_week"))
    if artist.get("cadence") == "paused":
        return False, "休止中（cadence=paused）"
    if debut is None:
        return False, "デビュー週が未設定（debut.py で設定すると制作が始まる。試すだけなら --force）"
    first_production = monday_of(debut) - timedelta(weeks=LEAD_WEEKS)
    if production_monday < first_production:
        return False, f"デビュー前（制作開始は {first_production}）"
    if artist.get("cadence") == "biweekly":
        n = (production_monday - first_production).days // 7
        if n % 2:
            return False, "隔週の休みの週"
    return True, "制作する週"

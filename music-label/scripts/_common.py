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

"""
新しいスクリプト（select_takes / master_track / distrokid_sheet / post_social / supabase_sync）で共通に使う小道具。

- .env の読み込み（鍵はここからだけ読む。画面やログには出さない）
- 設定書（Postdog Records / 子レーベル）の読み込み
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
MAIN_LABEL_NAME = "Postdog Records"   # メインのレーベル（slug は drive）の公開名。配信のレーベル欄・プロフィールに出る


def dotenv_values(path: Path = ROOT / ".env") -> dict[str, str]:
    """music-label/.env の中身（いまのファイルの値）"""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def protect_env(path: Path = ROOT / ".env") -> None:
    """鍵の入った .env を、この Mac の自分だけが読める権限（600）にそろえる。
    cp で作った .env や、スクリプトが新しく作った .env はほかの利用者にも読める権限（644）になるため。
    一度 600 にすれば、あとで書き直しても権限は保たれる"""
    try:
        if path.exists() and path.stat().st_mode & 0o077:
            path.chmod(0o600)
    except OSError:
        pass


def load_dotenv(path: Path = ROOT / ".env", override: bool = False) -> None:
    """music-label/.env があれば読み込んで環境変数にする（ふだんは既に設定済みのものは上書きしない。
    override=True：ファイルの今の値で上書きする。動かしたままのダッシュボードが、あとで直した .env を使うため）"""
    protect_env(path)
    for k, v in dotenv_values(path).items():
        if override:
            os.environ[k] = v
        else:
            os.environ.setdefault(k, v)


def ssl_context():
    import ssl
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_PY_WITH: dict[str, str | None] = {}


def python_with(module: str = "playwright") -> str | None:
    """その部品（例 playwright）が入っている Python を探す（決定 59）。
    Python の版が上がる（3.13 → 3.14 など）と、版ごとの置き場所に入れた部品が新しい版から見えなくなるため、
    いま動いている Python → よく使われる場所の Python（Homebrew・python.org 版・版ごとの python3.x）の順に試す"""
    import glob
    import shutil
    import subprocess
    if module in _PY_WITH:
        return _PY_WITH[module]
    cands = [sys.executable, shutil.which("python3") or ""]
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/Library/Frameworks/Python.framework/Versions/Current/bin", "/usr/bin"):
        cands.append(f"{d}/python3")
        cands += sorted(glob.glob(f"{d}/python3.[0-9]*"), reverse=True)
    cands += sorted(glob.glob("/Library/Frameworks/Python.framework/Versions/3.*/bin/python3"), reverse=True)
    cands += sorted(glob.glob(str(Path.home() / ".pyenv/versions/*/bin/python3")), reverse=True)
    seen = set()
    for c in cands:
        if not c or c in seen or not Path(c).exists() or c.endswith("-config"):
            continue
        seen.add(c)
        try:
            ok = subprocess.run([c, "-c", f"import {module}, sys; assert sys.version_info >= (3, 10)"],
                                capture_output=True, timeout=20).returncode == 0
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            _PY_WITH[module] = c
            return c
    _PY_WITH[module] = None
    return None


def rel(p: Path) -> str:
    """表示用の場所。music-label の中なら短く、外（一時フォルダなど）ならそのまま"""
    p = Path(p).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def label_slug_of(value) -> str:
    """管理表の『レーベル』欄の書き方（Postdog Records・旧表記の「本体」・子レーベルの slug や名前）を slug にそろえる"""
    v = str(value or "").strip()
    if not v or v in ("本体", MAIN_LABEL) or v.casefold() == MAIN_LABEL_NAME.casefold():
        return MAIN_LABEL
    for p in sorted((ROOT / "templates" / "labels").glob("*.json")):
        if p.stem.startswith("_"):
            continue
        if v.casefold() in (p.stem.casefold(), str(read_json(p).get("name", "")).casefold()):
            return p.stem
    return v


# Postdog Records の組が聴かれる場面（決定 29。2026-10-22〜 追加でデビューする組）。
# 最初の 5 組はドライブ（設定書の drive_spec）のまま。新しい組は「主な場面」を 1 つ選び、その仕様（scene_spec）で作る
SCENE_TYPES = {
    "live": {"name": "ライブハウス", "en": "live houses and small venues (band energy, sing-along choruses)",
             "words": ["ライブ", "live", "ステージ", "会場", "フェス", "バンドの音", "小箱"],
             "spec": {"hook_within_sec": 45, "length_sec_min": 180, "length_sec_max": 270, "no_sudden_silence": False,
                      "notes": "生演奏で再現できる編成。サビは観客が一緒に歌える短い言葉。キメやブレイク（一瞬の無音）を使ってよい。ライブで伸ばせるイントロ・アウトロ"}},
    "party": {"name": "パーティ・クラブ", "en": "parties, clubs and pre-game playlists (danceable, steady groove)",
              "words": ["パーティ", "パーティー", "party", "クラブ", "club", "dj", "踊", "ダンスフロア", "宴", "フロア"],
              "spec": {"hook_within_sec": 30, "length_sec_min": 180, "length_sec_max": 300, "no_sudden_silence": False,
                       "notes": "DJ がつなぎやすい 8〜16 小節のイントロ・アウトロ。テンポは一定。ブレイクからの盛り上げ（ドロップ）を使ってよい。踊れるグルーヴ"}},
    "personal": {"name": "ひとりで聴く", "en": "personal listening (headphones, alone in a room, late nights, walks)",
                 "words": ["ひとり", "一人", "ヘッドホン", "イヤホン", "部屋", "夜更かし", "散歩", "寝る前", "personal", "headphone"],
                 "spec": {"hook_within_sec": 45, "length_sec_min": 150, "length_sec_max": 270, "no_sudden_silence": False,
                          "notes": "ヘッドホンで気づく細部（息・部屋の響き・小さな音）。音量の起伏や静かな間を使ってよい。歌詞は 1 人に語りかける距離"}},
    "drive": {"name": "ドライブ", "en": "driving (morning highway, night drive, long distance)",
              "words": ["ドライブ", "運転", "車", "高速", "環状線", "海岸線", "drive", "highway"],
              "spec": {"hook_within_sec": 30, "length_sec_min": 150, "length_sec_max": 210, "no_sudden_silence": True,
                       "notes": "車内の騒音でも聴き取れる中音域。急な無音・大きな音量差を作らない"}},
}
MAIN_SCENE = ("Postdog Records の組は、幅広い場面で聴かれる：ライブハウス・パーティやクラブ・ひとりで聴く（ヘッドホン・部屋・散歩）・ドライブ。"
              "新しい組は scene_types から主な場面を 1 つと、近い場面を 1〜2 つ選ぶ。最初の 5 組はドライブなので、それ以外を優先する")


def scene_type_of(text: str, default: str = "personal") -> str:
    """「聴かれる場面」の文から、主な場面の種類（live / party / personal / drive）。いちばん先に出てくる言葉で決める"""
    t = str(text or "").casefold()
    best, pos = default, None
    for k, v in SCENE_TYPES.items():
        for w in v["words"]:
            i = t.find(w.casefold())
            if i >= 0 and (pos is None or i < pos):
                best, pos = k, i
    return best


SCENE_PLAN = ROOT / "templates" / "scene_plan.json"


def scene_weights(artist: dict) -> dict[str, float]:
    """定期配信で、この組の曲がどの場面で聴かれるかの割合（Postdog Records の組だけ。子レーベルは空）。決定 30"""
    if label_slug_of(artist.get("label_slug")) not in (MAIN_LABEL, ""):
        return {}
    plan = read_json(SCENE_PLAN) if SCENE_PLAN.exists() else {}
    w = {k: float(v) for k, v in (plan.get(artist.get("slug", "")) or {}).items() if k in SCENE_TYPES and float(v) > 0}
    if w:
        return w
    main = (artist.get("persona") or {}).get("scene_type") or ("drive" if (artist.get("sound") or {}).get("drive_spec") else
                                                               scene_type_of((artist.get("persona") or {}).get("scene", "")))
    rest = [k for k in SCENE_TYPES if k != main]
    return {main: 0.55, **{k: 0.45 / len(rest) for k in rest}}


SCENE_GROWTH = OUT / "growth" / "scene_weights.json"   # analyze_growth.py：場面ごとの伸び（決定 31）


def scene_growth(slug: str) -> dict:
    """この組の場面ごとの伸び {weights: {場面: 倍率}, hits: {場面: [よく伸びた曲]}, stats}。まだ無ければ空"""
    try:
        return (read_json(SCENE_GROWTH) if SCENE_GROWTH.exists() else {}).get(slug) or {}
    except (OSError, ValueError):
        return {}


def pick_scene(artist: dict, week_monday, last: str | None = None) -> dict | None:
    """今週の曲の聴かれる場面を、割合のくじで選ぶ（週と組で決まるので、やり直しても同じ）。
    割合＝scene_plan.json × 場面ごとの伸びの倍率（よく伸びた場面ほど厚く。決定 31）。
    前の週と同じ場面は 3 分の 1 に下げる（よく伸びている場面＝倍率 1.5 以上は 3 分の 2 まで。続けて厚くしてよい）"""
    import random
    w = scene_weights(artist)
    if not w:
        return None
    g = scene_growth(artist.get("slug", "")).get("weights") or {}
    w = {k: v * float(g.get(k, 1.0)) for k, v in w.items()}
    if last in w and len(w) > 1:
        w = {**w, last: w[last] * (2 / 3 if float(g.get(last, 1.0)) >= 1.5 else 1 / 3)}
    rng = random.Random(f"{week_monday}|{artist.get('slug')}|scene")
    k = rng.choices(list(w), weights=list(w.values()), k=1)[0]
    t = SCENE_TYPES[k]
    out = {"type": k, "name": t["name"], "en": t["en"], **t["spec"]}
    if k in g:
        out["growth_multiplier"] = g[k]
    return out


def scene_of_type(artist: dict, k: str) -> dict | None:
    """場面の種類を決めて、その場面の情報を返す（集中配信で曲ごとに場面を散らすとき。決定 53）"""
    if k not in SCENE_TYPES:
        return None
    t = SCENE_TYPES[k]
    out = {"type": k, "name": t["name"], "en": t["en"], **t["spec"]}
    g = scene_growth(artist.get("slug", "")).get("weights") or {}
    if k in g:
        out["growth_multiplier"] = g[k]
    return out


def load_label(slug: str | None) -> dict:
    """子レーベルの設定書。Postdog Records（drive）は設定書ファイルが無いので既定値を返す"""
    if not slug or slug == MAIN_LABEL:
        return {"slug": MAIN_LABEL, "name": MAIN_LABEL_NAME, "automation_tier": "A",
                "release_hour_et": 17, "release_weekday": "wednesday", "accounts": {},
                "scene": MAIN_SCENE,
                "scene_types": {k: {"name": v["name"], "en": v["en"], **v["spec"]} for k, v in SCENE_TYPES.items()}}
    p = ROOT / "templates" / "labels" / f"{slug}.json"
    if not p.exists():
        sys.exit(f"[エラー] レーベルの設定書がありません: {p}")
    return read_json(p)


def load_artist(slug: str, label: str | None = None, missing_ok: bool = False) -> dict | None:
    """アーティスト設定書を探す。label が無ければPostdog Records → 子レーベルの順に探す。
    missing_ok なら、見つからないとき（保管庫へ移した組の古い記録など）に None を返す"""
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
    if missing_ok:
        return None
    sys.exit(f"[エラー] アーティスト {slug} の設定書が見つかりません（templates/artists か templates/labels を確認）")


def all_artists() -> list[dict]:
    """Postdog Records の 5 組 + 子レーベルの全アーティスト"""
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
    """曲の尺・サビ位置などの仕様。子レーベルは scene_spec、Postdog Records の最初の 5 組は drive_spec、
    追加の組は設定書の sound.scene_spec（主な場面の仕様。決定 29）"""
    spec = dict(label.get("scene_spec") or {})
    spec.update((artist.get("sound") or {}).get("drive_spec") or {})
    spec.update((artist.get("sound") or {}).get("scene_spec") or {})
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
    """設定書を元のファイルに書き戻す。Postdog Records は templates/artists/<slug>.json、子レーベルはレーベルの設定書の中"""
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
    """配信の曜日と時刻（ET）。Postdog Records は水曜 17:00、子レーベルは設定書の値"""
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
      デビュー曲（debut_kit の Suno の曲）がある組は、それを DistroKid に登録するまで毎週の曲を作らない。
      登録すると publish.py が debut_week をその配信週に書き換え、そこから数えて毎週の制作が始まる（決定 17）
    戻り値：(作るか, 理由)
    """
    from datetime import timedelta
    debut = as_date(artist.get("debut_week"))
    if artist.get("cadence") == "paused":
        return False, "休止中（cadence=paused）"
    import _songs
    if _songs.debut_registered(artist.get("slug", "")) is False:
        return False, "デビュー曲の登録待ち（Suno の曲を DistroKid に登録すると、その配信週から毎週の制作が始まる）"
    if debut is None:
        return False, "デビュー週が未設定（debut.py で設定すると制作が始まる。試すだけなら --force）"
    first_production = monday_of(debut) - timedelta(weeks=LEAD_WEEKS)
    if production_monday < first_production:
        return False, f"デビュー前（制作開始は {first_production}）"
    if artist.get("cadence") == "biweekly":
        n = (production_monday - first_production).days // 7
        if n % 2:
            return False, "隔週の休みの週"
    try:   # 毎日配信の期間に配信日が入る週は、daily.py が作る（決定 94）
        import daily
        rel = release_at_utc(production_monday, *release_schedule(artist, load_label(artist.get("label_slug")))).date()
        if daily.in_rotation(artist.get("slug", ""), rel):
            return False, "毎日配信の期間（その組の曲は daily.py が日ごとに作る）"
    except ImportError:
        pass
    return True, "制作する週"


# ---------------------------------------------------------------------------
# デビュー週：毎月 1 日のある週（月曜始まり）
#   例）2026 年 12 月 → 12/1（火）のある週 ＝ 11/30 の週。Postdog Records の配信は水曜 12/2 17:00 ET
#   1 日が日曜の月は、その前の月曜からの週になる（例 2026 年 11 月 → 10/26 の週、配信は 10/28）
# ---------------------------------------------------------------------------
def debut_week_of_month(year: int, month: int):
    from datetime import date
    return monday_of(date(year, month, 1))


def next_debut_week(not_before):
    """not_before 以降で最初の「1 日のある週」の月曜"""
    y, m = not_before.year, not_before.month
    for _ in range(24):
        w = debut_week_of_month(y, m)
        if w >= not_before:
            return w
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise RuntimeError("デビュー週が見つかりません")


def is_debut_week(monday) -> bool:
    from datetime import timedelta
    return any((monday + timedelta(days=i)).day == 1 for i in range(7))


def notify(title: str, message: str) -> None:
    """Mac の通知センターに知らせる（Mac 以外では何もしない）。定期実行で人の判断が要るときに使う"""
    import platform
    import shutil
    import subprocess
    if platform.system() != "Darwin" or not shutil.which("osascript"):
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                   capture_output=True, check=False)

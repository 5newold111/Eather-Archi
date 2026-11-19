#!/usr/bin/env python3
"""
いまの制作の状態を Notion に写す（段階 1：見るだけ。決定 167）

  Notion の「Postdog Records 管理」ページの下に、データベース（表）を 2 つ作って、毎回中身を新しくする。
    ・アーティスト … 組の名前・レーベル・状態（所属／外す手続き中／外した）・編成・デビュー週・
                      配信サービスのアーティストページ・写真（小さくしたもの）
    ・楽曲         … 楽曲管理シート（SSD の 01_管理表/楽曲管理.xlsx）と同じ中身に、いまの工程の担当と状況、
                      ジャケット（小さくしたもの）、R2 に預けた配信用の音源を聞けるリンク（7 日で切れる。毎回つなぎ直す）
  工程（1 企画 → 2 Suno で作曲 → … → 8 配信中）で並べたボードの見え方は、Notion で「ボード」を足して「工程」でまとめる。

  ここではまだ Notion 側の書き換えを読み込まない（Notion で書き換えても、次に写すときに Mac の値に戻る）。
  Notion で承認やジャケット選びをするのは段階 2、ほかの工程も Notion から進めるのは段階 3。

  ファイルの置き場所：音源などの大きいファイルは R2（backup_r2.py が毎晩上げる）。
                      Notion には小さくした画像だけ（無料プランは 1 ファイル 5 MB まで）。

使い方
  python3 scripts/notion_sync.py              # 写す（変わった行だけ書き直す）
  python3 scripts/notion_sync.py --dry-run    # 何を写すかだけ表示（Notion には触らない）
  python3 scripts/notion_sync.py --all        # 変わっていない行も全部書き直す
  python3 scripts/notion_sync.py --if-ready   # 鍵が無ければ何もしない（定期実行から。30 分ごと）

鍵（.env。setup_keys.py --only notion で入れる。チャットには貼らない）
  NOTION_TOKEN        … Notion のインテグレーション（内部）のシークレット（ntn_… / secret_…）
  NOTION_PARENT_PAGE  … 表を置くページの住所（そのページの「接続」にインテグレーションを追加しておく）

記録：out/notion/map.json（Notion の表・行の番号と、前に写した中身の印）。消すと次は表から作り直す。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, all_artists, load_dotenv, read_json, ssl_context, step, write_json  # noqa: E402

load_dotenv()
API = os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1")   # 試すときだけ手元の偽サーバーに向ける
VERSION_HDR = "2022-06-28"
DIR = OUT / "notion"
MAP = DIR / "map.json"
THUMBS = DIR / "thumbs"
SCHEMA = 1                 # 表の項目を足したら上げる（次に写すとき、Notion の表にも足す）
THUMB_PX = 1000            # Notion に上げる画像の大きさ（長い辺）
MAX_UPLOAD = 5 * 1024 * 1024
LINK_DAYS = 7              # R2 の音源のリンクの有効期限（R2 の上限が 7 日）
RELINK_DAYS = 2            # 残りがこれより短くなったらつなぎ直す
WAIT = 0.35                # Notion の速さの制限（1 秒に 3 回ほど）

PHASE_COLOR = {"1": "gray", "2": "orange", "3": "yellow", "4": "yellow", "5": "brown", "6": "blue", "7": "green", "8": "green"}
STATE_JA = {"retiring": "外す手続き中", "retired": "外した"}
FLOW_JA = {"debut": "デビュー", "regular": "定期配信", "irregular": "不定期配信"}


def _opts(names: list[str], colors: dict[str, str] | None = None) -> dict:
    return {"select": {"options": [{"name": n, **({"color": colors[n]} if colors and n in colors else {})} for n in names]}}


def artist_schema() -> dict:
    return {
        "名前": {"title": {}}, "slug": {"rich_text": {}}, "レーベル": {"select": {}},
        "状態": _opts(["所属", "外す手続き中", "外した"], {"所属": "green", "外す手続き中": "yellow", "外した": "gray"}),
        "編成": {"select": {}}, "デビュー週": {"date": {}},
        "Spotify": {"url": {}}, "Apple Music": {"url": {}}, "YouTube Music": {"url": {}},
        "写真": {"files": {}}, "更新日": {"date": {}},
    }


def song_schema(artist_db: str) -> dict:
    from song_book import PHASES
    return {
        "曲名": {"title": {}}, "曲 ID": {"rich_text": {}}, "アーティスト": {"select": {}},
        "組のページ": {"relation": {"database_id": artist_db, "type": "dual_property", "dual_property": {}}},
        "流れ": _opts(list(FLOW_JA.values()), {"デビュー": "purple", "定期配信": "blue", "不定期配信": "pink"}),
        "工程": _opts(PHASES, {p: PHASE_COLOR[p[0]] for p in PHASES}),
        "次にやること": {"rich_text": {}},
        "担当": _opts(["あなた", "自動"], {"あなた": "red", "自動": "default"}),
        "いまの状況": {"rich_text": {}},
        "制作週": {"date": {}}, "配信予定日": {"date": {}},
        "BPM": {"number": {}}, "ジャンル": {"rich_text": {}}, "言語": {"select": {}}, "曲名の別案": {"rich_text": {}},
        "Suno の指示": {"rich_text": {}}, "Suno のモデル": {"select": {}},
        "歌詞": {"rich_text": {}}, "歌詞の日本語訳": {"rich_text": {}},
        "選んだテイク": {"rich_text": {}}, "保管場所（SSD）": {"rich_text": {}},
        "ISRC": {"rich_text": {}}, "UPC": {"rich_text": {}}, "配信 URL": {"url": {}},
        "音源（R2）": {"url": {}}, "ジャケット": {"files": {}}, "メモ": {"rich_text": {}}, "更新日": {"date": {}},
    }


# ---------------------------------------------------------------------------
# Notion とのやりとり
class NotionError(Exception):
    def __init__(self, code: int, body: dict | str):
        self.code, self.body = code, body
        msg = body.get("message", "") if isinstance(body, dict) else str(body)[:200]
        super().__init__(f"Notion {code}: {msg}")

    @property
    def gone(self) -> bool:
        """相手（表・行）が消された・ゴミ箱に入った"""
        msg = (self.body.get("message", "") if isinstance(self.body, dict) else str(self.body)).lower()
        return self.code == 404 or (self.code == 400 and "archived" in msg)


def token() -> str:
    return os.environ.get("NOTION_TOKEN", "").strip()


def parent_id(v: str | None = None) -> str:
    """ページの住所（https://www.notion.so/…-<32 桁>）か番号から、ページの番号だけを取り出す"""
    v = (v if v is not None else os.environ.get("NOTION_PARENT_PAGE", "")).strip()
    m = re.findall(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", v.split("?")[0])
    if not m:
        return ""
    h = m[-1].replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def call(method: str, path: str, body: dict | None = None, raw: bytes | None = None, ctype: str = "application/json",
         tok: str | None = None) -> dict:
    hdr = {"Authorization": f"Bearer {tok or token()}", "Notion-Version": VERSION_HDR}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        hdr["Content-Type"] = ctype
    for attempt in range(5):
        req = urllib.request.Request(API + path, data=data, method=method, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as r:
                time.sleep(WAIT)
                txt = r.read().decode("utf-8", "replace")
                return json.loads(txt) if txt else {}
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            try:
                b = json.loads(txt)
            except ValueError:
                b = txt
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:   # 混んでいる → 少し待ってやり直す
                time.sleep(float(e.headers.get("Retry-After") or 0) or 2 ** attempt)
                continue
            raise NotionError(e.code, b) from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise NotionError(0, str(e)) from None
    raise NotionError(0, "やり直しても届きませんでした")


def upload(path: Path) -> str | None:
    """画像を Notion に上げて、その番号を返す（1 時間以内に行に付けないと消える）"""
    data = path.read_bytes()
    if len(data) > MAX_UPLOAD:
        return None
    ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    fu = call("POST", "/file_uploads", {"mode": "single_part", "filename": path.name, "content_type": ctype})
    boundary = "----postdog" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    call("POST", f"/file_uploads/{fu['id']}/send", raw=body, ctype=f"multipart/form-data; boundary={boundary}")
    return fu["id"]


# ---------------------------------------------------------------------------
# 値の形
def _rt(s) -> list[dict]:
    s = "" if s is None else str(s)
    return [{"type": "text", "text": {"content": s[i:i + 2000]}} for i in range(0, min(len(s), 2000 * 100), 2000)]


def _sel(s) -> dict | None:
    s = str(s or "").replace(",", "・").strip()[:100]
    return {"name": s} if s else None


def _date(s) -> dict | None:
    s = str(s or "")[:10]
    try:
        date.fromisoformat(s)
    except ValueError:
        return None
    return {"start": s}


def _url(s) -> str | None:
    s = str(s or "").strip()
    return s if s.startswith(("http://", "https://")) else None


def _num(s) -> float | None:
    try:
        return float(s) if str(s).strip() != "" else None
    except ValueError:
        return None


def props(schema: dict, values: dict) -> dict:
    """{項目: 値} を Notion の形に（表に無い項目・ファイルの項目はここでは入れない）"""
    out = {}
    for k, v in values.items():
        kind = next(iter(schema[k]))
        if kind == "title":
            out[k] = {"title": _rt(v or "（曲名未定）")}
        elif kind == "rich_text":
            out[k] = {"rich_text": _rt(v)}
        elif kind == "select":
            out[k] = {"select": _sel(v)}
        elif kind == "date":
            out[k] = {"date": _date(v)}
        elif kind == "url":
            out[k] = {"url": _url(v)}
        elif kind == "number":
            out[k] = {"number": _num(v)}
        elif kind == "relation":
            out[k] = {"relation": [{"id": x} for x in (v or [])]}
    return out


def _sig(x) -> str:
    return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _file_sig(p: Path | None) -> str:
    if not p or not p.exists():
        return ""
    st = p.stat()
    return f"{p.name}:{st.st_size}:{int(st.st_mtime)}"


def thumb(src: Path, name: str) -> Path | None:
    """Notion に上げる小さい画像（長い辺 1000 px の JPEG）。作れなければ元の画像（5 MB を超えたら上げない）"""
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = THUMBS / f"{name}.jpg"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_PX, THUMB_PX))
            im.save(out, "JPEG", quality=85)
        return out
    except Exception:  # noqa: BLE001  Pillow が無いときは、Mac の sips で小さくする
        import shutil
        import subprocess
        if shutil.which("sips"):
            r = subprocess.run(["sips", "-Z", str(THUMB_PX), "-s", "format", "jpeg", str(src), "--out", str(out)],
                               capture_output=True, check=False)
            if r.returncode == 0 and out.exists():
                return out
    return src if src.stat().st_size <= MAX_UPLOAD else None


# ---------------------------------------------------------------------------
# R2 の音源のリンク（見るだけの、期限つきの住所）
def r2_link(rel: str, days: int = LINK_DAYS) -> tuple[str, str] | None:
    acc, ak, sk = (os.environ.get(k, "").strip() for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"))
    bucket = os.environ.get("R2_BUCKET", "").strip() or "label-backup"
    if not (acc and ak and sk):
        return None
    man = OUT / "backup" / "r2_manifest.json"
    if not man.exists() or rel not in read_json(man):   # まだ R2 に上がっていない
        return None
    now = datetime.now(timezone.utc)
    amz, ds = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    host = f"{acc}.r2.cloudflarestorage.com"
    path = urllib.parse.quote(f"/{bucket}/label/{rel}", safe="/-_.~")
    scope = f"{ds}/auto/s3/aws4_request"
    q = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{ak}/{scope}", "X-Amz-Date": amz,
         "X-Amz-Expires": str(days * 86400), "X-Amz-SignedHeaders": "host"}
    query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in sorted(q.items()))
    canonical = "\n".join(["GET", path, query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + sk).encode()
    for part in (ds, "auto", "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    until = (now + timedelta(days=days)).date().isoformat()
    return f"https://{host}{path}?{query}&X-Amz-Signature={sig}", until


# ---------------------------------------------------------------------------
# 写す中身
def artist_rows() -> list[dict]:
    import artist_photo
    import retire
    gone = {r["slug"]: r for r in retire.listing()}
    seen, rows = set(), []
    from _common import load_artist
    arts = all_artists(include_retiring=True) + [a for s in gone if (a := load_artist(s, missing_ok=True))]
    for a in arts:
        slug = a.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        r = gone.get(slug) or {}
        pages = (a.get("distribution") or {}).get("artist_pages") or {}
        try:
            photo = artist_photo.current_photo(slug)
        except Exception:  # noqa: BLE001
            photo = None
        rows.append({"key": f"artist:{slug}", "slug": slug, "photo": photo, "values": {
            "名前": a.get("name") or slug, "slug": slug, "レーベル": _label_name(a),
            "状態": STATE_JA.get(r.get("status", ""), "所属"), "編成": a.get("formation", ""),
            "デビュー週": a.get("debut_week", ""), "Spotify": pages.get("spotify", ""),
            "Apple Music": pages.get("apple_music", ""), "YouTube Music": pages.get("youtube_music", ""),
        }})
    return rows


def _label_name(a: dict) -> str:
    from _common import MAIN_LABEL, MAIN_LABEL_NAME, load_label
    slug = a.get("label_slug") or MAIN_LABEL
    if slug == MAIN_LABEL:
        return MAIN_LABEL_NAME
    try:
        return load_label(slug).get("name") or slug
    except Exception:  # noqa: BLE001
        return slug


def song_rows() -> list[dict]:
    import _songs
    import _timeline
    import song_book
    path, _ = song_book.target_path()
    old = song_book.read_existing(path) if path else {}
    rows = song_book.merge(song_book.debut_rows() + song_book.weekly_rows(), old)   # 楽曲管理シートと同じ中身（オーナーの欄も）
    _songs.freeze()
    try:
        tl = _timeline.timeline()
        flow, now = {}, {}
        for key in ("debut", "regular", "irregular"):
            for r in tl[key]["rows"]:
                if r.get("id") and not r.get("planned"):
                    flow[r["id"]] = FLOW_JA[key]
                    cur = next((p for p in r["phases"] if p["state"] in ("ng", "wait", "run")), None)
                    if cur:
                        now[r["id"]] = (cur.get("who", ""), f"{cur['name']}：{cur.get('detail', '')}" + ("（止まっています）" if cur["state"] == "ng" else ""))
        out = []
        for b in rows:
            sid = str(b.get("曲 ID") or "")
            if not sid:
                continue
            song = _songs.by_id(sid)
            slug = song["slug"] if song else ""
            who, what = now.get(sid, ("", ""))
            if not what and str(b.get("フェーズ", "")).startswith("8"):
                what = "配信中"
            cover = _songs.cover_source(song) if song else None
            out.append({"key": f"song:{sid}", "slug": slug, "cover": cover, "audio": (f"out/masters/{song['key']}/master.wav" if song else ""),
                        "values": {
                            "曲名": b.get("曲名", ""), "曲 ID": sid, "アーティスト": b.get("アーティスト", ""),
                            "流れ": flow.get(sid) or ("デビュー" if song and song["kind"] == "debut" else "定期配信"),
                            "工程": b.get("フェーズ", ""), "次にやること": b.get("次にやること", ""),
                            "担当": who if who in ("あなた", "自動") else "", "いまの状況": what,
                            "制作週": b.get("制作週", ""), "配信予定日": b.get("配信予定日", ""), "BPM": b.get("BPM", ""),
                            "ジャンル": b.get("ジャンル", ""), "言語": b.get("言語", ""), "曲名の別案": b.get("曲名の別案", ""),
                            "Suno の指示": b.get("Suno の指示（スタイル）", ""), "Suno のモデル": b.get("Suno のモデル", ""),
                            "歌詞": b.get("歌詞", ""), "歌詞の日本語訳": b.get("歌詞の日本語訳", ""),
                            "選んだテイク": b.get("選んだテイク", ""), "保管場所（SSD）": b.get("保管場所（SSD）", ""),
                            "ISRC": b.get("ISRC", ""), "UPC": b.get("UPC", ""), "配信 URL": b.get("配信 URL", ""), "メモ": b.get("メモ", ""),
                        }})
        return out
    finally:
        _songs.thaw()


# ---------------------------------------------------------------------------
def ensure_dbs(m: dict, parent: str) -> None:
    """表が無ければ作る。項目が増えていたら表に足す"""
    if m.get("parent") != parent:   # 置き場所のページを変えたら作り直す
        m.clear()
        m.update({"parent": parent, "db": {}, "pages": {}})
    db = m.setdefault("db", {})
    for name, title, schema in (("artists", "アーティスト", artist_schema), ("songs", "楽曲", None)):
        sch = schema() if schema else song_schema(db["artists"])
        if db.get(name):
            try:   # Notion で表を消した（ゴミ箱に入れた）なら作り直す
                info = call("GET", f"/databases/{db[name]}")
                alive = not (info.get("archived") or info.get("in_trash"))
            except NotionError as e:
                if not e.gone:
                    raise
                alive = False
            if alive:
                if m.get("schema", 0) < SCHEMA:   # 項目を足した版 → 表にも足す（題名とつながりの項目はそのまま）
                    call("PATCH", f"/databases/{db[name]}",
                         {"properties": {k: v for k, v in sch.items() if not ({"title", "relation"} & set(v))}})
                continue
            db.pop(name, None)
        step(f"Notion に表「{title}」を作っています")
        r = call("POST", "/databases", {"parent": {"type": "page_id", "page_id": parent}, "is_inline": True,
                                        "title": [{"type": "text", "text": {"content": title}}], "properties": sch})
        db[name] = r["id"]
        db[f"{name}_url"] = r.get("url", "")
        prefix = "artist:" if name == "artists" else "song:"   # 新しい表には行がまだ無い
        m["pages"] = {k: v for k, v in (m.get("pages") or {}).items() if not k.startswith(prefix)}
        if name == "artists":   # 楽曲の表は、アーティストの表を指すので作り直す
            db.pop("songs", None)
            m["pages"] = {}
    m["schema"] = SCHEMA


def upsert(m: dict, db_key: str, schema: dict, row: dict, extra: dict, file_prop: str, file_src: Path | None,
           force: bool, counts: dict) -> str | None:
    pages = m.setdefault("pages", {})
    rec = pages.get(row["key"]) or {}
    body = {**props(schema, row["values"]), **extra}
    sig, fsig = _sig(body), _file_sig(file_src)
    need_file = fsig != rec.get("file", "")
    if rec.get("id") and rec.get("sig") == sig and not need_file and not force:
        counts["same"] += 1
        return rec["id"]
    if need_file:
        if file_src:
            small = thumb(file_src, re.sub(r"[^0-9A-Za-z_.-]", "_", row["key"]))
            uid = upload(small) if small else None
            body[file_prop] = {"files": [{"type": "file_upload", "file_upload": {"id": uid}, "name": small.name}]} if uid else {"files": []}
        else:
            body[file_prop] = {"files": []}
    body["更新日"] = {"date": {"start": date.today().isoformat()}}
    for attempt in range(2):
        try:
            if rec.get("id"):
                r = call("PATCH", f"/pages/{rec['id']}", {"properties": body})
                counts["updated"] += 1
            else:
                r = call("POST", "/pages", {"parent": {"database_id": m["db"][db_key]}, "properties": body})
                counts["created"] += 1
            break
        except NotionError as e:
            if e.gone and rec.get("id") and attempt == 0:   # Notion で行を消した → 作り直す
                rec = {}
                continue
            raise
    pages[row["key"]] = {"id": r["id"], "sig": sig, "file": fsig}
    return r["id"]


def sync(dry: bool = False, force: bool = False) -> dict:
    parent = parent_id()
    arts, songs = artist_rows(), song_rows()
    step(f"Notion に写すもの：アーティスト {len(arts)} 組・楽曲 {len(songs)} 曲")
    if dry:
        for s in songs[:40]:
            v = s["values"]
            print(f"   {v['工程']:<16} {v['アーティスト']}「{v['曲名']}」 {v['いまの状況']}")
        return {"artists": len(arts), "songs": len(songs)}
    m = read_json(MAP) if MAP.exists() else {}
    ensure_dbs(m, parent)
    write_json(MAP, m)
    counts = {"created": 0, "updated": 0, "same": 0}
    a_sch, s_sch = artist_schema(), song_schema(m["db"]["artists"])
    ids = {}
    try:
        for i, a in enumerate(arts, 1):
            step(f"[{i}/{len(arts)}] アーティスト：{a['values']['名前']}")
            ids[a["slug"]] = upsert(m, "artists", a_sch, a, {}, "写真", a["photo"], force, counts)
        write_json(MAP, m)
        links = m.setdefault("links", {})
        for i, s in enumerate(songs, 1):
            step(f"[{i}/{len(songs)}] 楽曲：{s['values']['アーティスト']}「{s['values']['曲名']}」")
            extra = {"組のページ": {"relation": [{"id": ids[s["slug"]]}] if ids.get(s["slug"]) else []}}
            ln = links.get(s["key"]) or {}
            if not ln.get("until") or ln["until"] <= (date.today() + timedelta(days=RELINK_DAYS)).isoformat():
                got = r2_link(s["audio"]) if s["audio"] else None   # 期限が近いときだけつなぎ直す（毎回変えると毎回書き直しになる）
                ln = {"url": got[0], "until": got[1]} if got else {}
                links[s["key"]] = ln
            extra["音源（R2）"] = {"url": ln.get("url") or None}
            upsert(m, "songs", s_sch, s, extra, "ジャケット", s["cover"], force, counts)
            if i % 10 == 0:
                write_json(MAP, m)
    finally:
        m["last"] = {"at": datetime.now().isoformat(timespec="seconds"), **counts}
        write_json(MAP, m)
    step(f"Notion に写しました（新しく {counts['created']} 行・書き直し {counts['updated']} 行・変わらず {counts['same']} 行）")
    if m["db"].get("songs_url"):
        print(f"   楽曲の表：{m['db']['songs_url']}")
    return counts


def status() -> dict:
    """ダッシュボードに出す：鍵があるか・最後に写した時刻・表の住所"""
    m = read_json(MAP) if MAP.exists() else {}
    db = m.get("db") or {}
    return {"ready": bool(token() and parent_id()), "last": m.get("last") or {},
            "songs_url": db.get("songs_url", ""), "artists_url": db.get("artists_url", "")}


def main() -> None:
    ap = argparse.ArgumentParser(description="いまの制作の状態を Notion に写す（段階 1：見るだけ）")
    ap.add_argument("--dry-run", action="store_true", help="何を写すかだけ表示")
    ap.add_argument("--all", action="store_true", help="変わっていない行も書き直す")
    ap.add_argument("--if-ready", action="store_true", help="鍵が無ければ何もしない（定期実行）")
    a = ap.parse_args()
    if not a.dry_run and not (token() and parent_id()):
        if a.if_ready:
            return
        print("   Notion の鍵がまだありません。次を Mac のターミナルで実行して入れてください（チャットには貼らない）：\n"
              "     python3 scripts/setup_keys.py --only notion")
        sys.exit(1)
    try:
        sync(a.dry_run, a.all)
    except NotionError as e:
        hint = {401: "NOTION_TOKEN が違います（インテグレーションのシークレットを入れ直す）",
                403: "インテグレーションの権限が足りません（コンテンツの読み取り・更新・挿入をオンに）",
                404: "置き場所のページが見つかりません。開発者ポータルのコネクションの「Content access」→「Edit access」でこのページを選んだか確かめてください"}.get(e.code, "")
        print(f"   ✕ Notion に写せませんでした：{e}" + (f"\n     → {hint}" if hint else ""))
        sys.exit(1)


if __name__ == "__main__":
    main()

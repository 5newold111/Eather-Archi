#!/usr/bin/env python3
"""
Cloudflare R2（予備の保管庫）に預けたものを、Mac に戻す（backup_r2.py の逆。決定 174）

  Mac が壊れた・買い替えたときに、R2 に毎晩預けてある記録（ブリーフ・歌詞・配信用の音源・ジャケット・設定書・
  SSD の中身の写し）を、元と同じ場所に戻す。

  戻す場所
    R2 の label/<music-label からの場所>   → music-label/<同じ場所>
    R2 の label/ssd/<保管庫からの場所>     → SSD の「Postdog Records」（つながっているとき）
                                             つながっていなければ music-label/out/restore/ssd/<同じ場所>（あとで SSD に移す）
    R2 の label/vault/（鍵の金庫）          → ここでは戻さない（python3 scripts/vault.py restore）

  安全のための決まり
    ・中身が同じファイルは飛ばす（大きさと、R2 が持っている中身の指紋〔MD5〕で比べる）
    ・手元のほうが新しいファイルは上書きしない（--force を付けたときだけ上書き）
    ・上書きするときは、前のファイルを out/restore/replaced/<日時>/ によけてから
    ・戻したファイルは out/backup/r2_manifest.json に書くので、次の予備保管で上げ直しにならない

使い方
  python3 scripts/restore_r2.py --dry-run             # 何を戻すかだけ表示（何も書かない）
  python3 scripts/restore_r2.py                       # 戻す
  python3 scripts/restore_r2.py --only out/briefs     # この場所の下だけ（ssd/01_管理表 のようにも書ける）
  python3 scripts/restore_r2.py --force               # 手元のほうが新しくても R2 の中身で上書き

鍵（.env）：R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET
  （新しい Mac では、先に python3 scripts/vault.py restore で .env を戻す）
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_dotenv, read_json, ssl_context, step, write_json  # noqa: E402
from backup_r2 import sign_v4  # noqa: E402

load_dotenv()
PREFIX = "label/"          # backup_r2.py が R2 に置くときの頭
SKIP = ("vault/",)         # ここでは戻さないもの（鍵の金庫は vault.py）
MANIFEST = OUT / "backup" / "r2_manifest.json"
RESTORE = OUT / "restore"


class R2Error(RuntimeError):
    pass


def creds() -> tuple[str, str, str, str]:
    """(アカウント ID, バケット, アクセスキー ID, シークレット)。足りなければ空の文字が入る"""
    return (os.environ.get("R2_ACCOUNT_ID", "").strip(), os.environ.get("R2_BUCKET", "").strip() or "label-backup",
            os.environ.get("R2_ACCESS_KEY_ID", "").strip(), os.environ.get("R2_SECRET_ACCESS_KEY", "").strip())


def endpoint(account: str) -> str:
    """R2 の住所。R2_ENDPOINT は試験のときだけ（手元の偽サーバーに向ける）"""
    return os.environ.get("R2_ENDPOINT", "").strip().rstrip("/") or f"https://{account}.r2.cloudflarestorage.com"


def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe="-_.~")


def _open(method: str, account: str, bucket: str, key: str, ak: str, sk: str, query: dict | None = None,
          body: bytes = b"", ctype: str | None = None, timeout: int = 600):
    """署名を付けて R2 に 1 回たのむ（開いたままの返事を返す。呼んだ側で読む）"""
    base = endpoint(account)
    host = urllib.parse.urlparse(base).netloc
    path = f"/{bucket}/{key}" if key else f"/{bucket}"
    qs = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted((query or {}).items()))
    amz = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = hashlib.sha256(body).hexdigest()
    headers = {"x-amz-content-sha256": payload, "x-amz-date": amz}
    if ctype:
        headers["content-type"] = ctype
    auth = sign_v4(method, host, path, qs, headers, payload, ak, sk, "auto", amz)
    url = base + urllib.parse.quote(path, safe="/-_.~") + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, data=body if method in ("PUT", "POST") else None, method=method,
                                 headers={**headers, "Authorization": auth})
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ssl_context() if url.startswith("https") else None)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        hint = {403: "鍵が違うか、このバケットへの権限がありません", 404: "見つかりません（バケット名 R2_BUCKET・R2_ACCOUNT_ID・ファイルの場所を確認）"}.get(e.code, "")
        raise R2Error(f"R2 → HTTP {e.code} {hint}\n{msg}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise R2Error(f"R2 に届きません：{e}") from None


def put_object(account: str, bucket: str, key: str, body: bytes, ctype: str, ak: str, sk: str) -> None:
    """backup_r2.put_object と同じ（こちらは R2_ENDPOINT で向き先を変えられる）"""
    with _open("PUT", account, bucket, key, ak, sk, body=body, ctype=ctype) as r:
        r.read()


def get_object(account: str, bucket: str, key: str, ak: str, sk: str) -> bytes:
    with _open("GET", account, bucket, key, ak, sk, timeout=120) as r:
        return r.read()


def download(account: str, bucket: str, key: str, ak: str, sk: str, dst: Path) -> int:
    """大きいファイルも少しずつ読んで書く（途中のものは .part。終わってから名前を変える）"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f".{dst.name}.part")
    n = 0
    with _open("GET", account, bucket, key, ak, sk) as r, open(part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            n += len(chunk)
    part.replace(dst)
    return n


def list_objects(account: str, bucket: str, ak: str, sk: str, prefix: str = PREFIX) -> list[dict]:
    """prefix の下の全部のファイル：[{key, size, modified（UTC の秒）, etag}]。1,000 件ごとの続きも読む"""
    out, token = [], None
    while True:
        q = {"list-type": "2", "prefix": prefix, "max-keys": os.environ.get("R2_LIST_MAX", "1000")}
        if token:
            q["continuation-token"] = token
        with _open("GET", account, bucket, "", ak, sk, query=q, timeout=120) as r:
            root = ET.fromstring(r.read())
        for el in root.iter():   # 名前空間（{http://s3.amazonaws.com/…}）を外す
            el.tag = el.tag.split("}", 1)[-1]
        for c in root.findall("Contents"):
            mod = (c.findtext("LastModified") or "").replace("Z", "+00:00")
            try:
                ts = datetime.fromisoformat(mod).timestamp()
            except ValueError:
                ts = 0.0
            out.append({"key": c.findtext("Key") or "", "size": int(c.findtext("Size") or 0), "modified": ts,
                        "etag": (c.findtext("ETag") or "").strip('"')})
        token = root.findtext("NextContinuationToken")
        if (root.findtext("IsTruncated") or "").lower() != "true" or not token:
            return out


# ---------------------------------------------------------------------------
def ssd_root() -> Path | None:
    try:
        import _archive as archive
        base, _ = archive.root()
        return base
    except Exception:  # noqa: BLE001
        return None


def dest_of(rel: str, ssd: Path | None) -> Path | None:
    """R2 の label/ より後ろ（rel）から、戻す場所。戻さないもの・おかしな場所は None"""
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in ("..", ".") for p in parts) or rel.startswith("/") or any(rel.startswith(s) for s in SKIP):
        return None
    if parts[-1] == ".env" or parts[-1].startswith(".env."):   # 鍵は金庫から戻す
        return None
    if parts[0] == "ssd":
        if len(parts) < 2:
            return None
        return (ssd if ssd else RESTORE / "ssd").joinpath(*parts[1:])
    return ROOT.joinpath(*parts)


def _md5(p: Path) -> str:
    h = hashlib.md5()  # noqa: S324  R2 の指紋（ETag）と比べるためだけ
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def decide(obj: dict, dst: Path, force: bool) -> str:
    """same（同じ）・new（手元に無い）・update（R2 のほうが新しい）・local_newer（手元のほうが新しい → 上書きしない）"""
    if not dst.exists():
        return "new"
    st = dst.stat()
    if st.st_size == obj["size"] and (not obj["etag"] or "-" in obj["etag"] or _md5(dst) == obj["etag"]):
        return "same"   # 指紋が無い・分割で上げたもの（指紋が MD5 でない）は、大きさが同じなら同じとみなす
    if st.st_mtime > obj["modified"] and not force:
        return "local_newer"
    return "update"


def restore(only: str = "", dry: bool = False, force: bool = False) -> dict:
    account, bucket, ak, sk = creds()
    if not (account and ak and sk):
        raise R2Error("R2 の鍵（R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY）がありません。"
                      "新しい Mac では、先に python3 scripts/vault.py restore で鍵を戻してください")
    prefix = PREFIX + only.strip("/") if only else PREFIX
    step(f"R2 の中身を調べています（{bucket}/{prefix}）")
    objs = list_objects(account, bucket, ak, sk, prefix)
    ssd = ssd_root()
    if any(o["key"].startswith(PREFIX + "ssd/") for o in objs):
        print(f"   SSD の中身の戻し先：{ssd if ssd else str(RESTORE / 'ssd') + '（SSD がつながっていないので、いったんここへ）'}")
    counts = {"new": 0, "update": 0, "same": 0, "local_newer": 0, "skip": 0, "bytes": 0}
    todo = []
    for o in objs:
        rel = o["key"][len(PREFIX):] if o["key"].startswith(PREFIX) else ""
        dst = dest_of(rel, ssd)
        if dst is None or o["key"].endswith("/"):
            counts["skip"] += 1
            continue
        how = decide(o, dst, force)
        counts[how] += 1
        if how in ("new", "update"):
            todo.append((o, rel, dst, how))
            counts["bytes"] += o["size"]
        elif how == "local_newer":
            print(f"   － 手元のほうが新しいので上書きしません：{rel}")
    step(f"R2 に {len(objs)} 件：戻す {len(todo)} 件（{counts['bytes'] / 1024 / 1024:.1f} MB。新しく {counts['new']}・上書き {counts['update']}）・"
         f"同じ {counts['same']} 件・手元が新しい {counts['local_newer']} 件")
    if dry:
        for _, rel, dst, how in todo[:40]:
            print(f"     {'＋' if how == 'new' else '↻'} {rel}")
        if len(todo) > 40:
            print(f"     …ほか {len(todo) - 40} 件")
        return counts
    manifest = read_json(MANIFEST) if MANIFEST.exists() else {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for i, (o, rel, dst, how) in enumerate(todo, 1):
        step(f"[{i}/{len(todo)}] 戻しています：{rel}")
        if how == "update":   # 前のファイルは消さずによける
            keep = RESTORE / "replaced" / stamp / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, keep)
        download(account, bucket, o["key"], ak, sk, dst)
        if o["modified"]:
            os.utime(dst, (o["modified"], o["modified"]))
        st = dst.stat()
        manifest[rel] = [st.st_size, int(st.st_mtime)]   # backup_r2.py と同じ印（次の予備保管で上げ直さない）
        if i % 20 == 0:
            write_json(MANIFEST, manifest)
    if todo:
        write_json(MANIFEST, manifest)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="R2 に預けたものを Mac に戻す（backup_r2.py の逆）")
    ap.add_argument("--dry-run", action="store_true", help="何を戻すかだけ表示")
    ap.add_argument("--only", default="", help="この場所の下だけ（例：out/briefs・templates・ssd/01_管理表）")
    ap.add_argument("--force", action="store_true", help="手元のほうが新しくても R2 の中身で上書き")
    a = ap.parse_args()
    try:
        c = restore(a.only, a.dry_run, a.force)
    except R2Error as e:
        print(f"   ✕ {e}")
        print("【結果】R2 から戻せませんでした")
        sys.exit(1)
    if a.dry_run:
        print(f"【結果】戻す予定：{c['new'] + c['update']} 件（確認だけ。何も書いていません）")
    else:
        print(f"【結果】R2 から戻しました：新しく {c['new']} 件・上書き {c['update']} 件・同じ {c['same']} 件・手元が新しいので残した {c['local_newer']} 件")


if __name__ == "__main__":
    main()

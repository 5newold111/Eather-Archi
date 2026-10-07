#!/usr/bin/env python3
"""
手元の大事なファイルを Cloudflare R2（予備の保管庫）に毎晩上げる。変わっていないファイルは上げ直さない。

  上げるもの
    ・配信用の音源（out/masters/**/master.wav と音量の記録）
    ・選んだテイクの音源と選んだ記録（out/takes/*/selection.json）
    ・ジャケットの完成品（cover_final.jpg）と、選ばれたロゴ・写真
    ・ブリーフ・最終歌詞・登録データ・成績・傾向レポート・成長分析
    ・設定書（templates/）… アーティストやレーベルの設定そのもの
  上げないもの：鍵（.env）、没テイクの音源、ログ

  R2 側の置き場所：<バケット>/label/<music-label からの相対パス>

使い方
  python scripts/backup_r2.py              # 定期実行（毎日 03:30）
  python scripts/backup_r2.py --dry-run    # 何を上げるかだけ表示
  python scripts/backup_r2.py --all        # 変わっていなくても全部上げ直す

鍵（.env）：R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET
  （Cloudflare の R2 → API トークン。権限は Object Read & Write、対象はこのバケットだけ）
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_dotenv, read_json, ssl_context, step, write_json  # noqa: E402

load_dotenv()
MANIFEST = OUT / "backup" / "r2_manifest.json"
PATTERNS = [
    "out/masters/**/master.wav", "out/masters/**/*.report.json",
    "out/takes/*/selection.json",
    "out/visuals/*/cover/*/cover_final.jpg", "out/visuals/*/cover/*/selection_cover.json",
    "out/visuals/*/debut/selection_*.json", "out/visuals/*/reshoot/selection_*.json",
    "out/briefs/*.json", "out/briefs/*.lyrics_final.txt",
    "out/distrokid/**/*.json", "out/distrokid/**/*.md",
    "out/metrics/daily.csv", "out/metrics/snapshots.json",
    "out/growth/*.json", "out/growth/*.md", "out/trends/*.json", "out/trends/*.md",
    "out/references/*.json", "out/collabs/*.json", "out/social/*/*/plan.json",
    "templates/**/*.json",
]
CTYPE = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".jpg": "image/jpeg", ".png": "image/png", ".json": "application/json",
         ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8"}


# ---------------------------------------------------------------------------
# 署名（AWS 署名バージョン 4。R2 は S3 と同じ方式で鍵を確かめる）
# ---------------------------------------------------------------------------
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_v4(method: str, host: str, path: str, query: str, headers: dict[str, str], payload_hash: str,
            access_key: str, secret_key: str, region: str, amz_date: str, service: str = "s3") -> str:
    """Authorization ヘッダーの値を作る"""
    date_stamp = amz_date[:8]
    hdrs = {k.lower(): " ".join(str(v).strip().split()) for k, v in {**headers, "host": host}.items()}
    signed = ";".join(sorted(hdrs))
    canonical_headers = "".join(f"{k}:{hdrs[k]}\n" for k in sorted(hdrs))
    canonical = "\n".join([method, urllib.parse.quote(path, safe="/-_.~"), query, canonical_headers, signed, payload_hash])
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), date_stamp), region), service), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"


def put_object(account: str, bucket: str, key: str, body: bytes, ctype: str, ak: str, sk: str) -> None:
    host = f"{account}.r2.cloudflarestorage.com"
    path = f"/{bucket}/{key}"
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {"content-type": ctype, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    auth = sign_v4("PUT", host, path, "", headers, payload_hash, ak, sk, "auto", amz_date)
    req = urllib.request.Request(f"https://{host}{urllib.parse.quote(path, safe='/-_.~')}", data=body, method="PUT",
                                 headers={**headers, "Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=600, context=ssl_context()) as r:
            r.read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        hint = {403: "鍵が違うか、このバケットへの書き込み権限が無い", 404: "バケット名（R2_BUCKET）か R2_ACCOUNT_ID が違う"}.get(e.code, "")
        raise RuntimeError(f"R2 → HTTP {e.code} {hint}\n{msg}") from None


# ---------------------------------------------------------------------------
def targets() -> list[Path]:
    files: set[Path] = set()
    for pat in PATTERNS:
        files.update(p for p in ROOT.glob(pat) if p.is_file())
    # 選んだテイクの音源（没テイクは上げない）
    for sel in OUT.glob("takes/*/selection.json"):
        d = read_json(sel)
        chosen = (d.get("decision") or {}).get("selected")
        if chosen and (sel.parent / chosen).exists():
            files.add(sel.parent / chosen)
    # 選ばれたロゴ・写真の画像
    for sel in OUT.glob("visuals/*/*/selection_*.json"):
        d = read_json(sel)
        f = sel.parent / f"{d.get('kind')}_{int(d.get('chosen', 0)):02d}.png"
        if f.exists():
            files.add(f)
    return sorted(p for p in files if ".env" not in p.name and not p.name.endswith(".prompt.md"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Cloudflare R2 への予備保管")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    account, bucket = os.environ.get("R2_ACCOUNT_ID"), os.environ.get("R2_BUCKET", "label-backup")
    ak, sk = os.environ.get("R2_ACCESS_KEY_ID"), os.environ.get("R2_SECRET_ACCESS_KEY")
    manifest = {} if a.all or not MANIFEST.exists() else read_json(MANIFEST)
    files = targets()
    todo = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        st = f.stat()
        sig = [st.st_size, int(st.st_mtime)]
        if manifest.get(rel) != sig:
            todo.append((f, rel, sig))
    size = sum(s[0] for _, _, s in todo)
    step(f"予備保管の対象 {len(files)} 件のうち、新しいか変わったもの {len(todo)} 件（{size / 1024 / 1024:.1f} MB）")
    if a.dry_run or not (account and ak and sk):
        if not a.dry_run:
            print("   R2 の鍵（R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY）が無いので、上げずに一覧だけ表示します")
        for _, rel, _ in todo[:30]:
            print(f"     {rel}")
        if len(todo) > 30:
            print(f"     …ほか {len(todo) - 30} 件")
        return
    for i, (f, rel, sig) in enumerate(todo, 1):
        step(f"[{i}/{len(todo)}] R2 に上げています: {rel}")
        put_object(account, bucket, f"label/{rel}", f.read_bytes(), CTYPE.get(f.suffix.lower(), "application/octet-stream"), ak, sk)
        manifest[rel] = sig
        if i % 20 == 0:
            write_json(MANIFEST, manifest)
    write_json(MANIFEST, manifest)
    step("予備保管が終わりました")


if __name__ == "__main__":
    main()

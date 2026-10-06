"""
Supabase への書き込みの小道具（標準ライブラリだけで動く）。

鍵（SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY）は .env から読むだけで、画面にもファイルにも出さない。
鍵が無いときは SQL ファイルを書き出す（Supabase の SQL Editor に貼れば同じことができる）。

行の中で別の表の id が要る所は Ref で書く。
  Ref("artists", {"slug": "light"})                      → SQL では (select id from artists where slug = 'light')
                                                          → API では先に id を調べて埋める
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from _common import ssl_context


@dataclass(frozen=True)
class Ref:
    table: str
    where: tuple  # ((列, 値 or Ref), ...)

    @staticmethod
    def of(table: str, **where) -> "Ref":
        return Ref(table, tuple(sorted(where.items())))


# ---------------------------------------------------------------------------
# SQL に書き出す
# ---------------------------------------------------------------------------
def sql_value(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, Ref):
        cond = " and ".join(f"{k} = {sql_value(x)}" for k, x in v.where)
        return f"(select id from {v.table} where {cond})"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (dict,)):
        return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    if isinstance(v, list):
        if all(isinstance(x, str) for x in v):
            return "array[" + ", ".join(sql_value(x) for x in v) + "]::text[]" if v else "'{}'::text[]"
        return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def upsert_sql(table: str, row: dict, conflict: list[str]) -> str:
    cols = list(row)
    vals = ", ".join(sql_value(row[c]) for c in cols)
    upd = [c for c in cols if c not in conflict]
    tail = (f" on conflict ({', '.join(conflict)}) do update set " + ", ".join(f"{c} = excluded.{c}" for c in upd)
            if upd else f" on conflict ({', '.join(conflict)}) do nothing")
    return f"insert into {table} ({', '.join(cols)}) values ({vals}){tail};"


# ---------------------------------------------------------------------------
# API で書き込む
# ---------------------------------------------------------------------------
class Client:
    def __init__(self) -> None:
        self.url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
        self._ids: dict[Ref, str] = {}

    @property
    def ready(self) -> bool:
        return bool(self.url and self.key)

    def _req(self, method: str, path: str, body=None, headers: dict | None = None, raw: bytes | None = None):
        h = {"apikey": self.key}
        if not self.key.startswith("sb_"):
            # 旧形式の service_role キー（JWT）は Authorization にも入れる。新形式（sb_secret_…）は apikey だけで通る
            h["Authorization"] = f"Bearer {self.key}"
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300, context=ssl_context()) as r:
                txt = r.read().decode() or "null"
                return json.loads(txt)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:400]
            hint = {401: "鍵が違うか期限切れ（SUPABASE_SERVICE_ROLE_KEY を確認）",
                    404: "表かバケットが無い（schema.sql / storage.sql を流したか確認）",
                    409: "一意制約にぶつかった（同じ週・同じ組が既にある等）"}.get(e.code, "")
            raise RuntimeError(f"Supabase {method} {path.split('?')[0]} → HTTP {e.code} {hint}\n{msg}") from None

    def resolve(self, v):
        if not isinstance(v, Ref):
            return v
        if v in self._ids:
            return self._ids[v]
        q = "&".join(f"{k}=eq.{urllib.parse.quote(str(self.resolve(x)))}" for k, x in v.where)
        rows = self._req("GET", f"/rest/v1/{v.table}?select=id&{q}")
        if not rows:
            raise RuntimeError(f"{v.table} に {dict(v.where)} が見つかりません（先に seed / 前の手順を実行）")
        self._ids[v] = rows[0]["id"]
        return self._ids[v]

    def upsert(self, table: str, row: dict, conflict: list[str]) -> dict:
        body = {k: self.resolve(v) for k, v in row.items()}
        res = self._req("POST", f"/rest/v1/{table}?on_conflict={','.join(conflict)}", body,
                        {"Prefer": "resolution=merge-duplicates,return=representation"})
        return res[0] if isinstance(res, list) and res else {}

    def upload(self, bucket: str, path: str, file: Path, content_type: str) -> str:
        self._req("POST", f"/storage/v1/object/{bucket}/{urllib.parse.quote(path)}",
                  headers={"Content-Type": content_type, "x-upsert": "true"}, raw=file.read_bytes())
        return f"{bucket}/{path}"

    def signed_url(self, bucket: str, path: str, expires: int = 3600) -> str:
        r = self._req("POST", f"/storage/v1/object/sign/{bucket}/{urllib.parse.quote(path)}", {"expiresIn": expires})
        return self.url + "/storage/v1" + r["signedURL"]

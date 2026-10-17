#!/usr/bin/env python3
"""
成績（再生数・保存数・スキップ数）を集めて 1 つの表にまとめ、成長分析に渡す。

  集め方
    1. 受け取り箱（out/metrics/inbox/）に置かれた書き出しファイルを読む
         ・DistroKid の「Excruciating detail」（Bank → See excruciating detail → Download）… 月ごと・ストアごとの再生数
         ・Spotify for Artists / Apple Music for Artists などの CSV … 列名を自動で判別（日付・曲名・再生・保存・スキップ）
           日付の列が無い「累計」の表は、前回との差を今日の数字として記録する
       読んだファイルは inbox/done/ に移す
    2. SNS の数字を公式 API で取る（鍵があれば）：YouTube の再生・高評価、Instagram の再生・保存、TikTok の再生・いいね
       どれも累計なので、前回との差を今日の数字にする

  曲の見分け方：ISRC（国際標準レコーディングコード）が一致すればそれ、無ければ「アーティスト名＋曲名」

  出力
    out/metrics/daily.csv          … 全部の記録（release_id, artist_slug, title, release_date, date, platform, kind, streams, saves, skips, listeners）
    out/metrics/metrics.csv        … 成長分析用（kind=dsp＝配信サービスの数字だけ。SNS の再生は混ぜない）
    out/metrics/brief_sources.csv  … どの曲にどの参考曲を使ったか（成長分析の「要素」の判定用）
    out/metrics/unmatched.csv      … どの曲か分からなかった行（曲名の綴り違いなど）

使い方
  python scripts/collect_metrics.py                 # 受け取り箱と SNS を集める
  python scripts/collect_metrics.py --analyze       # 集めた後に analyze_growth.py まで流す（定期実行はこれ）
  python scripts/collect_metrics.py --no-sns        # SNS の API を呼ばない
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, load_artist, load_dotenv, read_json, step, write_json  # noqa: E402

load_dotenv()
M = OUT / "metrics"
INBOX = M / "inbox"
FIELDS = ["release_id", "artist_slug", "title", "release_date", "date", "platform", "kind",
          "streams", "saves", "skips", "listeners"]
SNS_PLATFORMS = {"youtube_shorts", "instagram_reels", "tiktok"}


STORE = {"spotify": "spotify", "apple music": "apple", "itunes": "apple", "amazon": "amazon", "youtube": "youtube_music",
         "deezer": "deezer", "tidal": "tidal", "tiktok": "tiktok_ugc", "instagram": "meta_ugc", "facebook": "meta_ugc"}


def store_name(s: str) -> str:
    low = (s or "").strip().lower()
    return next((v for k, v in STORE.items() if low.startswith(k)), re.sub(r"[^a-z0-9]+", "_", low) or "other")


def norm(s: str) -> str:
    s = re.sub(r"\((feat|ft)\.?[^)]*\)", "", str(s), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------------------
# 自分たちの曲の一覧（DistroKid 登録シートのデータから）
# ---------------------------------------------------------------------------
def releases() -> list[dict]:
    out = []
    for mp in sorted((OUT / "distrokid").glob("*/*.metadata.json")):
        m = read_json(mp)
        if not m.get("title") or m["title"].startswith("（"):
            continue
        a = load_artist(m["artist_slug"], m.get("label_slug"))
        out.append({"release_id": f"{m['week_start']}_{m['artist_slug']}", "artist_slug": m["artist_slug"],
                    "artist": a["name"], "title": m["title"], "isrc": (m.get("isrc") or "").upper(),
                    "release_date": m["release_at_utc"][:10], "week_start": m["week_start"], "meta_path": str(mp)})
    return out


def matcher(rels: list[dict]):
    by_isrc = {r["isrc"]: r for r in rels if r["isrc"]}
    by_name = {(norm(r["artist"]), norm(r["title"])): r for r in rels}
    by_title = {}
    for r in rels:
        by_title.setdefault(norm(r["title"]), []).append(r)

    def find(isrc: str = "", artist: str = "", title: str = "") -> dict | None:
        if isrc and isrc.upper() in by_isrc:
            return by_isrc[isrc.upper()]
        if (norm(artist), norm(title)) in by_name:
            return by_name[(norm(artist), norm(title))]
        cands = by_title.get(norm(title), [])
        return cands[0] if len(cands) == 1 and not artist else None
    return find


# ---------------------------------------------------------------------------
# 記録の保管（同じ曲・日・場所は上書き）
# ---------------------------------------------------------------------------
def load_daily() -> dict[tuple, dict]:
    p = M / "daily.csv"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return {(r["release_id"], r["date"], r["platform"]): r for r in csv.DictReader(f)}


def save_daily(rows: dict[tuple, dict]) -> None:
    M.mkdir(parents=True, exist_ok=True)
    with (M / "daily.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({c: rows[k].get(c, 0) for c in FIELDS})


def put(rows: dict, rel: dict, day: str, platform: str, kind: str, streams=0, saves=0, skips=0, listeners=0,
        add: bool = False) -> None:
    k = (rel["release_id"], day, platform)
    old = rows.get(k, {})
    def v(name, x):
        return int(float(old.get(name, 0) or 0)) + int(x) if add else int(x)
    rows[k] = {"release_id": rel["release_id"], "artist_slug": rel["artist_slug"], "title": rel["title"],
               "release_date": rel["release_date"], "date": day, "platform": platform, "kind": kind,
               "streams": v("streams", streams), "saves": v("saves", saves), "skips": v("skips", skips),
               "listeners": v("listeners", listeners)}


# 累計の数字 → 前回との差
def delta(snap: dict, key: str, values: dict) -> dict:
    prev = snap.get(key, {})
    d = {k: max(0, int(values.get(k, 0)) - int(prev.get(k, 0))) for k in values}
    snap[key] = values
    return d


# ---------------------------------------------------------------------------
# 受け取り箱の読み込み
# ---------------------------------------------------------------------------
def read_table(p: Path) -> list[dict]:
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    delim = "\t" if text.count("\t") > text.count(",") else ","
    return list(csv.DictReader(text.splitlines(), delimiter=delim))


def col(row: dict, *names) -> str | None:
    low = {k.strip().lower(): k for k in row}
    for n in names:
        for k_low, k in low.items():
            if k_low == n or k_low.startswith(n):
                return k
    return None


def import_file(p: Path, find, rows: dict, snap: dict, unmatched: list) -> int:
    data = read_table(p)
    if not data:
        return 0
    first = data[0]
    n = 0
    # DistroKid の詳細明細
    if col(first, "sale month") and col(first, "store") and col(first, "quantity"):
        for r in data:
            if (r.get(col(first, "song/album")) or "Song").lower().startswith("album"):
                continue
            rel = find(r.get(col(first, "isrc")) or "", r.get(col(first, "artist")) or "", r.get(col(first, "title")) or "")
            if not rel:
                unmatched.append({"file": p.name, "artist": r.get(col(first, "artist")), "title": r.get(col(first, "title"))})
                continue
            month = (r[col(first, "sale month")] or "")[:7]
            # DistroKid の明細は月単位。同じストアの日ごとの数字（Spotify for Artists など）があれば、そちらを優先する
            put(rows, rel, f"{month}-01", store_name(r[col(first, "store")]), "dsp_monthly",
                streams=int(float(r[col(first, "quantity")] or 0)), add=True)
            n += 1
        return n
    # それ以外の CSV（Spotify for Artists など）
    platform = "apple" if "apple" in p.name.lower() else ("amazon" if "amazon" in p.name.lower() else "spotify")
    c_date = col(first, "date", "day")
    c_title = col(first, "song", "track", "title", "name")
    c_artist = col(first, "artist")
    c_isrc = col(first, "isrc")
    c_streams = col(first, "streams", "plays")
    c_saves = col(first, "saves", "saved")
    c_skips = col(first, "skips")
    c_listen = col(first, "listeners")
    if not (c_title and c_streams):
        print(f"   － {p.name}：曲名と再生数の列が見つからないので飛ばします（列：{', '.join(first)[:120]}）")
        return 0
    today = date.today().isoformat()
    for r in data:
        rel = find(r.get(c_isrc, "") if c_isrc else "", r.get(c_artist, "") if c_artist else "", r[c_title])
        if not rel:
            unmatched.append({"file": p.name, "artist": r.get(c_artist) if c_artist else "", "title": r[c_title]})
            continue
        vals = {"streams": num(r.get(c_streams)), "saves": num(r.get(c_saves)) if c_saves else 0,
                "skips": num(r.get(c_skips)) if c_skips else 0, "listeners": num(r.get(c_listen)) if c_listen else 0}
        if c_date and r.get(c_date):
            put(rows, rel, r[c_date][:10], platform, "dsp", **vals)
        else:   # 累計の表：前回との差を今日の数字に
            put(rows, rel, today, platform, "dsp", **delta(snap, f"{platform}|{rel['release_id']}", vals), add=True)
        n += 1
    return n


def num(v) -> int:
    try:
        return int(float(str(v or 0).replace(",", "")))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# SNS の数字（公式 API）
# ---------------------------------------------------------------------------
def sns_collect(rels: list[dict], rows: dict, snap: dict) -> None:
    from post_social import cred, http
    by_week_slug = {(r["week_start"], r["artist_slug"]): r for r in rels}
    today = date.today().isoformat()
    for pp in sorted((OUT / "social").glob("*/*/plan.json")):
        week, slug = pp.parent.parent.name, pp.parent.name
        rel = by_week_slug.get((week, slug))
        if not rel:
            continue
        plan = read_json(pp)
        label = next((p.get("label_slug") for p in plan["posts"]), None) or "drive"
        for p in plan["posts"]:
            res = p.get("result") or {}
            if p.get("status") != "done":
                continue
            try:
                if p["platform"] == "youtube" and res.get("id"):
                    vals = youtube_stats(res["id"], slug, label, cred, http)
                    plat = "youtube_shorts"
                elif p["platform"] == "instagram" and res.get("id"):
                    vals = instagram_stats(res["id"], slug, label, cred, http)
                    plat = "instagram_reels"
                elif p["platform"] == "tiktok" and res.get("publish_id"):
                    vals = tiktok_stats(res, slug, label, cred, http)
                    plat = "tiktok"
                    write_json(pp, plan)   # TikTok の投稿 ID を覚えておく
                else:
                    continue
            except Exception as e:  # noqa: BLE001
                print(f"   ✕ {slug} / {p['platform']}：数字を取れませんでした（{str(e)[:120]}）")
                continue
            if vals is None:
                continue
            put(rows, rel, today, plat, "sns", **delta(snap, f"{plat}|{rel['release_id']}", vals), add=True)
            print(f"   ○ {slug} / {plat}：累計 再生 {vals['streams']:,}・保存/いいね {vals['saves']:,}")


def youtube_stats(video_id, slug, label, cred, http):
    cid, csec, rtok = (cred(k, slug, label) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))
    if not (cid and csec and rtok):
        return None
    tok = http("POST", "https://oauth2.googleapis.com/token",
               form={"client_id": cid, "client_secret": csec, "refresh_token": rtok, "grant_type": "refresh_token"})["access_token"]
    d = http("GET", f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={video_id}",
             headers={"Authorization": f"Bearer {tok}"})
    st = (d.get("items") or [{}])[0].get("statistics", {})
    return {"streams": int(st.get("viewCount", 0)), "saves": int(st.get("likeCount", 0)), "skips": 0, "listeners": 0}


def instagram_stats(media_id, slug, label, cred, http):
    tok = cred("IG_ACCESS_TOKEN", slug, label)
    if not tok:
        return None
    host = os.environ.get("IG_GRAPH_HOST", "graph.instagram.com")
    d = http("GET", f"https://{host}/v21.0/{media_id}/insights?metric=views,saved,likes,reach"
                    f"&access_token={urllib.parse.quote(tok)}")
    v = {x["name"]: (x.get("values") or [{}])[0].get("value", 0) for x in d.get("data", [])}
    return {"streams": int(v.get("views", 0)), "saves": int(v.get("saved", 0)), "skips": 0, "listeners": int(v.get("reach", 0))}


def tiktok_stats(res: dict, slug, label, cred, http):
    from post_social import tiktok_access
    tok = tiktok_access(slug, label)
    if not tok:
        return None
    api = "https://open.tiktokapis.com/v2"
    auth = {"Authorization": f"Bearer {tok}"}
    if not res.get("video_id"):
        st = http("POST", f"{api}/post/publish/status/fetch/", headers=auth, body={"publish_id": res["publish_id"]})["data"]
        ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
        if not ids:
            return None   # 自分だけ公開（審査前）の投稿は ID が出ない
        res["video_id"] = str(ids[0])
    d = http("POST", f"{api}/video/query/?fields=id,view_count,like_count,share_count", headers=auth,
             body={"filters": {"video_ids": [res["video_id"]]}})
    v = (d.get("data", {}).get("videos") or [{}])[0]
    return {"streams": int(v.get("view_count", 0)), "saves": int(v.get("like_count", 0)), "skips": 0, "listeners": 0}


# ---------------------------------------------------------------------------
def write_for_growth(rows: dict) -> None:
    direct = {(r["release_id"], r["platform"]) for r in rows.values() if r.get("kind", "dsp") == "dsp"}
    dsp = [r for r in rows.values() if r.get("kind", "dsp") == "dsp"
           or (r.get("kind") == "dsp_monthly" and (r["release_id"], r["platform"]) not in direct)]   # 二重に数えない
    with (M / "metrics.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["release_id", "artist_slug", "title", "release_date", "date", "platform",
                                          "streams", "saves", "skips"])
        w.writeheader()
        for r in sorted(dsp, key=lambda r: (r["release_id"], r["date"], r["platform"])):
            w.writerow({k: r[k] for k in w.fieldnames})
    with (M / "brief_sources.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["release_id", "slot", "reference_id"])
        w.writeheader()
        for bp in sorted((OUT / "briefs").glob("*.json")):
            if "." in bp.stem:
                continue
            b = read_json(bp)
            for slot, s in (b.get("slots") or {}).items():
                if s and s.get("reference_id"):
                    w.writerow({"release_id": f"{b['week_start']}_{b['artist_slug']}", "slot": slot,
                                "reference_id": s["reference_id"]})


def mark_live(rels: list[dict], rows: dict) -> None:
    """配信日を過ぎ、数字が出始めた曲は status を live にする"""
    has = {k[0] for k in rows}
    now = datetime.now(timezone.utc).date().isoformat()
    for r in rels:
        if r["release_id"] in has and r["release_date"] <= now:
            m = read_json(Path(r["meta_path"]))
            if m.get("status") in ("planned", "mastered", "uploaded"):
                m["status"] = "live"
                write_json(Path(r["meta_path"]), m)


def main() -> None:
    ap = argparse.ArgumentParser(description="成績を集めて成長分析に渡す")
    ap.add_argument("--analyze", action="store_true", help="集めた後に analyze_growth.py を実行する")
    ap.add_argument("--no-sns", action="store_true")
    a = ap.parse_args()

    INBOX.mkdir(parents=True, exist_ok=True)
    rels = releases()
    step(f"自分たちの曲 {len(rels)} 曲を読み込みました（DistroKid 登録シートのデータから）")
    find = matcher(rels)
    rows = load_daily()
    snap_path = M / "snapshots.json"
    snap = read_json(snap_path) if snap_path.exists() else {}
    unmatched: list[dict] = []

    files = sorted(p for p in INBOX.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".tsv", ".txt"))
    if not files:
        print(f"   受け取り箱は空です（{INBOX.relative_to(ROOT)}/ に DistroKid や Spotify for Artists の書き出しを置く）")
    for p in files:
        step(f"{p.name} を読み込んでいます")
        n = import_file(p, find, rows, snap, unmatched)
        print(f"     {n} 行を記録しました")
        (INBOX / "done").mkdir(exist_ok=True)
        shutil.move(str(p), INBOX / "done" / f"{date.today()}_{p.name}")

    if not a.no_sns:
        step("SNS の数字を集めています（鍵がある所だけ）")
        sns_collect(rels, rows, snap)

    save_daily(rows)
    write_json(snap_path, snap)
    write_for_growth(rows)
    mark_live(rels, rows)
    if unmatched:
        with (M / "unmatched.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["file", "artist", "title"])
            w.writeheader()
            w.writerows(unmatched)
        print(f"   ⚠ どの曲か分からなかった行が {len(unmatched)} 行あります: out/metrics/unmatched.csv（曲名の綴りを確認）")
    n_dsp = sum(1 for r in rows.values() if r.get("kind", "dsp") in ("dsp", "dsp_monthly"))
    step(f"記録を保存しました: out/metrics/daily.csv（配信サービス {n_dsp} 行・SNS {len(rows) - n_dsp} 行）")

    if a.analyze:
        if not n_dsp:
            print("   配信サービスの数字がまだ無いので、成長分析は飛ばします")
            return
        step("成長分析を実行しています（analyze_growth.py）")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "analyze_growth.py"),
                        "--metrics", str(M / "metrics.csv"), "--sources", str(M / "brief_sources.csv")], check=False)


if __name__ == "__main__":
    main()

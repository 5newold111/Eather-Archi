#!/usr/bin/env python3
"""
アーティスト名・レーベル名の重複確認（デビュー処理の最初に自動で走る）

照合先：
  鍵なし  : Apple Music / iTunes 検索、MusicBrainz、Deezer
  鍵あり  : Spotify（SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET）、YouTube（YOUTUBE_API_KEY）
  手動    : 商標（J-PlatPat / USPTO / EUIPO / WIPO）、SNS のハンドル → 確認用リンクを出す

判定：
  clear      : 完全一致（大文字小文字・記号・空白を無視）が 1 件もない
  collision  : 完全一致あり → 名前を変える（name_candidates の次の候補で再確認）
  near       : ほぼ一致（含む / 含まれる）→ 人が見る

結果：
  out/names/report.md と names.json。clear なら設定書の name_status を checked に更新（--apply）。
  commercial な配信登録は checked になった名前だけ行う。

使い方：
  python3 scripts/check_names.py --all                 # 本体 5 組 ＋ 子レーベル 4 ＋ 12 組
  python3 scripts/check_names.py --artist light --apply
  python3 scripts/check_names.py --name "AURELINE"     # 単発
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, urllib.parse, urllib.request, base64
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "EtherArchi-label-namecheck/1.0 (contact: see repository)"

def load_dotenv(path: Path) -> None:
    """music-label/.env があれば読み込んで環境変数にする（既に設定済みのものは上書きしない）"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")



def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def get_json(url: str, headers: dict | None = None, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# 照合先
# ---------------------------------------------------------------------------
def q_itunes(name: str) -> list[str]:
    d = get_json("https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": name, "entity": "musicArtist", "limit": 25}))
    return [r.get("artistName", "") for r in d.get("results", [])]


def q_musicbrainz(name: str) -> list[str]:
    q = urllib.parse.quote(f'artist:"{name}"')
    d = get_json(f"https://musicbrainz.org/ws/2/artist/?query={q}&fmt=json&limit=25")
    time.sleep(1.1)   # MusicBrainz は 1 リクエスト / 秒
    return [a.get("name", "") for a in d.get("artists", [])]


def q_deezer(name: str) -> list[str]:
    d = get_json("https://api.deezer.com/search/artist?" + urllib.parse.urlencode({"q": name, "limit": 25}))
    return [a.get("name", "") for a in d.get("data", [])]


_spotify_token: str | None = None
def q_spotify(name: str) -> list[str] | None:
    global _spotify_token
    cid, sec = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not (cid and sec):
        return None
    if not _spotify_token:
        body = b"grant_type=client_credentials"
        auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        req = urllib.request.Request("https://accounts.spotify.com/api/token", data=body,
                                     headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=20) as r:
            _spotify_token = json.loads(r.read())["access_token"]
    d = get_json("https://api.spotify.com/v1/search?" + urllib.parse.urlencode({"q": f'artist:"{name}"', "type": "artist", "limit": 25}),
                 headers={"Authorization": f"Bearer {_spotify_token}"})
    return [a.get("name", "") for a in d.get("artists", {}).get("items", [])]


def q_youtube(name: str) -> list[str] | None:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return None
    d = get_json("https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(
        {"part": "snippet", "q": name, "type": "channel", "maxResults": 25, "key": key}))
    return [i["snippet"]["title"] for i in d.get("items", [])]


SOURCES = [("Apple Music / iTunes", q_itunes), ("MusicBrainz", q_musicbrainz), ("Deezer", q_deezer),
           ("Spotify", q_spotify), ("YouTube", q_youtube)]


def manual_links(name: str) -> dict[str, str]:
    q = urllib.parse.quote(name)
    h = re.sub(r"[^a-z0-9_.]", "", name.lower().replace(" ", "").replace("&", "and"))
    return {
        "Spotify（手動）": f"https://open.spotify.com/search/{q}/artists",
        "YouTube（手動）": f"https://www.youtube.com/results?search_query={q}&sp=EgIQAg%253D%253D",
        "Instagram ハンドル": f"https://www.instagram.com/{h}/",
        "TikTok ハンドル": f"https://www.tiktok.com/@{h}",
        "商標 J-PlatPat": "https://www.j-platpat.inpit.go.jp/（商標検索で『" + name + "』を称呼検索）",
        "商標 USPTO": f"https://tmsearch.uspto.gov/search/search-information（『{name}』）",
        "商標 WIPO Global Brand DB": f"https://branddb.wipo.int/（『{name}』）",
        "Google": f"https://www.google.com/search?q=%22{q}%22+music",
    }


# ---------------------------------------------------------------------------
def check_name(name: str) -> dict:
    n = norm(name)
    result = {"name": name, "checked_at": date.today().isoformat(), "sources": {}, "exact": [], "near": [], "skipped": []}
    for label, fn in SOURCES:
        try:
            hits = fn(name)
        except Exception as e:   # noqa: BLE001
            result["sources"][label] = {"error": str(e)[:120]}
            continue
        if hits is None:
            result["skipped"].append(label); continue
        exact = sorted({h for h in hits if norm(h) == n})
        near = sorted({h for h in hits if h and norm(h) != n and (n in norm(h) or (len(norm(h)) >= 4 and norm(h) in n))})
        result["sources"][label] = {"hits": len(hits), "exact": exact, "near": near[:8]}
        result["exact"] += [f"{label}: {h}" for h in exact]
        result["near"] += [f"{label}: {h}" for h in near[:8]]
    answered = [k for k, v in result["sources"].items() if "error" not in v]
    if result["exact"]:
        result["verdict"] = "collision"
    elif not answered:
        result["verdict"] = "unverified"          # 1 件も照合できていない（ネットワーク制限など）→ clear にしない
    elif result["near"]:
        result["verdict"] = "near"
    else:
        result["verdict"] = "clear"
    result["answered_sources"] = answered
    result["manual"] = manual_links(name)
    return result


def iter_targets(args) -> list[tuple[str, str, Path | None, str | None]]:
    """(表示名, 名前, 設定書パス, 子レーベル slug) の一覧"""
    t = []
    if args.name:
        return [("（単発）", args.name, None, None)]
    if args.artist and not args.label:
        p = ROOT / "templates" / "artists" / f"{args.artist}.json"
        return [(args.artist, json.loads(p.read_text(encoding="utf-8"))["name"], p, None)]
    if args.all or args.label:
        if args.all:
            for p in sorted((ROOT / "templates" / "artists").glob("[!_]*.json")):
                a = json.loads(p.read_text(encoding="utf-8")); t.append((a["slug"], a["name"], p, None))
        labels = [args.label] if args.label else [p.stem for p in sorted((ROOT / "templates" / "labels").glob("[!_]*.json"))]
        for ls in labels:
            p = ROOT / "templates" / "labels" / f"{ls}.json"; l = json.loads(p.read_text(encoding="utf-8"))
            t.append((f"[レーベル] {ls}", l["name"], p, ls))
            for a in l["artists"]:
                if not args.artist or a["slug"] == args.artist:
                    t.append((a["slug"], a["name"], p, ls))
    return t


def apply_status(path: Path, label_slug: str | None, slug_or_label: str, verdict: str) -> None:
    """clear なら name_status を checked に。collision なら draft のまま、フラグを付ける"""
    d = json.loads(path.read_text(encoding="utf-8"))
    status = "checked" if verdict == "clear" else "draft"   # near / collision / unverified は draft のまま
    def set_on(obj):
        obj["name_status"] = status
        obj["name_check"] = {"verdict": verdict, "date": date.today().isoformat(), "manual_pending": ["商標", "SNS ハンドル"] if verdict == "clear" else []}
    if label_slug and slug_or_label.startswith("[レーベル]"):
        set_on(d)
    elif label_slug:
        for a in d["artists"]:
            if a["slug"] == slug_or_label: set_on(a)
    else:
        set_on(d)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="アーティスト名・レーベル名の重複確認")
    ap.add_argument("--all", action="store_true"); ap.add_argument("--artist"); ap.add_argument("--label"); ap.add_argument("--name")
    ap.add_argument("--apply", action="store_true", help="clear なら設定書の name_status を checked に更新する")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "names")
    args = ap.parse_args()
    if not (args.all or args.artist or args.label or args.name):
        ap.error("--all / --artist / --label / --name のどれかを指定してください")

    targets = iter_targets(args)
    print(f"=== 名前の重複確認を始めます（{len(targets)} 件）===")
    results = []
    for disp, name, path, ls in targets:
        r = check_name(name); r["target"] = disp; results.append(r)
        mark = {"clear": "○ clear", "near": "△ near", "collision": "× collision", "unverified": "？ unverified"}[r["verdict"]]
        print(f"  {mark:12} {disp:18} {name}" + (f"   ← {r['exact'][0]}" if r["exact"] else "") + (f"   （鍵なしで未照合: {', '.join(r['skipped'])}）" if r["skipped"] and disp == targets[0][0] else ""))
        if args.apply and path:
            apply_status(path, ls, disp, r["verdict"])

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "names.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# 名前の重複確認レポート（{date.today()}）", "", "| 対象 | 名前 | 判定 | 完全一致 | ほぼ一致 |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['target']} | **{r['name']}** | {r['verdict']} | {'<br>'.join(r['exact']) or '—'} | {'<br>'.join(r['near'][:4]) or '—'} |")
    lines += ["", "## 判定の意味", "", "- **clear**：機械で照合した配信サービスに完全一致なし。商標と SNS ハンドルは下のリンクで**手動確認**してから登録",
              "- **near**：似た名前がある。混同されないか人が判断する", "- **collision**：完全一致あり。`name_candidates` の次の候補で再確認する",
              "- **unverified**：照合先に届かなかった。clear ではない。別の環境で再実行する", ""]
    skipped = sorted({s for r in results for s in r["skipped"]})
    if skipped:
        lines += [f"鍵が無いため未照合：{', '.join(skipped)}（.env に SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / YOUTUBE_API_KEY を入れると自動照合）", ""]
    lines += ["## 手動確認リンク", ""]
    for r in results:
        lines.append(f"### {r['name']}")
        lines += [f"- {k}: {v}" for k, v in r["manual"].items()]
        lines.append("")
    (args.out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    n = {v: sum(1 for r in results if r["verdict"] == v) for v in ("clear", "near", "collision", "unverified")}
    print(f"\n=== 完了：clear {n['clear']} / near {n['near']} / collision {n['collision']} / unverified {n['unverified']} → {args.out.relative_to(ROOT)}/report.md ===")
    if n["unverified"]:
        errs = sorted({str(v.get("error", ""))[:60] for r in results for v in r["sources"].values() if "error" in v})
        print(f"    照合先に届きませんでした（{'; '.join(errs)[:200]}）。ネットワーク制限のない環境（手元の PC や GitHub Actions）で再実行してください")
    if n["collision"]:
        print("    collision の名前は name_candidates の次の候補に差し替えて再実行してください")
        sys.exit(2)


if __name__ == "__main__":
    main()

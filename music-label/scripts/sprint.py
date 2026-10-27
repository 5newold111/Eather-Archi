#!/usr/bin/env python3
"""
集中配信：最初の 5 組の曲を短い間にまとめて出し、売れ筋（どの場面・どの方向の曲が伸びるか）を早く見る（決定 53）

ふだんは 1 組 1 週 1 曲（制作は配信の 2 週間前から）。集中配信では、足りない曲を次の制作週から前倒しで作り、
配信日を「いちばん早い日」にする。
  ・曲数：デビュー曲を入れて、1 組 --songs 曲（既定 5）。もう作ってある毎週の曲も数に入れる
  ・場面：売れ筋を見るため、1 組の曲が同じ場面に偏らないように散らす（ライブハウス・パーティ・ひとりで聴く・ドライブ）。
    まだ出していない場面を、その組の場面の割合が高い順に。デビュー曲はドライブとして数える
  ・歌詞：Claude が全部書く（full）。ふだんの「Suno に節を書かせて合体する」手間を省く。インストの組はインストのまま
  ・前倒しした制作週は、ふだんの毎週の制作で作り直さない（ブリーフがある週は飛ばす）。そのため、
    集中配信のあとは、前倒しした週のぶん新しい曲が出ない期間ができる（その間に売れ筋を見て、次の曲に生かす）
  ・2 曲目からの DistroKid 登録は、その組の Spotify のアーティストのページがわかってから（デビュー曲と同じページにまとめるため）

使い方：
  python3 scripts/sprint.py plan              # 何曲・どの週・どの場面で作るかを見るだけ
  python3 scripts/sprint.py make              # ブリーフを作る（参考曲の割り当て → Claude が書く）
  python3 scripts/sprint.py status            # 組ごとの進み具合（作曲・登録・配信）
  --songs 5   1 組の曲数（デビュー曲を入れて）
  --artists declan_fay,tidewool   この組だけ
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, ROOT, SCENE_TYPES, load_artist, load_dotenv, monday_of, read_json, scene_weights, step  # noqa: E402

load_dotenv()
PY = sys.executable
S = ROOT / "scripts"
BRIEFS = OUT / "briefs"
DEBUT_SCENE = "drive"   # 最初の 5 組のデビュー曲はドライブの場面で作った
STATUS_JA = {None: "作曲待ち", "": "作曲待ち", "taken": "取り込み済み", "mastered": "仕上げ中", "waiting_cover": "ジャケット待ち",
             "ready": "登録待ち", "registered": "登録済み", "live": "配信中"}


def first_five() -> list[str]:
    import _songs
    return list(dict.fromkeys(s["slug"] for s in _songs.debut_songs()))


def songs_of(slug: str) -> list[dict]:
    import _songs
    return [s for s in _songs.all_songs() if s["slug"] == slug and not str(s["week"]).startswith("2000-")]


def scene_order(artist: dict, covered: list[str]) -> list[str]:
    """まだ出していない場面を割合の高い順に、その後ろに出した場面を割合の高い順に（何周でも）"""
    w = scene_weights(artist) or {k: 1.0 for k in SCENE_TYPES}
    ranked = sorted(w, key=lambda k: -w[k])
    fresh = [k for k in ranked if k not in covered]
    return fresh + [k for k in ranked if k in covered]


def plan(songs: int, slugs: list[str]) -> list[dict]:
    """[{slug, artist, have, need, items:[{week, scene}]}]"""
    this = monday_of(date.today())
    out = []
    for slug in slugs:
        a = load_artist(slug)
        have = songs_of(slug)
        covered = [DEBUT_SCENE if s["kind"] == "debut" else ((s.get("scene") or {}).get("type") or "") for s in have]
        need = max(0, songs - len(have))
        used = {s["week"] for s in have if s["kind"] == "weekly"}
        order = scene_order(a, covered)
        items, w = [], this
        for i in range(need):
            while w.isoformat() in used or (BRIEFS / f"{w}_{slug}.json").exists():
                w += timedelta(weeks=1)
            items.append({"week": w.isoformat(), "scene": order[i % len(order)]})
            used.add(w.isoformat())
        out.append({"slug": slug, "artist": a.get("name", slug), "have": have, "need": need, "items": items,
                    "instrumental": (a.get("lyrics") or {}).get("mode") == "instrumental"})
    return out


def show_plan(p: list[dict], songs: int) -> None:
    print(f"▶ 集中配信の計画（1 組 {songs} 曲。デビュー曲を入れて）")
    for r in p:
        have = "・".join(f"{s['title'] or s['id']}（{'デビュー' if s['kind'] == 'debut' else SCENE_TYPES.get((s.get('scene') or {}).get('type'), {}).get('name', '場面なし')}）"
                         for s in r["have"])
        print(f"\n■ {r['artist']}：いま {len(r['have'])} 曲 → あと {r['need']} 曲" + (f"\n   いまある曲：{have}" if have else ""))
        for it in r["items"]:
            print(f"   ＋ 制作週 {it['week']} の枠を前倒し・場面：{SCENE_TYPES[it['scene']]['name']}")
    total = sum(r["need"] for r in p)
    print(f"\n合計 {total} 曲を新しく作ります。Suno のダウンロードは 1 曲 1 本なら {total} 本（Premier は月 60 本まで）")


def release_at_asap(day: date) -> str:
    """配信日時（その日の 17:00 ET を UTC で）。SNS の投稿や楽曲管理シートが使う"""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    t = datetime.combine(day, time(17, 0), tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("UTC"))
    return t.isoformat().replace("+00:00", "Z")


def mark_sprint(path: Path, tag: str) -> None:
    """集中配信の曲にする。配信日時は目安（今日から 5 日後）。DistroKid に登録すると、登録した配信日に書き換わる"""
    b = read_json(path)
    b["sprint"] = {"id": tag, "release": "asap", "marked_at": date.today().isoformat()}
    b["release_at"] = release_at_asap(date.today() + timedelta(days=5))
    path.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make(p: list[dict], tag: str, dry: bool = False) -> int:
    refs = OUT / "references"
    if (S / "reference_db.py").exists():   # 先に取り込む（参考データベースの曲も解析シートとして数える）
        step("SSD の参考データベースを読んでいます（reference_db.py。SSD が無ければ前回の取り込みを使う）")
        subprocess.run([PY, S / "reference_db.py", "--quiet"])
    n = sum(1 for x in refs.glob("*.json") if not x.name.endswith(".measure.json")) if refs.exists() else 0
    if n < 11:
        sys.exit(f"[停止] 参考曲の解析シートが {n} 件しかありません（11 件以上必要）: {refs}\n"
                 "        SSD をつないで参考データベースを読めるようにしてから、もう一度実行してください")
    made = []
    for r in p:
        for s in r["have"]:   # もう作ってある毎週の曲（まだ登録していないもの）も、いちばん早い日に出す
            if s["kind"] == "weekly" and s.get("brief") and not s.get("sprint"):
                import _songs
                if _songs.state(s).get("status") not in ("registered", "live"):
                    mark_sprint(Path(s["brief"]), tag)
                    print(f"   ・{r['artist']}：作ってあった {s['week']} の曲も集中配信に入れました（配信日はいちばん早い日）")
        for it in r["items"]:
            step(f"{r['artist']}：制作週 {it['week']} の枠で、{SCENE_TYPES[it['scene']]['name']}の曲の参考曲を割り当てています")
            if dry:
                continue
            code = subprocess.run([PY, S / "select_references.py", "--artist", r["slug"], "--week", it["week"], "--force",
                                   "--scene", it["scene"], "--references", refs]).returncode
            bp = BRIEFS / f"{it['week']}_{r['slug']}.json"
            if code != 0 or not bp.exists():
                print(f"   ⚠ {r['artist']} の {it['week']} の枠を作れませんでした")
                continue
            mark_sprint(bp, tag)
            made.append((bp, r["instrumental"]))
    if dry or not made:
        return 0
    for inst in (False, True):   # 歌のある組は、歌詞を Claude が全部書く（Suno に節を書かせる手間を省く）
        group = [str(bp) for bp, i in made if i == inst]
        if group:
            step(f"Claude がブリーフを書いています（{len(group)} 曲）")
            subprocess.run([PY, S / "write_brief.py", *group, "--references", refs, *([] if inst else ["--lyrics-mode", "full"])])
    print("\n【結果】集中配信のブリーフを作りました。次は：")
    print("   1 ダッシュボードの「作曲待ちの曲を Suno に写して貼る」→ 1 曲ずつ「Create」を押し、聴いて 1 本だけダウンロード")
    print("   2 取り込み・音量・ジャケット・下書きは毎時の自動の流れで進みます（前倒しした週も）")
    print("   3 「登録を待つ曲を続けて DistroKid に入力する」→ 1 曲ずつ Continue と「登録した」→ 続けて歌詞")
    return 0


def status(slugs: list[str]) -> None:
    import _songs
    for slug in slugs:
        ss = songs_of(slug)
        a = load_artist(slug)
        print(f"■ {a.get('name', slug)}（{len(ss)} 曲）")
        for s in ss:
            st = _songs.state(s)
            sc = "デビュー" if s["kind"] == "debut" else SCENE_TYPES.get((s.get("scene") or {}).get("type"), {}).get("name", "")
            print(f"   {STATUS_JA.get(st.get('status'), st.get('status'))}　{s['title'] or s['id']}（{sc}）"
                  + (f"　配信日 {st['release_date']}" if st.get("release_date") else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="最初の 5 組の曲をまとめて作り、早く売れ筋を見る")
    ap.add_argument("cmd", choices=["plan", "make", "status"])
    ap.add_argument("--songs", type=int, default=5)
    ap.add_argument("--artists", help="この組だけ（, 区切りの slug）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    slugs = a.artists.split(",") if a.artists else first_five()
    if a.cmd == "status":
        status(slugs)
        return
    p = plan(a.songs, slugs)
    show_plan(p, a.songs)
    if a.cmd == "make":
        sys.exit(make(p, f"{date.today():%Y-%m}", a.dry_run))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
ジャケットの試し運転：実際に Claude と OpenAI を使って、新しい作り方の 6 案を作ってみる（オーナーの指示 2026-10-29。決定 125）

  ・本物の曲の状態は変えない：ブリーフを試し運転のフォルダに写して使う（選んだシーンもその写しにだけ残す）。選択・仕上げ・登録には進まない
  ・流れは本番と同じ：曲の印象的なシーン 6 つ（cover_scene.py）→ 6 案の指示文（generate_visuals.prompts_cover）→ 画像 6 枚（OpenAI）→
    候補ごとのデザインで文字入りの見本 → Claude の採点
  ・結果：out/cover_trial/<日時>/（見本の画像・一覧の画像 sheet.jpg・指示文・シーン）。ダッシュボードの /cover-trial で見られる
  ・料金の目安：画像 6 枚（OpenAI）＋シーン選びと採点（Claude）で 1 回 約 150〜300 円

使い方：
  python3 scripts/cover_trial.py              # いちばん新しい毎週の曲（曲名と歌詞があるもの）で
  python3 scripts/cover_trial.py <曲 ID>      # 曲を指定して
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT, load_artist, load_dotenv, read_json, step, write_json  # noqa: E402

load_dotenv()
TRIAL = OUT / "cover_trial"


def pick_song(sid: str | None) -> dict | None:
    import _songs
    if sid:
        return _songs.by_id(sid)
    songs = [s for s in _songs.weekly_songs() if not str(s["week"]).startswith("2000-") and s.get("title") and s.get("brief")]
    with_lyrics = [s for s in songs if str(s.get("lyrics") or "").strip() and "{{SUNO_" not in str(s.get("lyrics"))]
    pool = with_lyrics or songs
    return sorted(pool, key=lambda s: s["week"])[-1] if pool else None


def run(sid: str | None) -> int:
    import _claude
    import cover_scene
    import generate_visuals as gv
    song = pick_song(sid)
    if not song:
        print("【結果】試せる曲がありません（毎週の曲・不定期の曲で、曲名が決まっているもの）")
        return 2
    missing = [n for n, ok in (("OpenAI（画像）", bool(os.environ.get("OPENAI_API_KEY"))), ("Claude（シーン選び・採点）", _claude.available())) if not ok]
    if missing:
        print(f"【結果】鍵がありません：{'・'.join(missing)}（ダッシュボードの「鍵を入れる」から）")
        return 2
    out = TRIAL / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    bp = out / Path(song["brief"]).name   # 曲の ID と同じ名前（曲名・アーティスト名を本物から引くため）。本物のブリーフは変えない
    brief = read_json(song["brief"])
    brief.pop("cover_scenes", None)
    write_json(bp, brief)
    artist = load_artist(song["slug"], song.get("label_slug") if song.get("label_slug") not in (None, "", "drive") else None)
    print(f"▶ 試し運転：{song['artist']}「{song['title']}」（{song['id']}）。本物の曲の状態は変えません")

    step("曲の印象的なシーンを 6 つ選んでいます（Claude）")
    scenes = cover_scene.scenes_for(brief, bp, redo=True)
    brief = read_json(bp)
    print(f"   曲のテーマとイメージ：{cover_scene.song_image(brief)}")
    for i, sc in enumerate(scenes, 1):
        who = {"artist": "本人", "other": "曲の人物", "none": "人なし"}.get(sc.get("who"), "")
        ty = sc.get("type") or {}
        print(f"   {i}. [{cover_scene.SHOT_JA.get(sc.get('shot'), sc.get('shot', ''))}・{who}{('・' + sc['member']) if sc.get('member') else ''}] "
              f"{sc.get('why_ja', '')}／作り：{sc.get('look_ja', '')}／文字：{ty.get('font', '')}・{ty.get('place', '')}・{ty.get('layout', '')}")

    step("6 案の指示文を作っています（実在の CD ジャケットの作り方も 1 枚ずつ）")
    cands = gv.prompts_cover(artist, brief, bp)
    step(f"画像を {len(cands)} 枚作っています（OpenAI。1 枚 10〜60 秒）")
    errors = []
    for i, c in enumerate(cands, 1):
        print(f"【進み具合】{i}/{len(cands)}：画像", flush=True)
        f = out / f"cover_{c['no']:02d}.png"
        try:
            if gv.generate_image(c["prompt"], f):
                c["file"] = f.name
        except Exception as e:  # noqa: BLE001
            msg = getattr(e, "read", None) and e.read().decode("utf-8", "replace")[:300] or str(e)
            c["error"] = msg
            errors.append(msg)
            print(f"   ✕ {i} 枚目：{msg}")
            if len(errors) >= 3 and not any(x.get("file") for x in cands):
                print("   3 枚続けて失敗したので止めます")
                break
    made = [c for c in cands if c.get("file")]
    if not made:
        write_json(out / "candidates.json", cands)
        print("【結果】画像を作れませんでした（上の記録を見てください）")
        return 2
    step("アーティスト名と曲名を、候補ごとのデザインで入れています")
    n_tx = gv.text_previews(cands, out, artist, bp)
    try:
        step("Claude が 6 案を見て採点しています")
        crit = gv.load_criteria()
        gv.claude_score(cands, out, artist, crit, brief)
        for c in cands:
            gv.score_candidate(c, crit)
    except Exception as e:  # noqa: BLE001
        print(f"   － 採点できませんでした（{e}）")
    write_json(out / "candidates.json", cands)
    sheet(cands, out)
    print(f"【結果】試し運転ができました：画像 {len(made)} 枚・文字入り {n_tx} 枚。ダッシュボードの「試し運転の結果を見る」で見られます（{out}）")
    return 0


def sheet(cands: list[dict], out: Path) -> Path | None:
    """6 案を 1 枚に並べた一覧（番号つき）"""
    from PIL import Image, ImageDraw
    files = [out / (c.get("preview") or c.get("file")) for c in cands if c.get("file")]
    if not files:
        return None
    W = 600
    img = Image.new("RGB", (W * 3, W * ((len(files) + 2) // 3)), "white")
    d = ImageDraw.Draw(img)
    for i, f in enumerate(files):
        x, y = (i % 3) * W, (i // 3) * W
        img.paste(Image.open(f).convert("RGB").resize((W, W)), (x, y))
        d.rectangle((x + 8, y + 8, x + 44, y + 40), fill="white")
        d.text((x + 18, y + 14), str(i + 1), fill="black")
    p = out / "sheet.jpg"
    img.save(p, "JPEG", quality=88)
    return p


def latest() -> Path | None:
    runs = sorted([p for p in TRIAL.glob("*") if (p / "candidates.json").exists()]) if TRIAL.exists() else []
    return runs[-1] if runs else None


def page() -> str:
    """ダッシュボードの /cover-trial：いちばん新しい試し運転の 6 案"""
    import cover_scene
    import song_check
    run_dir = latest()
    head = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>ジャケットの試し運転</title><style>body{font-family:-apple-system,"Hiragino Sans",sans-serif;margin:0;background:#f6f3ee;color:#222}'
            'main{max-width:1100px;margin:0 auto;padding:16px}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}'
            '.c{background:#fff;border:1px solid #ddd;border-radius:10px;padding:10px}.c img{width:100%;border-radius:6px;display:block}'
            '.s{font-size:13px;color:#555;margin:6px 0}details{font-size:12px;color:#666}pre{white-space:pre-wrap}a{color:#7a5a20}'
            '@media(prefers-color-scheme:dark){body{background:#1b1a18;color:#eee}.c{background:#26241f;border-color:#3a372f}.s{color:#bbb}}</style>'
            '<main><p><a href="/">← ダッシュボードへ</a></p><h1 style="font-size:20px">ジャケットの試し運転</h1>')
    if not run_dir:
        return head + "<p>まだ試し運転をしていません。ダッシュボードの「ジャケットの試し運転」を押してください。</p></main>"
    cands = read_json(run_dir / "candidates.json")
    briefs = [p for p in run_dir.glob("*.json") if p.name != "candidates.json"]
    b = read_json(briefs[0]) if briefs else {}
    E = html.escape
    body = f"<p class='s'>{E(run_dir.name)}・曲のテーマとイメージ：{E(cover_scene.song_image(b))}</p><div class='g'>"
    for c in cands:
        f = run_dir / (c.get("preview") or c.get("file") or "")
        who = {"artist": "本人", "other": "曲の人物", "none": "人なし"}.get(c.get("who"), "")
        img = f"<img src='{song_check.furl(f)}' alt='案 {c['no']}' loading='lazy'>" if c.get("file") and f.exists() else f"<p>✕ 作れませんでした：{E(str(c.get('error', '')))[:200]}</p>"
        t = c.get("type") or {}
        fam = (cover_scene.FAMILIES.get(c.get("family") or "") or ("", ""))[1]
        body += (f"<div class='c'>{img}<p class='s'><b>案 {c['no']}{('：' + E(fam)) if fam else ''}</b>（{E(cover_scene.SHOT_JA.get(c.get('shot'), c.get('shot', '')))}・{E(who)}"
                 f"{('・' + E(c['member'])) if c.get('member') else ''}）{E(c.get('scene_ja', ''))}</p>"
                 f"<p class='s'>作り：{E(c.get('look_ja', ''))}／文字：{E(str(t.get('font', '')))}・{E(str(t.get('place', '')))}・{E(str(t.get('layout', '')))}"
                 f"{('（' + E(c['type_ja']) + '）') if c.get('type_ja') else ''}</p>"
                 + (f"<p class='s'>採点：{c['total']:.1f}　{E(str(c.get('claude_note', '')))}</p>" if isinstance(c.get("total"), (int, float)) else "")
                 + f"<details><summary>画像の指示文</summary><pre>{E(c.get('prompt', ''))}</pre></details></div>")
    return head + body + "</div></main>"


def main() -> None:
    ap = argparse.ArgumentParser(description="ジャケットの試し運転（実際に API を使う。曲の状態は変えない）")
    ap.add_argument("song", nargs="?")
    a = ap.parse_args()
    sys.exit(run(a.song))


if __name__ == "__main__":
    main()

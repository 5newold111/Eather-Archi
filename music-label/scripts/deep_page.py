"""
ダッシュボードの「深い分析」のページ（決定 180）

  /deep            … 深く測った参考曲の一覧（BPM・キー・進行・音の多さ・盛り上がり）
  /deep?id=aud-…   … 1 曲の中身：盛り上がりの曲線・コード進行・ドラムの型・楽器の出入り・ベース・ボーカル・音づくり・再現カード
"""
from __future__ import annotations

import json

import _ui
from _ui import E

TOOLBAR = _ui.btn({"action": "ref_deep", "label": "まだの曲を深く測る（30 曲まで・無料）"}, primary=True)


def _songs() -> list[tuple[str, dict]]:
    import reference_db
    out = []
    for p in sorted(reference_db.AUDIO_CACHE.glob("aud-*.json")) if reference_db.AUDIO_CACHE.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append((p.stem, d))
    return out


def _title(d: dict) -> str:
    r = d.get("reference") or {}
    return f"{r.get('artist_name') or '?'} - {r.get('title') or '?'}"


def status() -> dict:
    songs = _songs()
    return {"total": len(songs), "done": sum(1 for _, d in songs if d.get("deep")),
            "with_parts": sum(1 for _, d in songs if ((d.get("deep") or {}).get("instruments") or {}).get("parts"))}


def _curve(per_bar: list[float], sections: list[dict]) -> str:
    """盛り上がりの曲線（小節ごと 0〜100）。区切りの境目に細い線と名前"""
    if len(per_bar) < 2:
        return ""
    w, h, pad = 640, 130, 18
    n = len(per_bar)
    xs = [pad + i * (w - 2 * pad) / (n - 1) for i in range(n)]
    ys = [h - 22 - v / 100 * (h - 40) for v in per_bar]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = f"{pad},{h - 22} " + pts + f" {w - pad},{h - 22}"
    total = sum(max(0, s.get("bars") or 0) for s in sections) or 1
    marks, acc = [], 0
    for s in sections:
        x = pad + acc / total * (w - 2 * pad)
        marks.append(f'<line x1="{x:.1f}" y1="8" x2="{x:.1f}" y2="{h - 22}" stroke="var(--line)" stroke-width="1"/>'
                     f'<text x="{x + 3:.1f}" y="{h - 8}" font-size="10" fill="var(--sub)">{E(str(s.get("name"))[:10])}</text>')
        acc += max(0, s.get("bars") or 0)
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="盛り上がりの曲線" style="width:100%;height:auto;display:block">'
            f'{"".join(marks)}<polygon points="{area}" fill="var(--accent)" opacity=".14"/>'
            f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>'
            f'<line x1="{pad}" y1="{h - 22}" x2="{w - pad}" y2="{h - 22}" stroke="var(--line)"/></svg>')


def _grid_row(name: str, pat: str) -> str:
    cells = []
    for i, c in enumerate(pat):
        style = {"x": "background:var(--accent)", "o": "background:var(--accent);opacity:.4", "?": "border-style:dashed"}.get(c, "")
        gap = "margin-left:6px;" if i and i % 4 == 0 else ""
        cells.append(f'<i style="display:inline-block;width:14px;height:14px;border:1px solid var(--line);border-radius:3px;{gap}{style}"></i>')
    return f'<div class="row" style="gap:2px 8px"><span class="sub" style="width:6.5em">{E(name)}</span><span style="display:inline-flex;gap:2px">{"".join(cells)}</span></div>'


def _facts(rows: list[tuple[str, object]]) -> str:
    rows = [(k, v) for k, v in rows if v not in (None, "", [], {})]
    return _ui.table(["項目", "中身"], [[k, v] for k, v in rows]) if rows else '<p class="empty">まだありません</p>'


def _detail(rid: str, d: dict) -> str:
    dp = d.get("deep") or {}
    g, h, den, t, mx, dr = (dp.get(k) or {} for k in ("groove", "harmony", "density", "tension", "mix", "drums"))
    ins, bs, vo, imp, cd = (dp.get(k) or {} for k in ("instruments", "bass", "vocal", "impression", "card"))
    secs = (dp.get("structure") or {}).get("sections") or []
    body = [f'<h1>{E(_title(d))}</h1><p class="sub"><a href="/deep">← 一覧へ</a>　測り方：{E(str(dp.get("source", "")))}</p>',
            _ui.kpis([(g.get("bpm", "—"), "BPM", g.get("tempo_drift", "")), (h.get("key", "—"), "キー", f"確からしさ {h.get('key_confidence', '—')}"),
                      (g.get("time_signature", "—"), "拍子", f"{g.get('bars', '—')} 小節"), (den.get("onsets_per_bar", "—"), "1 小節の音の数", den.get("level", "")),
                      (t.get("average", "—"), "盛り上がりの平均", f"最高 {t.get('peak', '—')}・{t.get('peak_section', '')}")])]
    body.append(f'<section class="card"><h2>印象</h2><p>{E("・".join(imp.get("words_ja") or []) or "—")}</p>'
                + (f'<p class="sub">曲を聞いて判定したムード：{E(str(imp["mood_from_tags"]))}</p>' if imp.get("mood_from_tags") else "") + "</section>")
    rise, drop = t.get("biggest_rise") or {}, t.get("biggest_drop") or {}
    body.append('<section class="card"><h2>盛り上がり（テンション）</h2>' + _curve(t.get("per_bar") or [], secs)
                + f'<p>{E(str(t.get("shape", "")))}</p><p class="sub">音量・音の明るさ・音の多さ・和音の緊張を合わせて、小節ごとに 0〜100 で表しています。'
                + (f'いちばん大きく上がる所：{E(str(rise.get("into")))}（{rise.get("at_sec")} 秒・+{rise.get("by")}）。' if rise else "")
                + (f'いちばん大きく下がる所：{E(str(drop.get("into")))}（{drop.get("at_sec")} 秒・{drop.get("by")}）。' if drop else "") + "</p>"
                + (_ui.table(["区切り", "小節", "盛り上がり", "1 小節の音の数", "音量（dB）"],
                             [[s.get("name"), s.get("bars"), s.get("tension"), s.get("onsets_per_bar"), s.get("loudness_db")] for s in secs], num=(1, 2, 3, 4)) if secs else "")
                + "</section>")
    lp = h.get("loop") or {}
    body.append('<section class="card"><h2>コード進行（和声）</h2>' + _facts([
        ("進み方", h.get("progression_character")), ("繰り返しの型（度数）", " | ".join(lp.get("pattern") or [])),
        ("コードが変わる速さ", f"{h.get('harmonic_rhythm')}（1 小節に {h.get('chords_per_bar')} 回）"),
        ("よく使うコード", "　".join(f"{x['chord']} {int(x['share'] * 100)}%" for x in h.get("vocabulary") or [])),
        ("キーの外のコード", "・".join(h.get("outside_key") or [])),
        ("転調", "・".join(f"{x['section']}（{x['at_sec']} 秒）で {x['to']} へ" for x in h.get("modulations") or []) or "なし"),
        ("読みについて", h.get("key_note"))])
        + (_ui.table(["区切り", "最初の 8 小節（度数）"], [[s.get("name"), " | ".join(s.get("progression") or [])] for s in h.get("sections") or []]) if h.get("sections") else "")
        + '<p class="sub">度数（I・IV・V など）は、キーの主音から数えたコードの位置です。大文字は明るい和音（長和音）、小文字は暗い和音（短和音）。</p></section>')
    if dr.get("pattern"):
        p = dr["pattern"]
        body.append('<section class="card"><h2>ドラムの型（1 小節）</h2>' + _grid_row("ハイハット", p["hat"]) + _grid_row("スネア", p["snare"]) + _grid_row("キック", p["kick"])
                    + f'<p>{E("・".join(dr.get("labels") or []))}</p><p class="sub">濃い四角＝ほぼ毎回鳴る・薄い四角＝ときどき・点線＝読めていない。拾い方：{E(str(dr.get("from", "")))}</p></section>')
    if ins.get("parts"):
        body.append('<section class="card"><h2>楽器の構成と出入り</h2>'
                    + _ui.table(["楽器", "鳴っている割合", "入る所", "1 小節の音の数", "音域"],
                                [[x["name"], f"{int(x['active'] * 100)}%", f"{x.get('enters_in', '')}（{x.get('enters_sec')} 秒）", x.get("notes_per_bar"), x.get("range", "")]
                                 for x in ins["parts"]], num=(1, 3))
                    + _ui.table(["区切り", "鳴っている楽器", "入ってくる", "抜ける"],
                                [[r["section"], "・".join(r.get("playing") or []), "・".join(r.get("added") or []), "・".join(r.get("dropped") or [])] for r in ins.get("timeline") or []])
                    + f'<p class="sub">同時に鳴る楽器の数：平均 {(ins.get("layers") or {}).get("average")}・最大 {(ins.get("layers") or {}).get("max")}。'
                      f'曲を聞いて判定した名札：{E("・".join(str(x) for x in ins.get("listed") or []) or "—")}</p></section>')
    else:
        body.append(f'<section class="card"><h2>楽器の構成と出入り</h2><p class="empty">{E(str(ins.get("from", "まだ測っていません")))}</p></section>')
    two = []
    if bs.get("usable"):
        two.append('<section class="card"><h2>ベース</h2>' + _facts([("動き方", bs.get("motion")), ("音の長さ", bs.get("articulation")), ("キックとの関係", bs.get("kick_bass_relation")),
                                                                  ("音域", bs.get("range")), ("1 小節の音の数", bs.get("notes_per_bar"))]) + "</section>")
    if vo.get("usable"):
        two.append('<section class="card"><h2>ボーカル</h2>' + _facts([("歌っている割合", f"{int(vo['active'] * 100)}%"), ("入る所", f"{vo.get('enters_in')}（{vo.get('enters_sec')} 秒）"),
                                                                    ("重ね方", vo.get("layers")), ("音域", vo.get("range")), ("いちばん高い音", vo.get("highest_in")),
                                                                    ("いちばん長い音", f"{vo.get('longest_note_in')}（{vo.get('longest_note_sec')} 秒）")]) + "</section>")
    two.append('<section class="card"><h2>音づくり</h2>' + _facts([("低音〜高音", mx.get("tone")), ("音量の幅", mx.get("dynamics")), ("左右の広がり", mx.get("stereo"))]) + "</section>")
    body.append(f'<div class="grid2">{"".join(two)}</div>')
    if dp.get("score_file"):
        body.append(f'<section class="card"><h2>楽譜</h2><p>旋律・コード名・ベースの楽譜を書き出しています：<span class="mono">{E(str(dp["score_file"]))}</span></p>'
                    '<p class="sub">MusicXML という楽譜の形です。無料の MuseScore で開くと五線譜になります。自動の書き起こしなので、下書きとして見てください。</p></section>')
    if cd.get("style"):
        body.append('<section class="card"><h2>再現カード（分析の答え合わせ用）</h2>'
                    f'<label class="field">スタイル欄に入る形（英語・{cd.get("style_chars")} 文字）<textarea id="card-style" readonly>{E(cd["style"])}</textarea></label>'
                    f'<div class="row">{_ui.copy_btn("card-style")}</div>'
                    f'<label class="field">区切りごとの指示<textarea id="card-struct" readonly style="min-height:9em">{E(chr(10).join(cd.get("structure") or []))}</textarea></label>'
                    f'<div class="row">{_ui.copy_btn("card-struct")}</div>'
                    f'<p class="sub">{E(str(cd.get("note", "")))}</p></section>')
    return "".join(body)


def page(token: str, rid: str | None = None) -> str:
    songs = _songs()
    if rid:
        d = next((d for k, d in songs if k == rid), None)
        if d and d.get("deep"):
            return _ui.page("深い分析", _detail(rid, d), token, "/deep")
    done = [(k, d) for k, d in songs if d.get("deep")]
    st = status()
    rows = []
    for k, d in sorted(done, key=lambda kd: _title(kd[1]).casefold()):
        dp = d["deep"]
        g, h, den, t = (dp.get(x) or {} for x in ("groove", "harmony", "density", "tension"))
        lp = h.get("loop") or {}
        rows.append([f'<a href="/deep?id={E(k)}">{E(_title(d))}</a>', g.get("bpm"), h.get("key"), " | ".join(lp.get("pattern") or []) or "—",
                     den.get("level", ""), t.get("shape", ""), len((dp.get("instruments") or {}).get("parts") or []) or "—"])
    body = ('<h1>深い分析</h1><p class="sub">参考曲（既存楽曲）を、コード進行・楽器の出入り・ドラムの型・音の多さ・盛り上がりの面から測った結果です。'
            "曲作りには、枠ごとにその枠の部分だけが渡ります（1 曲 1 枠）。</p>"
            + _ui.kpis([(st["done"], "深く測った曲", f"全 {st['total']} 曲"), (st["with_parts"], "楽器ごとまで測れた曲", "メロディーの書き起こし（MIDI）がある曲"),
                        (st["total"] - st["done"], "まだの曲", "30 分ごとに 6 曲ずつ自動で進みます")])
            + f'<section class="card"><div class="row">{TOOLBAR}</div></section>'
            + '<section class="card"><h2>測った曲</h2>'
            + (_ui.table(["曲", "BPM", "キー", "繰り返しの型", "音の多さ", "盛り上がりの形", "楽器の数"], rows, num=(1, 6), raw=(0,)) if rows
               else '<p class="empty">まだ測った曲がありません。SSD をつないで「まだの曲を深く測る」を押すと始まります。</p>')
            + "</section>")
    return _ui.page("深い分析", body, token, "/deep", auto=not rows)

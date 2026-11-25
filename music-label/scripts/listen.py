#!/usr/bin/env python3
"""
仕上げた音を聴いて確かめる（配信前の作業の 1 つ。決定 169）

  公開の準備ができた曲（ready）は、Gon さんが「この仕上げで進める」を押すまで、DistroKid には入力しない。
  仕上げ直すと音が変わるので、確かめた印は外れる（確かめた音と登録する音が必ず同じになる）。

  ページ /listen       … 確かめるのを待っている曲の一覧
        /listen?id=…  … 1 曲：仕上げ前／仕上げ後を同じ位置で切り替えて聴く（同じ音量にそろえてある）・した手当ての一覧・
                         確かめること・「この仕上げで進める」・味付けと強さを変えて「仕上げ直す」・「Suno で作曲に戻す」

使い方
  python3 scripts/listen.py ok <曲 ID> [--note メモ]   # 確かめた（ダッシュボードの「この仕上げで進める」）
  python3 scripts/listen.py undo <曲 ID>               # 確かめた印を外す
  python3 scripts/listen.py need <曲 ID|all>           # まだ確かめていない曲を知らせる（DistroKid の入力の前に呼ばれる）
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT  # noqa: E402

import _songs  # noqa: E402

E = html.escape
CHECKS = [("voice", "声が不自然でない（ロボットっぽさ・急な声変わりが無い）"),
          ("words", "歌詞が聞き取れる・変な言葉になっていない"),
          ("noise", "ノイズ・途切れ・音割れが無い"),
          ("edges", "始まりと終わりが自然（急に切れない・無音が長くない）"),
          ("balance", "低い音がこもらない・高い音が耳に痛くない")]


def master_path(song: dict) -> Path:
    return OUT / "masters" / song["key"] / "master.wav"


def master_sig(song: dict) -> str:
    p = master_path(song)
    if not p.exists():
        return ""
    st = p.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def approved(song: dict) -> bool:
    """いまの音源を、聴いて確かめてあるか"""
    li = _songs.state(song).get("listen") or {}
    return bool(li.get("ok")) and li.get("sig") == master_sig(song) and bool(li.get("sig"))


def needed(song: dict) -> bool:
    """DistroKid に進む前に、聴いて確かめるのを待っているか"""
    return _songs.state(song).get("status") == "ready" and not approved(song)


def approve(song: dict, note: str = "", checks: list[str] | None = None) -> None:
    st = _songs.state(song)
    st["listen"] = {"ok": True, "at": datetime.now().isoformat(timespec="seconds"), "sig": master_sig(song),
                    "note": note[:500], "checks": [c for c in (checks or []) if c in dict(CHECKS)]}
    _songs.save_state(song, st)


def revoke(song: dict) -> None:
    st = _songs.state(song)
    if st.pop("listen", None) is not None:
        _songs.save_state(song, st)


def todos() -> list[dict]:
    """聴いて確かめるのを待っている曲（ダッシュボードの「あなたの番」に出す）"""
    out = []
    for s in _songs.all_songs():
        if needed(s):
            out.append({"id": s["id"], "slug": s["slug"], "artist": s["artist"], "title": s["title"],
                        "what": "仕上げた音を聴いて確かめる", "url": f"/listen?id={s['id']}"})
    return out


def status() -> dict:
    t = todos()
    return {"waiting": len(t), "todos": t}


# ---------------------------------------------------------------------------
# ページ
PLAYER_CSS = """
.ab{display:grid;gap:10px}
.ab .sw{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.ab .sw button{border:0;border-radius:0;padding:12px 8px;font-size:15px;background:var(--card);color:var(--sub)}
.ab .sw button.on{background:var(--accent);color:var(--accent-ink);font-weight:700}
.ab .ctl{display:flex;gap:10px;align-items:center}
.ab .ctl button{min-width:5.5em} .ab input[type=range]{flex:1;min-width:0;padding:0}
.ab .t{font-variant-numeric:tabular-nums;font-size:13px;color:var(--sub);white-space:nowrap}
.moves li{margin:3px 0} .moves b{font-weight:600}
.checks label{display:flex;gap:8px;align-items:flex-start;padding:4px 0;font-size:14px}
.nums td.n{font-family:ui-monospace,Menlo,monospace}
"""

PLAYER_JS = """
(function(){const A=document.getElementById("au-after"),B=document.getElementById("au-before");if(!A)return;
 const seek=document.getElementById("ab-seek"),tm=document.getElementById("ab-time"),play=document.getElementById("ab-play");
 let cur=A;const fmt=s=>isFinite(s)?Math.floor(s/60)+":"+String(Math.floor(s%60)).padStart(2,"0"):"0:00";
 function show(){document.getElementById("ab-a").classList.toggle("on",cur===A);const b=document.getElementById("ab-b");if(b)b.classList.toggle("on",cur===B)}
 function sw(to){if(!to||to===cur)return;const t=cur.currentTime,playing=!cur.paused;cur.pause();to.currentTime=Math.min(t,(to.duration||t));cur=to;if(playing)cur.play();show()}
 document.getElementById("ab-a").onclick=()=>sw(A);const bb=document.getElementById("ab-b");if(bb)bb.onclick=()=>sw(B);
 play.onclick=()=>{cur.paused?cur.play():cur.pause()};
 for(const a of [A,B]){if(!a)continue;a.addEventListener("timeupdate",()=>{if(a!==cur)return;seek.max=a.duration||0;seek.value=a.currentTime;tm.textContent=fmt(a.currentTime)+" / "+fmt(a.duration)});
   a.addEventListener("play",()=>{if(a===cur)play.textContent="止める"});a.addEventListener("pause",()=>{if(a===cur)play.textContent="再生"});
   a.addEventListener("ended",()=>{play.textContent="再生"})}
 seek.oninput=()=>{cur.currentTime=+seek.value};
 document.getElementById("ab-back").onclick=()=>{cur.currentTime=Math.max(0,cur.currentTime-10)};
 document.getElementById("ab-fwd").onclick=()=>{cur.currentTime=Math.min(cur.duration||0,cur.currentTime+10)};
 document.addEventListener("keydown",e=>{if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;
   if(e.code==="Space"){e.preventDefault();play.onclick()}else if(e.key==="a"||e.key==="A")sw(A);else if((e.key==="b"||e.key==="B")&&B)sw(B)});
 show()})();
"""


def _player(song: dict, rep: dict) -> str:
    import song_check
    d = master_path(song).parent
    after = d / "preview_after.m4a" if (d / "preview_after.m4a").exists() else master_path(song)
    before = d / "preview_before.m4a" if (d / "preview_before.m4a").exists() else None
    matched = before is not None
    if before is None:
        try:
            import mastering
            before = mastering.source_of(song)
        except Exception:  # noqa: BLE001
            before = None
    b_btn = '<button id="ab-b" type="button">仕上げ前（B）</button>' if before else ""
    b_audio = f'<audio id="au-before" preload="auto" src="{song_check.furl(before)}"></audio>' if before else ""
    note = ("仕上げ前は、仕上げ後と同じ音量にそろえてあります（大きい音のほうが良く聞こえるのを防ぐため）。" if matched else
            "仕上げ前は元の音量のままです（音量が違うので、大きいほうが良く聞こえやすい点に気をつけてください）。" if before else "")
    return (f'<div class="ab"><div class="sw" style="grid-template-columns:{"1fr 1fr" if before else "1fr"}">'
            f'<button id="ab-a" type="button" class="on">仕上げ後（A）</button>{b_btn}</div>'
            f'<div class="ctl"><button id="ab-play" type="button" class="primary">再生</button><button id="ab-back" type="button">← 10 秒</button>'
            f'<button id="ab-fwd" type="button">10 秒 →</button><span class="t" id="ab-time">0:00 / 0:00</span></div>'
            f'<input type="range" id="ab-seek" min="0" max="0" step="0.1" value="0" aria-label="再生位置">'
            f'<audio id="au-after" preload="auto" src="{song_check.furl(after)}"></audio>{b_audio}'
            f'<p class="sub">同じ位置のまま切り替わります。キーボード：スペース＝再生／止める、A＝仕上げ後、B＝仕上げ前。{note}</p></div>')


def _report_html(rep: dict) -> str:
    import _ui
    if not rep:
        return '<p class="empty">仕上げの記録がありません。</p>'
    try:
        import mastering
        style = mastering.STYLES.get(rep.get("style", ""), "音量だけ（前の作り方）")
        strength = mastering.STRENGTHS.get(rep.get("strength", ""), "")
    except Exception:  # noqa: BLE001
        style, strength = rep.get("style", ""), rep.get("strength", "")
    moves = "".join(f"<li><b>{E(m['what'])}</b>：{E(m['why'])}" + (f"（{E(m['amount'])}）" if m.get("amount") else "") + "</li>"
                    for m in rep.get("moves", []))
    b, a = rep.get("before", {}), rep.get("after", {})
    rows = []
    for key, name, unit, tip in (("lufs", "音量", "LUFS", "配信サービスの基準は -14"), ("true_peak_db", "本当の最大値", "dBTP", "-1 以下なら配信側で割れない"),
                                 ("crest_db", "最大値と平均の差", "dB", "小さいほど強くつぶれている"), ("harsh_db", "耳に痛い高音の量", "dB", "小さいほどやわらかい"),
                                 ("low_side_db", "左右に散った低音", "dB", "小さいほど低音がまとまっている"), ("length_sec", "長さ", "秒", "")):
        bv = b.get(key, b.get("input_i") if key == "lufs" else b.get("input_tp") if key == "true_peak_db" else None)
        if a.get(key) is None and bv is None:
            continue
        rows.append([name, "—" if bv is None else f"{bv} {unit}", "—" if a.get(key) is None else f"{a.get(key)} {unit}", tip])
    ok = _ui.badge("done", "✓ 目標どおり") if rep.get("ok") else _ui.badge("ng", "！ 目標から外れています")
    return (f'<p>味付け：<b>{E(style)}</b>' + (f"・強さ：<b>{E(strength)}</b>" if strength else "") + f'　{ok}</p>'
            + (f'<h3>した手当て</h3><ul class="moves">{moves}</ul>' if moves else "")
            + '<h3>測った値（前 → 後）</h3><div class="nums">' + _ui.table(["", "仕上げ前", "仕上げ後", "見方"], rows, num=(1, 2)) + "</div>")


def song_page(song: dict, token: str) -> str:
    import _ui
    import song_check
    try:
        import mastering
        rep, outdated = mastering.report_of(song), mastering.outdated(song)
        d = mastering.defaults(song["slug"])
        styles, strengths = mastering.STYLES, mastering.STRENGTHS
        tools = mastering.need_tools()
        learned = mastering.learn_status()
    except Exception as e:  # noqa: BLE001
        rep, outdated, d, styles, strengths, tools, learned = {}, False, {"style": "auto", "strength": "mid"}, {}, {}, str(e), {"n": 0}
    st = _songs.state(song)
    status = st.get("status", "")
    sid = song["id"]
    head = f'<h1>{E(song["artist"])}「{E(song["title"])}」</h1>'
    if not master_path(song).exists():
        return _ui.page("聴いて確かめる", head + '<section class="card"><p class="empty">まだ仕上げた音源がありません（取り込みと仕上げが終わると、ここで聴けます）。</p></section>',
                        token, active="/listen")
    locked = status in ("registered", "live")
    cover = _songs.cover_source(song)
    if approved(song):
        li = st["listen"]
        state = _ui.badge("done", f"✓ 確かめた（{li.get('at', '')[:16].replace('T', ' ')}）")
    elif locked:
        state = _ui.badge("info", "DistroKid に登録済み")
    else:
        state = _ui.badge("wait", "○ 確かめるのを待っています")
    player = (f'<section class="card"><h2>聴き比べ {state}</h2>'
              + (f'<div class="row" style="align-items:flex-start"><img class="cover" style="width:120px" src="{song_check.furl(cover)}" alt="ジャケット">'
                 f'<div style="flex:1;min-width:240px">{_player(song, rep)}</div></div>' if cover else _player(song, rep)) + "</section>")
    checks = "".join(f'<label><input type="checkbox" id="ck-{k}"> {E(t)}</label>' for k, t in CHECKS)
    if locked:
        decide = '<section class="card"><h2>この曲はもう登録済みです</h2><p class="sub">登録した音と変わってしまうので、仕上げ直しはできません。</p></section>'
    else:
        ok_btn = _ui.btn({"action": "listen_ok", "id": sid, "label": "この仕上げで進める（DistroKid に登録できるようになります）"}, primary=True,
                         fields={**{k: f"ck-{k}" for k, _ in CHECKS}, "note": "ln-note"})
        undo = _ui.btn({"action": "listen_undo", "id": sid, "label": "確かめた印を外す"}, cls="ghost") if approved(song) else ""
        decide = (f'<section class="card"><h2>確かめること</h2><div class="checks">{checks}</div>'
                  f'<label class="field" style="margin-top:8px">気づいたこと（任意）<textarea id="ln-note" placeholder="例：2 番のサビで声が少しかすれる">{E((st.get("listen") or {}).get("note", ""))}</textarea></label>'
                  f'<div class="row" style="margin-top:10px">{ok_btn}{undo}</div>'
                  '<p class="sub">チェックは目安です（全部に付けなくても進められます）。気になる所があれば、下で味付けを変えて仕上げ直すか、Suno で作り直します。</p></section>')
    sel_style = "".join(f'<option value="{E(k)}"{" selected" if k == (rep.get("style") or d["style"]) else ""}>{E(v)}</option>'
                        for k, v in styles.items() if k != "ref")
    sel_str = "".join(f'<option value="{E(k)}"{" selected" if k == (rep.get("strength") or d["strength"]) else ""}>{E(v)}</option>' for k, v in strengths.items())
    if locked:
        redo = ""
    elif tools:
        redo = f'<section class="card"><h2>仕上げ直す</h2><p class="sub">{E(tools)}</p></section>'
    else:
        tgt = (f"目標：手元の市販曲 {learned['n']} 曲の平均" + ("（この組が影響を受けた人の曲があれば、そちらを優先）" if learned.get("acts") else "")
               if learned.get("n", 0) >= 12 else "目標：組み込みの形（「既存楽曲」から学ぶと、手元の市販曲の音のバランスを目標にできます）")
        redo = ('<section class="card"><h2>味付けを変えて仕上げ直す</h2>'
                + ('<p>' + _ui.badge("wait", "前の作り方（音量だけ）の仕上げです") + ' 新しい仕上げに直せます。</p>' if outdated else "")
                + f'<div class="row"><label class="field">味付け<select id="ms-style">{sel_style}</select></label>'
                f'<label class="field">強さ<select id="ms-strength">{sel_str}</select></label>'
                f'<label style="display:flex;gap:6px;align-items:center;font-size:13px"><input type="checkbox" id="ms-save"> この組の既定にする</label></div>'
                '<div class="row" style="margin-top:10px">'
                + _ui.btn({"action": "remaster", "id": sid, "label": "仕上げ直す（1〜2 分）"}, fields={"style": "ms-style", "strength": "ms-strength", "save": "ms-save"})
                + _ui.btn({"action": "master_learn", "label": "「既存楽曲」から目標を学ぶ",
                           "confirm": "SSD の「既存楽曲」の曲を 1 曲ずつ測って、音のバランスの目標にします（1 曲 数秒・料金なし。測った曲は次から飛ばします）。始めますか？"}, cls="ghost")
                + _ui.btn({"action": "back", "id": sid, "to": "suno", "label": "← Suno で作曲に戻す",
                           "confirm": "取り込んだ曲・下書き・公開セットを「戻したもの」フォルダに移して、Suno で作曲の段階に戻します（消しません）。よいですか？"}, cls="ghost")
                + f'</div><p class="sub">{E(tgt)}。おまかせ＝曲を測って要る手当てだけ／あたたかく＝高音を控えめに／明るく・クリア＝高音の空気感／'
                '太く・力強く＝低音の押しと厚み／そのまま＝掃除と音量だけ。仕上げ直すと、もう一度聴いて確かめます。</p></section>')
    report = f'<section class="card"><h2>この仕上げでしたこと</h2>{_report_html(rep)}</section>'
    body = (head + f'<p class="sub"><a href="/listen">← 一覧へ</a>　<a href="/song/{E(sid)}">公開の準備を見る</a></p>'
            + player + f'<div class="grid2">{decide}{redo}</div>' + report)
    return _ui.page("聴いて確かめる", body, token, active="/listen", head=f"<style>{PLAYER_CSS}</style>", script=PLAYER_JS)


def index_page(token: str) -> str:
    import _ui
    rows_wait, rows_done = [], []
    for s in _songs.all_songs():
        st = _songs.state(s)
        if not master_path(s).exists() or str(s["week"]).startswith("2000-"):
            continue
        link = f'<a href="/listen?id={E(s["id"])}">{E(s["artist"])}「{E(s["title"])}」</a>'
        try:
            import mastering
            rep = mastering.report_of(s)
            how = mastering.STYLES.get(rep.get("style", ""), "音量だけ（前の作り方）")
        except Exception:  # noqa: BLE001
            how = ""
        if needed(s):
            rows_wait.append([link, how, _ui.badge("wait", "○ 聴いて確かめる")])
        elif st.get("status") in ("ready", "registered", "live"):
            lab = {"ready": ("done", "✓ 確かめた"), "registered": ("info", "登録済み"), "live": ("info", "配信中")}[st["status"]]
            rows_done.append([link, how, _ui.badge(*lab)])
    body = ('<h1>聴いて確かめる</h1><p class="sub">公開の準備ができた曲は、仕上げた音を聴いて「この仕上げで進める」を押すと、DistroKid に登録できるようになります。</p>'
            '<section class="card"><h2>確かめるのを待っている曲</h2>'
            + (_ui.table(["曲", "味付け", "状態"], rows_wait, raw=(0, 2)) if rows_wait else '<p class="empty">いまはありません。曲を取り込むと、仕上げのあとにここへ並びます。</p>')
            + '</section>'
            + (f'<section class="card"><h2>済んだ曲</h2>{_ui.table(["曲", "味付け", "状態"], rows_done[-30:], raw=(0, 2))}</section>' if rows_done else ""))
    return _ui.page("聴いて確かめる", body, token, active="/listen", auto=bool(rows_wait))


def page(token: str, sid: str | None = None) -> str:
    song = _songs.by_id(sid) if sid else None
    return song_page(song, token) if song else index_page(token)


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="仕上げた音を聴いて確かめる（記録）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("ok")
    o.add_argument("id")
    o.add_argument("--note", default="")
    o.add_argument("--checks", default="")
    sub.add_parser("undo").add_argument("id")
    sub.add_parser("need").add_argument("id")
    a = ap.parse_args()
    if a.cmd == "need":
        songs = [s for s in _songs.all_songs() if needed(s)] if a.id == "all" else [s for s in [_songs.by_id(a.id)] if s and needed(s)]
        if not songs:
            return
        for s in songs:
            print(f"   ○ {s['artist']}「{s['title']}」：まだ聴いて確かめていません")
        print("【結果】先に、仕上げた音を聴いて確かめてください（ダッシュボードの「聴いて確かめる」→「この仕上げで進める」）。確かめた曲だけ DistroKid に入力します")
        sys.exit(2)
    song = _songs.by_id(a.id)
    if song is None:
        print(f"【結果】曲が見つかりません：{a.id}")
        sys.exit(1)
    if a.cmd == "ok":
        if not master_path(song).exists():
            print("【結果】仕上げた音源がまだありません")
            sys.exit(1)
        approve(song, a.note, [c for c in a.checks.split(",") if c])
        print(f"【結果】{song['artist']}「{song['title']}」を聴いて確かめた、と記録しました。DistroKid に登録できます")
    else:
        revoke(song)
        print(f"【結果】{song['artist']}「{song['title']}」の確かめた印を外しました")


if __name__ == "__main__":
    main()

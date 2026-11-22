"""
ダッシュボードの /keys「鍵と見守り」（決定 174）

  ・見守り：Mac の合図・スマホへの知らせ・R2 の予備保管・鍵の金庫・復旧キットの状態
  ・鍵：サービスごとに、入っているか（○ / －）と、入れ直す欄（値は画面に戻さない）・「接続を確かめる」
  ・Mac が壊れたときの戻し方

  鍵を入れる欄は /api/keys/save に送る（webapp.py。この Mac 自身か Tailscale からだけ受け取る）。
"""
from __future__ import annotations

import html
from datetime import datetime

import _ui
import alert
import vault

E = html.escape
MARK = {"ok": ("done", "○ つながりました"), "ng": ("ng", "✕ つながりません"), "none": ("info", "－ 未設定")}


def _ago(iso: str) -> str:
    if not iso:
        return "まだ"
    try:
        d = datetime.now() - datetime.fromisoformat(iso)
    except ValueError:
        return iso
    m = int(d.total_seconds() // 60)
    if m < 1:
        return "たった今"
    if m < 60:
        return f"{m} 分前"
    if m < 60 * 48:
        return f"{m // 60} 時間前"
    return f"{m // 1440} 日前"


def _r2_backup_at() -> str:
    from _common import OUT
    man = OUT / "backup" / "r2_manifest.json"
    return datetime.fromtimestamp(man.stat().st_mtime).isoformat(timespec="seconds") if man.exists() else ""


def watch_html(v: dict, a: dict) -> str:
    beat = ("止まっているかも" if a["stale"] else "動いています") if a["last_heartbeat"] else "まだ"
    vault_txt = "預けてあります" if v["ready"] else ("まだ作っていません" if not v["initialized"] else "預け直しが要ります")
    tiles = _ui.kpis([
        (beat, "Mac の見回り", f"最後：{_ago(a['last_heartbeat'])}" + ("" if a["healthcheck"] else "・外からの見守りは未設定")),
        ("オン" if a["ntfy"] else "未設定", "スマホへの知らせ", "ntfy" if a["ntfy"] else "下の「スマホへの知らせ」で設定"),
        (_ago(_r2_backup_at()), "R2 の予備保管", "毎日 3:30"),
        (vault_txt, "鍵の金庫", f"最後：{_ago(v['last_backup'])}"),
        ("保存済み" if v["kit_saved"] else "まだ", "復旧キット", "Apple の「パスワード」アプリにメモ"),
    ])
    probs = "".join(f'<li>{_ui.badge("ng" if p.get("level") == "error" else "wait", "！")} <b>{E(p.get("title", ""))}</b>'
                    f'<br><span class="sub">{E(p.get("detail", ""))}（{E(_ago(p.get("since", "")))}から）</span></li>' for p in a["problems"])
    probs_html = f'<ul style="margin:8px 0 0;padding-left:1.2em;display:grid;gap:6px">{probs}</ul>' if probs else '<p class="empty">気になることはありません。</p>'
    buttons = '<div class="row" style="margin-top:10px">' + "".join([
        _ui.btn({"action": "vault_backup", "label": "金庫に預ける"}, primary=not v["ready"] and v["initialized"]),
        _ui.btn({"action": "terminal", "key": "vault_init", "label": "金庫を作る（ターミナルが開きます）"}, primary=True) if not v["initialized"] else "",
        _ui.btn({"action": "alert_test", "label": "スマホに試しの知らせを送る"}),
        _ui.btn({"action": "terminal", "key": "vault_kit", "label": "復旧キットを表示（ターミナルが開きます）"}, primary=v["initialized"] and not v["kit_saved"]),
        _ui.btn({"action": "alert_watch", "label": "いま見回る"}, cls="ghost"),
    ]) + "</div>"
    return (f'{tiles}<section class="card"><h2>気になること</h2>{probs_html}{buttons}'
            '<p class="sub" style="margin-top:8px">見回りは 10 分ごと。新しい問題が出たときに 1 回だけスマホに知らせ、直ったらまた知らせます。'
            "Mac が丸ごと止まったときは、外からの見守り（healthchecks.io）がメールで知らせます。</p></section>")


def service_html(key: str, svc: dict, keys: list[dict], checks: dict) -> str:
    rows = []
    for k in keys:
        iid = f"k-{k['name']}"
        state = _ui.badge("done", "○ 入っています") if k["set"] else _ui.badge("info", "－ まだ")
        kind = "password" if k["secret"] else "text"
        rows.append(
            f'<div class="row" style="margin:6px 0"><code style="min-width:14em">{E(k["name"])}</code>{state}'
            f'<input type="{kind}" id="{E(iid)}" autocomplete="new-password" spellcheck="false" placeholder="{"入れ直すときだけ貼る" if k["set"] else "ここに貼る"}" style="flex:1;min-width:12em">'
            + _ui.btn({"label": "保存", "name": k["name"]}, fields={"value": iid}, post="/api/keys/save") + "</div>")
    c = checks.get(key) or {}
    chk = ""
    if c:
        cls, txt = MARK.get(c.get("state", ""), ("info", ""))
        chk = f' {_ui.badge(cls, txt)} <span class="sub">{E(c.get("msg", ""))}（{E(_ago(c.get("at", "")))}）</span>'
    link = f' <a href="{E(svc["url"])}" target="_blank" rel="noopener" class="sub">設定のページ ↗</a>' if svc.get("url") else ""
    return (f'<section class="card"><h2>{E(svc["title"])}{link}</h2>{"".join(rows)}'
            f'<div class="row" style="margin-top:6px">{_ui.btn({"action": "keys_check", "service": key, "label": "接続を確かめる"}, cls="ghost")}{chk}</div></section>')


RECOVER = """<section class="card"><h2>Mac が壊れた・買い替えたとき</h2>
<ol style="margin:4px 0;padding-left:1.4em;display:grid;gap:4px">
<li>ターミナルで、GitHub からこの仕組みを取ってくる：<code>git clone -b claude/jolly-edison-bzgnjf https://github.com/5newold111/Eather-Archi.git ~/Eather-Archi</code> → <code>cd ~/Eather-Archi/music-label</code></li>
<li>道具と部品を入れる：<code>bash setup_mac.sh</code>（止まったら、表示された命令を流して、もう一度同じ命令。Homebrew・ffmpeg・Python・部品の置き場まで入ります）</li>
<li>ターミナルの窓を開き直して <code>cd ~/Eather-Archi/music-label</code></li>
<li>鍵を戻す：<code>python3 scripts/vault.py restore</code>（「パスワード」アプリの復旧キットを貼る）</li>
<li>記録と音源を戻す：R2 から <code>python3 scripts/restore_r2.py --dry-run</code> → <code>python3 scripts/restore_r2.py</code>。
  R2 に無いものは SSD から <code>python3 scripts/recover.py from-ssd</code>（見るだけ）→ <code>--apply</code></li>
<li>定期実行とダッシュボードを登録する：<code>python3 scripts/schedule.py install</code></li>
<li>全体の点検：<code>python3 scripts/recover.py check</code>（8 項目そろえば完了。足りなければ「次にすること」が 1 つ出ます）</li>
<li>Suno・DistroKid・Spotify for Artists は、専用の Chrome でログインし直す</li>
</ol>
<p class="sub">DistroKid に登録済みの曲は、Mac が止まっていても予定どおり配信されます。止まるのは、新しい曲づくり・SNS の投稿・配信の確認です。</p></section>"""


def page(token: str) -> str:
    v, a = vault.status(), alert.status()
    svcs = vault.services()
    by = {}
    for k in v["keys"]:
        by.setdefault(k["service"], []).append(k)
    watch_first = [k for k in ("ntfy", "healthcheck") if k in svcs]
    order = watch_first + [k for k in svcs if k not in watch_first]
    n_set, n_all = sum(1 for k in v["keys"] if k["set"]), len(v["keys"])
    body = ("<h1>鍵と見守り</h1>"
            '<p class="sub">鍵（各サービスに入るための合言葉）は、この Mac の <code>.env</code> にだけ置き、暗号にした写しを R2 の金庫に預けます。'
            "入れた鍵は画面に表示しません。チャットやメールには貼らないでください。</p>"
            + watch_html(v, a)
            + f'<h2 style="margin-top:8px">鍵（{n_set}/{n_all} 個が入っています）</h2>'
            '<p class="sub">入れ方が分からないサービスは、ターミナルで <code>python3 scripts/setup_keys.py --only &lt;名前&gt;</code> を実行すると、順に案内します。'
            "ここから入れられるのは、この Mac のブラウザか Tailscale でつないだスマホだけです。</p>"
            + "".join(service_html(k, svcs[k], by.get(k, []), v["checks"]) for k in order if by.get(k))
            + f'<section class="card"><h2>Mac を眠らせない</h2><pre class="mono" style="white-space:pre-wrap;margin:0">{E(alert.keep_awake_hint())}</pre></section>'
            + RECOVER)
    return _ui.page("鍵と見守り", body, token, active="/keys")

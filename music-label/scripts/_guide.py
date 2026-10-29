"""
ダッシュボードのポップアップに出す「止まっている原因」「Gon さんがやること」「ワンクリックのボタン」を作る部品（webapp.py が使う）

explain(row, phase) → {"cause": 文章, "steps": [やること…], "actions": [ボタン…]}
ボタンの種類（webapp.py の画面が描く）：
  run        仕組みを動かす（action と、slug / id / week）
  copy       文をコピーする（text）
  link       ページを開く（url）
  terminal   ターミナルを開いて、鍵の設定などを始める（key：openai / spotify / owner / ffmpeg）
  registered 日付を選んで「DistroKid に登録した」を記録する（id）
  folder     公開セットのフォルダを開く（id）／ artist_folder：その組のフォルダを開く（slug）
"""
from __future__ import annotations

from datetime import date

import _songs

SUNO_URL = "https://suno.com/create"
DISTROKID_URL = "https://distrokid.com/new/"
DISTROKID_MYMUSIC = "https://distrokid.com/mymusic/"
OPENAI_KEYS = "https://platform.openai.com/api-keys"
SPOTIFY_DASH = "https://developer.spotify.com/dashboard"


def _cover_note(c: dict) -> str:
    """候補の説明：どのシーンか（寄り・引き・だれが写るか・曲のどこか）＋ Claude の一言（決定 122）"""
    import cover_scene
    who = {"artist": "本人", "other": "曲の人物", "none": "人なし"}.get(c.get("who"), "")
    fam = (cover_scene.FAMILIES.get(c.get("family") or "") or ("", ""))[1]   # デザインの系統（決定 126）
    head = "・".join(x for x in (fam, cover_scene.SHOT_JA.get(c.get("shot"), ""), who, c.get("look_ja", "") if c.get("look_ja") != fam else "") if x)   # 作りの印象も（決定 123）
    scene = f"【{head}】{c.get('scene_ja', '')}" if head or c.get("scene_ja") else ""
    note = c["note"] if c["ok"] else "（禁止ルールに当たる心配あり）" + c["note"]
    return " ".join(x for x in (scene, note) if x)


def _has_playwright() -> bool:
    """ブラウザで自動入力する部品（Playwright）が入っているか"""
    import importlib, importlib.util, os, site
    # ダッシュボードを動かしたまま部品を入れた場合も気づけるように、利用者用の置き場所を足して探し直す
    user = site.getusersitepackages()
    if os.path.isdir(user):
        site.addsitedir(user)
    importlib.invalidate_caches()
    if importlib.util.find_spec("playwright") is not None:
        return True
    import _common   # ダッシュボードの Python に無くても、ほかの Python に入っていれば、ブラウザの入力はそちらで動く（決定 59）
    return bool(_common.python_with("playwright"))


def _late(phase: dict) -> str:
    d = phase.get("detail", "")
    return "（予定より遅れています）" if "予定は" in d else ""


SONG_PHASES = ("Suno で作曲", "仕上げ", "DistroKid に登録", "配信待ち", "配信")


def back_actions(song: dict | None) -> list[dict]:
    """間違えて進めたときに戻すボタン（決定 38。消さずに「戻したもの」に移す）"""
    if not song:
        return []
    try:
        import publish
        opts = publish.back_options(song)
    except Exception:  # noqa: BLE001
        return []
    label = {"suno": "← Suno で作曲に戻す（取り込みを取り消す）", "master": "← 音量を整え直す",
             "cover": "← ジャケットに戻す（6 案を作り直して選び直す）",
             "ready": "← DistroKid の登録を取り消す（登録前に戻す）", "registered": "← 配信中を取り消す"}
    why = {"suno": "取り込んだ曲・下書き・公開セットを「戻したもの」フォルダに移して、Suno で作曲の段階に戻します（消しません）。",
           "master": "音量を整えた音源と下書きを作り直します。",
           "cover": "いまのジャケットを「戻したもの」に移して、ジャケットのフェーズに戻し、曲の印象的なシーンから 6 案を作り直します"
                    "（数分・OpenAI の画像の料金がかかります）。自動では選ばないので、できたら 1 枚選んでください。DistroKid の下書きも選んだジャケットで作り直します。", "ready": "DistroKid に登録した記録を取り消します（DistroKid の画面の登録そのものは取り消されません）。",
           "registered": "配信中の記録（ISRC・URL）を取り消します。"}
    return [{"kind": "run", "label": label[t], "action": "back", "id": song["id"], "to": t,
             "confirm": why[t] + " よいですか？"} for t in opts]


def explain(row: dict, phase: dict) -> dict:
    # 戻すボタンは、工程ごとではなく曲の行ごとに、ポップアップの上の「← 戻る」の欄に出す（webapp.py の rows[].back）
    g = _explain(row, phase)
    fwd = forward_actions(row, phase)
    if fwd:   # 同じボタンは下の並びから外す（日付・住所の欄が 2 つにならないように）
        kinds = {a["kind"] for a in fwd}
        g = {**g, "forward": fwd, "actions": [a for a in g.get("actions", []) if a.get("kind") not in kinds]}
    return g


def forward_actions(row: dict, phase: dict) -> list[dict]:
    """うまくいったのに表が進んでいないとき、ポップアップの上の「→ 次に進める」の欄に出すボタン（決定 58）。
    DistroKid に登録できた → 登録の記録（配信日つき）で「配信待ち」へ／配信を見つけた → 「配信中」へ"""
    if row.get("planned") or not row.get("id") or (phase.get("state") == "done" and not phase.get("milestone")):
        return []
    song = _songs.by_id(row["id"])
    if song is None:
        return []
    st = _songs.state(song).get("status") or ""
    if phase["name"] == "DistroKid に登録" and st not in ("registered", "live"):
        return [{"kind": "registered", "id": row["id"], "label": "✓ DistroKid に登録できた → 次へ進める（配信待ちへ）"}]
    if phase["name"] == "配信待ち" and st == "registered":
        return [{"kind": "mark_live", "id": row["id"], "label": "✓ 配信を見つけた → 次へ進める（配信中へ）"}]
    return []


def _explain(row: dict, phase: dict) -> dict:
    name, state = phase["name"], phase["state"]
    song = _songs.by_id(row.get("id", "")) if row.get("id") else None
    title = f"「{row.get('title')}」" if row.get("title") else ""
    if row.get("planned"):
        return {"cause": f"{row.get('sub', '')}の制作は、まだ始まっていません。その週の火曜の朝に、ブリーフ（Suno への指示と歌詞）が自動で作られます。",
                "steps": ["いまは何もしなくて大丈夫です。"], "actions": []}
    if state == "plan":
        return {"cause": f"まだ先の工程です（{phase['start']} ごろの予定）。前の工程が終わると、自動でこの工程に進みます。",
                "steps": ["いまは何もしなくて大丈夫です。"], "actions": []}
    if state == "done" and not phase.get("milestone"):
        return {"cause": f"済みました。{phase.get('detail', '')}", "steps": [], "actions": []}

    if name == "配信確認（アーティストのページ）":   # デビュー完了の最後の一歩（決定 139・141）
        import publish
        pages = publish.artist_pages(row.get("slug", ""))
        left = publish.artist_pages_missing(row.get("slug", ""))
        return {"cause": f"{row.get('artist', 'この組')} のデビュー曲が配信されたら、5 つのサービスのアーティストのページを記録してください。"
                         "全部そろうとデビュー完了になり、この組は「定期配信」のタブに移ります。"
                         + (f"（あと {len(left)} つ：{'・'.join(left)}）" if left else "（5 つそろっています）"),
                "steps": ["それぞれの欄の隣の「検索 ↗」を押すと、新しいタブでそのサービスの検索が組の名前で開きます。アーティストのページを開いて住所をコピーする",
                          "下の 5 つの欄に貼る（記録済みの欄は入っています。わかったものから少しずつでもよい）",
                          "「アーティストのページを記録する」を押す"],
                "actions": [{"kind": "artist_pages", "slug": row.get("slug"), "values": pages,
                             "search": publish.artist_page_search(row.get("artist", "")),
                             "fields": [[k, n] for k, n, _ in publish.ARTIST_PAGES], "label": "アーティストのページを記録する"}]}
    if name == "名前の確認":
        return {"cause": "同じ名前のアーティストがいないか、まだ確かめていません。同じ名前があると、配信したときに別人のページに曲が混ざってしまいます。",
                "steps": ["「名前を確かめる」を押す（Spotify などを自動で調べます）",
                          "「重なりあり」と出たら、Claude に相談して名前を変える"],
                "actions": [{"kind": "run", "label": "名前を確かめる", "action": "check_name", "slug": row["slug"], "primary": True}]}

    if name == "写真・ジャケット":
        if "鍵" in phase.get("detail", ""):
            return {"cause": "アーティスト写真とジャケットを作るための OpenAI の鍵が、Mac に入っていません。",
                    "steps": ["「OpenAI の鍵のページを開く」で鍵を作ってコピーする",
                              "「鍵を入れる（ターミナルが開きます）」を押し、出てきた質問に鍵を貼って Enter",
                              "終わったら「写真とジャケットを作る」を押す"],
                    "actions": [{"kind": "link", "label": "OpenAI の鍵のページを開く", "url": OPENAI_KEYS},
                                {"kind": "terminal", "label": "鍵を入れる（ターミナルが開きます）", "key": "openai"},
                                {"kind": "run", "primary": True, "label": "写真とジャケットを作る", "action": "images", "slug": row["slug"], "confirm": "写真とジャケットを作ります（OpenAI の画像の料金がかかります）。よいですか？"}]}
        return {"cause": f"{row['artist']} のアーティスト写真かジャケットが、まだできていません。",
                "steps": ["「写真とジャケットを作る」を押す（数分かかります。OpenAI の画像の料金がかかります）",
                          "できたら「フォルダを開く」で仕上がりを確かめる"],
                "actions": [{"kind": "run", "primary": True, "label": "写真とジャケットを作る", "action": "images", "slug": row["slug"], "confirm": "写真とジャケットを作ります（OpenAI の画像の料金がかかります）。よいですか？"},
                            {"kind": "artist_folder", "label": "フォルダを開く", "slug": row["slug"]}]}

    if name == "ブリーフ":
        return {"cause": "今週のブリーフ（Suno への指示と歌詞）を作っています。ふだんは火曜の朝に自動で作られます。",
                "steps": ["待つだけで大丈夫です。", "すぐに作りたいときは「ブリーフを作る」を押す"],
                "actions": [{"kind": "run", "label": "ブリーフを作る", "action": "brief", "week": row.get("week"), "primary": True}]}

    if name == "Suno で作曲":
        import take_check
        sid = row.get("id") or ""
        # 標準は専用の Chrome への自動入力（オーナーの指示 2026-10-27。決定 106）。ロボット確認が続くときは、ふだんのブラウザで写して貼る（決定 104）
        acts = [{"kind": "run", "primary": True, "label": "Suno に入力する（ブラウザが開きます）", "action": "fill_suno", "id": row.get("id")},
                {"kind": "retake", "id": row.get("id"), "reasons": list(take_check.MANUAL)},   # 2 曲とも良くないとき（決定 100）
                {"kind": "run", "label": "曲を取り込む", "action": "scan"},
                {"kind": "link", "label": "予備：ふだんのブラウザで写して貼る（ロボット確認が続くとき）", "url": f"/paste/{sid}" if sid else "/paste"},
                {"kind": "run", "label": "Suno にログイン（初回だけ）", "action": "browser_login", "site": "suno"},
                {"kind": "run", "label": "Suno の画面の部品を記録する（うまく入らない欄があるとき・Claude に見せる用）", "action": "browser_parts", "site": "suno"},
                {"kind": "run", "label": "専用の Chrome を作り直す（ログインが何度も断られるとき）", "action": "browser_reset",
                 "confirm": "専用の Chrome のログインの記録を消して作り直します。Suno と DistroKid に、もう一度ログインが要ります。よいですか？"}]
        steps = ["初回だけ：「Suno にログイン」を押し、開いた Chrome でログインする（システムはつながっていないので、Google でのログインも通ります）",
                 "「Suno に入力する」を押す → 専用の Chrome で「高度な」に切り替え、歌詞・スタイル・除外するスタイル・声の性別・間隔・Max mode・音量の大きさ・スタイルの影響度・バラエティ・あなたの好み・曲名が、曲のイメージに合わせて自動で入ります。入ったかを読み返して確かめ、全部そろったときだけ「終わりました ✓」と出ます",
                 "ブラウザの右下の案内に従って内容を確かめ、Suno の「作成」（Create）を押す（押すのは Gon さんだけ。ロボット確認が出たら自分でチェック。続けて何曲も作らない）",
                 "できた 2 つを聴いて、気に入った方に「いいね」→ その 1 本を SSD の「楽曲情報」にダウンロード（WAV。ダウンロードは月 20 曲まで）",
                 "数分で自動で取り込まれて、次のフェーズに進みます（すぐ進めたいときは「曲を取り込む」）",
                 "ロボット確認が何度も出て通らないときは「予備：ふだんのブラウザで写して貼る」"]
        if not _has_playwright():
            acts.insert(0, {"kind": "terminal", "label": "ブラウザ入力の部品を入れる（初回だけ・ターミナルが開きます）", "key": "playwright"})
            steps.insert(0, "初回だけ：「ブラウザ入力の部品を入れる」を押し、ターミナルの作業が終わるまで待つ")
        return {"cause": f"{title}の Suno での作曲を待っています。この工程は Gon さんの番です{_late(phase)}。", "steps": steps, "actions": acts}

    if name.startswith("仕上げ"):
        d = phase.get("detail", "")
        song = _songs.by_id(row["id"]) if row.get("id") else None
        if song and song["kind"] != "debut" and _songs.state(song).get("status") == "waiting_cover":
            # 毎週の曲・不定期の曲のジャケット：その曲の候補を作る → 最初の 5 回は Gon さんが 1 枚選ぶ（決定 73）
            import publish
            import song_check
            cands = publish.cover_candidates(song)
            if cands:
                return {"cause": "ジャケットの候補ができました。1 枚選ぶと、3000×3000 に仕上げて DistroKid の下書きまで進めます。"
                                 "（最初の 5 曲は Gon さんが選び、その好みを覚えて 6 曲目から自動で選びます）",
                        "steps": ["気に入った候補の「これにする」を押す（数十秒で次のフェーズへ）", "どれも好みでなければ「候補を作り直す」"],
                        "actions": [{"kind": "cover_pick", "id": song["id"], "no": c["no"], "img": song_check.furl(c["file"]),
                                     "label": f"#{c['no']} これにする" + (f"（{c['total']:.1f} 点）" if isinstance(c.get("total"), (int, float)) else ""),
                                     "note": _cover_note(c)} for c in cands[:6]]
                                   + [{"kind": "run", "label": "候補を作り直す（OpenAI の画像の料金がかかります）", "action": "cover", "id": song["id"], "redo": "1",
                                       "confirm": "ジャケットの候補を作り直しますか？（OpenAI の画像の料金がかかります）"}]}
            if "鍵" in d:
                return {"cause": "ジャケットを作るための OpenAI の鍵が、Mac に入っていません。",
                        "steps": ["「鍵を入れる（ターミナルが開きます）」を押し、鍵を貼って Enter", "終わったら「ジャケットを作る」を押す"],
                        "actions": [{"kind": "link", "label": "OpenAI の鍵のページを開く", "url": OPENAI_KEYS},
                                    {"kind": "terminal", "label": "鍵を入れる（ターミナルが開きます）", "key": "openai"},
                                    {"kind": "run", "label": "ジャケットを作る", "action": "cover", "id": song["id"], "primary": True}]}
            return {"cause": "この曲のジャケットを作っています（数分）。",
                    "steps": ["待つだけで大丈夫です。進まないときは「ジャケットを作る」を押す"],
                    "actions": [{"kind": "run", "label": "ジャケットを作る", "action": "cover", "id": song["id"], "primary": True},
                                {"kind": "run", "label": "もう一度進める", "action": "scan"}]}
        if "ffmpeg" in d:
            return {"cause": "曲の音量を整える道具（ffmpeg）が Mac に入っていないため、ここで止まっています。",
                    "steps": ["「道具を入れる（ターミナルが開きます）」を押す。終わるまで数分待つ", "終わったら「もう一度進める」を押す"],
                    "actions": [{"kind": "terminal", "label": "道具を入れる（ターミナルが開きます）", "key": "ffmpeg"},
                                {"kind": "run", "label": "もう一度進める", "action": "scan"}]}
        if "鍵" in d:
            return {"cause": "ジャケットを作るための OpenAI の鍵が、Mac に入っていません。",
                    "steps": ["「鍵を入れる（ターミナルが開きます）」を押し、鍵を貼って Enter", "終わったら「ジャケットを作る」を押す"],
                    "actions": [{"kind": "link", "label": "OpenAI の鍵のページを開く", "url": OPENAI_KEYS},
                                {"kind": "terminal", "label": "鍵を入れる（ターミナルが開きます）", "key": "openai"},
                                {"kind": "run", "label": "ジャケットを作る", "action": "cover", "slug": row["slug"], "primary": True}]}
        if "ジャケット" in d:
            return {"cause": "ジャケットを作っています（数分）。",
                    "steps": ["待つだけで大丈夫です。進まないときは「ジャケットを作る」を押す"],
                    "actions": [{"kind": "run", "label": "ジャケットを作る", "action": "cover", "slug": row["slug"], "primary": True},
                                {"kind": "run", "label": "もう一度進める", "action": "scan"}]}
        return {"cause": "取り込んだ曲の音量を整え、ジャケット・歌詞・DistroKid の下書きと一緒にまとめています（数分）。",
                "steps": ["待つだけで大丈夫です。「公開の準備を見る」で、進み具合と中身（ジャケット・音・下書き）を確かめられます",
                          "止まっているように見えたら「もう一度進める」を押す"],
                "actions": [{"kind": "link", "label": "公開の準備を見る（ジャケット・下書き・公開セット）", "url": f"/song/{row.get('id')}"},
                            {"kind": "run", "label": "もう一度進める", "action": "scan", "primary": True}]}

    if name == "DistroKid に登録":
        acts = [{"kind": "link", "label": "公開の準備を見る（ジャケット・下書き・公開セット）", "url": f"/song/{row.get('id')}"},
                {"kind": "run", "primary": True, "label": "DistroKid に入力する（ブラウザが開きます）", "action": "fill_distrokid", "id": row.get("id")},
                {"kind": "folder", "label": "公開セットのフォルダを開く", "id": row.get("id")},
                {"kind": "run", "label": "DistroKid にログイン（初回だけ）", "action": "browser_login", "site": "distrokid"},
                {"kind": "registered", "label": "DistroKid に登録した", "id": row.get("id")}]
        try:   # システムが残した画面の写し（入れ終わったとき・差し戻されたとき・入れ直したあと。決定 69）
            import song_check
            from _common import OUT as _OUT
            for when, ja in (("filled", "入れ終わったとき"), ("rejected", "差し戻されたとき"), ("refilled", "入れ直したあと")):
                shot = _OUT / "browser" / "screens" / f"{row.get('id')}_{when}.png"
                if shot.exists():
                    import datetime as _dt
                    at = _dt.datetime.fromtimestamp(shot.stat().st_mtime).strftime("%m/%d %H:%M")
                    acts.insert(2, {"kind": "link", "label": f"DistroKid の画面の写し（{ja}・{at}）を見る", "url": song_check.furl(shot)})
        except Exception:  # noqa: BLE001
            pass
        try:   # 登録を待つ曲がほかにもあれば、続けて入れるボタン（決定 51）
            n_wait = sum(1 for x in _songs.all_songs() if _songs.state(x).get("status") == "ready")
        except Exception:  # noqa: BLE001
            n_wait = 0
        try:   # 2 曲目から：Spotify のアーティストのページがわかるまで登録を待つ（決定 53）
            import browser_fill
            song = _songs.by_id(str(row.get("id") or ""))
            held = song is not None and not browser_fill.same_page_ready(song)
        except Exception:  # noqa: BLE001
            held = False
        if held:
            acts.insert(1, {"kind": "artist_page", "slug": row.get("slug"), "label": "Spotify のページを記録する"})
            return {"cause": f"{title}の準備はできています。ただ、{row.get('artist', 'この組')} の Spotify のアーティストのページがまだわからないので、"
                             "登録は少し待ちます（いま登録すると、デビュー曲と別のページができるおそれがあります）。",
                    "steps": ["デビュー曲が Spotify に出ると、毎日の確認でページが自動で記録され、登録できるようになります",
                              "早く進めたいとき：DistroKid のデビュー曲のページに出る Spotify の URI（spotify:artist:…）をコピーし、"
                              "下の欄に貼って「Spotify のページを記録する」を押す"],
                    "actions": acts}
        if n_wait > 1:
            acts.insert(2, {"kind": "run", "label": f"登録を待つ {n_wait} 曲を続けて DistroKid に入力する（1 曲ずつ「登録した」で次へ）",
                            "action": "fill_distrokid_all"})
        steps = ["「公開の準備を見る」で、ジャケット・音・下書き・公開セットを目で確かめる",
                 "「DistroKid に入力する」を押す → 専用のブラウザが開き、項目と音源・ジャケットが自動で入ります（初回だけ DistroKid へのログインを求められます）",
                 "ブラウザの右下の案内で、手で選ぶ項目（Explicit・AI の申告など）を確かめる",
                 "内容を確かめて、最後の登録ボタンを押す（押すのは Gon さんだけ）",
                 "案内の「登録した」を押すと、配信日と一緒に記録されます（ここの「DistroKid に登録した」でも同じ）"]
        if not _has_playwright():
            acts.insert(0, {"kind": "terminal", "label": "ブラウザ入力の部品を入れる（初回だけ・ターミナルが開きます）", "key": "playwright"})
            steps.insert(0, "初回だけ：「ブラウザ入力の部品を入れる」を押し、ターミナルの作業が終わるまで待つ")
        return {"cause": f"{title}の音源・ジャケット・登録の内容がそろいました。DistroKid への登録を待っています{_late(phase)}。",
                "steps": steps, "actions": acts}

    if name == "配信待ち":
        d = phase.get("detail", "")
        # Gon さんが Spotify・Apple Music などで配信を見つけたら、手で「配信中」にできる（Spotify の鍵が無くても。決定 55）
        live_btn = {"kind": "mark_live", "id": row.get("id"), "label": "配信を見つけた（配信中にする）"}
        live_step = ("配信を見つけたら：Spotify の曲の住所（共有 → リンクをコピー）と、アーティストの住所（アーティストのページの共有 → リンクをコピー）を"
                     "下の欄に貼って「配信を見つけた」を押す。アーティストの住所は、2 曲目からを同じページにまとめるのに使います")
        if "鍵" in d:
            return {"cause": "DistroKid には登録済みです。ただ、Spotify の鍵が無いので、配信が始まったかを自動で確かめられません。",
                    "steps": [live_step,
                              "自動で確かめたいとき：「Spotify の開発者ページを開く」でアプリを作り、Client ID と Client secret をコピー →"
                              "「鍵を入れる（ターミナルが開きます）」に貼って Enter → 「配信を確かめる」"],
                    "actions": [live_btn, {"kind": "link", "label": "Spotify の開発者ページを開く", "url": SPOTIFY_DASH},
                                {"kind": "terminal", "label": "鍵を入れる（ターミナルが開きます）", "key": "spotify"},
                                {"kind": "run", "label": "配信を確かめる", "action": "track"}]}
        if "1 週間" in d:
            return {"cause": "配信日から 1 週間たっても Spotify に出ていません。DistroKid で審査中か、差し戻しになっている可能性があります。",
                    "steps": ["「DistroKid の My Music を開く」で、この曲の状態を確かめる",
                              "差し戻しの知らせがあれば、その文を Claude に貼って相談する", live_step],
                    "actions": [{"kind": "link", "label": "DistroKid の My Music を開く", "url": DISTROKID_MYMUSIC},
                                {"kind": "run", "label": "配信を確かめる", "action": "track"}, live_btn]}
        ms = next((x for x in row.get("phases", []) if x.get("milestone")), None)
        when = f"配信日（{ms['start']}）に" if ms else "配信日に"
        return {"cause": f"{title}は DistroKid に登録済みです。{when}各ストアに並びます。毎朝自動で確かめ（Spotify の鍵が無くても、Apple Music・Deezer・"
                         "Spotify の曲のページで確かめます）、配信が始まったら ISRC・UPC・配信 URL と、Spotify のアーティストのページを記録します。",
                "steps": ["待つだけで大丈夫です。", "すぐに確かめたいときは「配信を確かめる」を押す", live_step],
                "actions": [{"kind": "run", "label": "配信を確かめる", "action": "track", "primary": True}, live_btn]}

    if phase.get("milestone"):
        if state == "done":
            acts = [{"kind": "link", "label": "Spotify で聴く", "url": phase["action"]}] if phase.get("action") else []
            return {"cause": f"配信中です。{phase.get('detail', '')}", "steps": [], "actions": acts}
        return {"cause": f"配信の予定日です（{phase['start']}）。", "steps": ["いまは何もしなくて大丈夫です。"], "actions": []}

    return {"cause": phase.get("detail", ""), "steps": [phase["action"]] if phase.get("action") else [], "actions": []}

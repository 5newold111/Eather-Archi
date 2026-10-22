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


def _has_playwright() -> bool:
    """ブラウザで自動入力する部品（Playwright）が入っているか"""
    import importlib, importlib.util, os, site
    # ダッシュボードを動かしたまま部品を入れた場合も気づけるように、利用者用の置き場所を足して探し直す
    user = site.getusersitepackages()
    if os.path.isdir(user):
        site.addsitedir(user)
    importlib.invalidate_caches()
    return importlib.util.find_spec("playwright") is not None


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
             "ready": "← DistroKid の登録を取り消す（登録前に戻す）", "registered": "← 配信中を取り消す"}
    why = {"suno": "取り込んだ曲・下書き・公開セットを「戻したもの」フォルダに移して、Suno で作曲の段階に戻します（消しません）。",
           "master": "音量を整えた音源と下書きを作り直します。", "ready": "DistroKid に登録した記録を取り消します（DistroKid の画面の登録そのものは取り消されません）。",
           "registered": "配信中の記録（ISRC・URL）を取り消します。"}
    return [{"kind": "run", "label": label[t], "action": "back", "id": song["id"], "to": t,
             "confirm": why[t] + " よいですか？"} for t in opts]


def explain(row: dict, phase: dict) -> dict:
    # 戻すボタンは、工程ごとではなく曲の行ごとに、ポップアップの上の「← 戻る」の欄に出す（webapp.py の rows[].back）
    return _explain(row, phase)


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
        acts = [{"kind": "run", "primary": True, "label": "Suno に入力する（ブラウザが開きます）", "action": "fill_suno", "id": row.get("id")},
                {"kind": "run", "label": "曲を取り込む", "action": "scan"},
                {"kind": "run", "label": "Suno にログイン（初回だけ）", "action": "browser_login", "site": "suno"},
                {"kind": "run", "label": "Suno の画面の部品を記録する（うまく入らない欄があるとき・Claude に見せる用）", "action": "browser_parts", "site": "suno"},
                {"kind": "run", "label": "専用の Chrome を作り直す（ログインが何度も断られるとき）", "action": "browser_reset",
                 "confirm": "専用の Chrome のログインの記録を消して作り直します。Suno と DistroKid に、もう一度ログインが要ります。よいですか？"}]
        steps = ["初回だけ：「Suno にログイン」を押し、開いた Chrome でログインする（システムはつながっていないので、Google でのログインも通ります）",
                 "「Suno に入力する」を押す → 専用の Chrome で「高度な」に切り替え、歌詞・スタイル・除外するスタイル・声の性別・間隔・Max mode・音量の大きさ・スタイルの影響度・バラエティ・あなたの好み・曲名が、曲のイメージに合わせて自動で入ります。入ったかを読み返して確かめ、全部そろったときだけ「終わりました ✓」と出ます",
                 "ブラウザの右下の案内に従って内容を確かめ、Suno の「作成」（Create）を押す（押すのは Gon さんだけ）",
                 "できた 2 つを聴いて、気に入った方に「いいね」→ その 1 本を SSD の「楽曲情報」にダウンロード",
                 "数分で自動で取り込まれて、次のフェーズに進みます（すぐ進めたいときは「曲を取り込む」）"]
        if not _has_playwright():
            acts.insert(0, {"kind": "terminal", "label": "ブラウザ入力の部品を入れる（初回だけ・ターミナルが開きます）", "key": "playwright"})
            steps.insert(0, "初回だけ：「ブラウザ入力の部品を入れる」を押し、ターミナルの作業が終わるまで待つ")
        return {"cause": f"{title}の Suno での作曲を待っています。この工程は Gon さんの番です{_late(phase)}。", "steps": steps, "actions": acts}

    if name.startswith("仕上げ"):
        d = phase.get("detail", "")
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
        if "鍵" in d:
            return {"cause": "DistroKid には登録済みです。ただ、Spotify の鍵が無いので、配信が始まったかを自動で確かめられません。",
                    "steps": ["「Spotify の開発者ページを開く」でアプリを作り、Client ID と Client secret をコピー",
                              "「鍵を入れる（ターミナルが開きます）」を押し、質問に貼って Enter",
                              "終わったら「配信を確かめる」を押す"],
                    "actions": [{"kind": "link", "label": "Spotify の開発者ページを開く", "url": SPOTIFY_DASH},
                                {"kind": "terminal", "label": "鍵を入れる（ターミナルが開きます）", "key": "spotify"},
                                {"kind": "run", "label": "配信を確かめる", "action": "track"}]}
        if "1 週間" in d:
            return {"cause": "配信日から 1 週間たっても Spotify に出ていません。DistroKid で審査中か、差し戻しになっている可能性があります。",
                    "steps": ["「DistroKid の My Music を開く」で、この曲の状態を確かめる",
                              "差し戻しの知らせがあれば、その文を Claude に貼って相談する"],
                    "actions": [{"kind": "link", "label": "DistroKid の My Music を開く", "url": DISTROKID_MYMUSIC},
                                {"kind": "run", "label": "配信を確かめる", "action": "track"}]}
        ms = next((x for x in row.get("phases", []) if x.get("milestone")), None)
        when = f"配信日（{ms['start']}）に" if ms else "配信日に"
        return {"cause": f"{title}は DistroKid に登録済みです。{when}各ストアに並びます。毎朝自動で確かめ、配信が始まったら ISRC・UPC・配信 URL を記録します。",
                "steps": ["待つだけで大丈夫です。", "すぐに確かめたいときは「配信を確かめる」を押す"],
                "actions": [{"kind": "run", "label": "配信を確かめる", "action": "track", "primary": True}]}

    if phase.get("milestone"):
        if state == "done":
            acts = [{"kind": "link", "label": "Spotify で聴く", "url": phase["action"]}] if phase.get("action") else []
            return {"cause": f"配信中です。{phase.get('detail', '')}", "steps": [], "actions": acts}
        return {"cause": f"配信の予定日です（{phase['start']}）。", "steps": ["いまは何もしなくて大丈夫です。"], "actions": []}

    return {"cause": phase.get("detail", ""), "steps": [phase["action"]] if phase.get("action") else [], "actions": []}

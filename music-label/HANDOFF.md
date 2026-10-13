# 引き継ぎメモ（2026-10-13・クラウドのセッションから Mac へ）

このフォルダで新しく Claude を起動したら、最初に「HANDOFF.md を読んで、続きから進めて」と頼む。

## 今どこにいるか

- レーベル名は **Postdog Records**（中の呼び名は drive）。所属は 5 組で、全員オーナーが採用済み
  - DECLAN FAY・HOLLOWPLAN・VERNALIGHT（旧名 NAO EASTERLY、ID は nao_easterly のまま）・SOFT CANTILEVER・TIDEWOOL
  - 設定書は `templates/artists/<組>.json`。管理表から `python3 scripts/artist_book.py pull` で作る（直接は書き換えない）
- オーナーは「できるだけ早く公開したい」。デビュー曲 5 曲は **自動の流れを使わず手作業で出す**
  - Suno で作曲（Pro プラン）→ 聴いて選ぶ → DistroKid に登録（公開日はいちばん早い日）
  - ジャケットとアーティスト写真は **自動で作る**（ChatGPT と同じ OpenAI の画像生成を API で呼ぶ。`python3 scripts/kit_images.py`。要 `.env` の `OPENAI_API_KEY`）
  - 材料：`debut_kit/2026-10-11/`（`kit.json` に曲名・Suno の指示文・歌詞、`chatgpt_prompts.json` に画像の依頼文）
- 設定書のデビュー週は 2026-10-26（自動の配信枠は 10/28）。手作業の 5 曲が出たら、この枠を 2 曲目として作り直す

## 置き場所

| もの | 場所 |
|---|---|
| アーティストの管理表（正本） | Google スプレッドシート「アーティスト情報」 https://docs.google.com/spreadsheets/d/1q_Zfo6kW4PNJRQjcNUJGzN9gfBNWJqrBxBR6PnEPSAA/edit |
| 詰まっているところの一覧（進み具合） | 同じスプレッドシートの「詰まっているところ」タブ |
| デビュー 5 曲の作業書・ChatGPT の依頼文（正本） | 5newold111 のドライブ「音楽レーベルアプリ／作業手順書（AI）／Postdog Records デビュー 5 曲 作業書」 https://docs.google.com/document/d/1sPgnW-T5qcLVu6bTcYVUxyiwjMmOCHPaO07jARiUBZs/edit 。今後の記入はすべてここ（Claude も書き換えられる）。同じフォルダの「最初の5曲」と、etherarch.official 持ちの同名の作業書（1XUw…）は古い版で、もう記入しない |
| コピーボタン付きの作業台 | https://claude.ai/artifact/7WEKVdVL4Bg2HSN9Dfmkbq |
| 決めたこと | `docs/12_decisions.md`（1〜9） |
| 管理表の使い方 | `docs/13_artist_book.md` |

## 詰まっているところ（2026-10-13 時点）

1. Suno のプラン … **完了**（Pro）
2. DistroKid のプラン … **完了**（Ultimate・アーティスト名 5 つ。今の 5 組でちょうど埋まる）
3. 名前の重複確認 … **完了**（オーナーが Spotify で確認。NAO EASTERLY は「NAO」と紛れるので VERNALIGHT に改名し、Claude が Web 検索で確認）
4. Suno で 5 曲 … 未着手
5. ジャケットと写真 … 自動で作る仕組みはできた（`kit_images.py`）。OpenAI の鍵を `.env` に入れれば Mac で `python3 scripts/kit_images.py` 一発で 10 枚できる
6. テイク選び … 未着手
7. DistroKid に登録 … 未着手
8. 配信後に ISRC と選んだテイクを記録 … 未着手
9. 10/28 の自動配信枠の調整 … 待ち
10. 参考曲 11 曲以上の購入と解析 … 未着手（デビューには不要）
11. 自動化の鍵の設定（`python3 scripts/setup_keys.py`）… 未着手（デビューには不要）
12. 作業を Mac に移す … このメモ

## Google のアカウント（2026-10-13 時点）

- レーベル用の正しいアカウントは etherarchi@gmail.com。アーティスト情報のシートは 5newold111 の持ち物で、etherarchi が編集者
- Claude のドライブのつなぎ先は 5newold111、カレンダーも 5newold111。ドキュメントのつなぎ先は不明（5newold111 ではない）
- Claude が書き換えられるのは「リンクを知っている全員が編集可」のファイルか、Claude 自身が作ったファイル（アーティスト情報のシートと正本の作業書は書き換えられることを確認済み）

## OpenAI の鍵の登録（Mac のターミナル）

```
cd music-label
python3 -m pip install -r requirements.txt
python3 scripts/setup_keys.py --only openai    # 鍵を貼って Enter（画面には出ない）→ 実際に接続して確かめる
python3 scripts/kit_images.py                  # 5 組 × 2 枚を自動で作り、3000×3000 に整える
```

## 守ること

- オーナーへの返事は日本語。専門用語はやさしく言い換え、何をしているかを日本語で書く
- 実在のアーティスト名・機種名は、Suno の指示文と画像の依頼文に入れない（プロフィールや管理表の「影響」「好きなアーティスト」の欄だけ）
- 鍵（API キー）は `.env` だけに書く。会話に貼らない・コミットしない
- DistroKid にテスト用の曲を上げない
- 管理表の「採用」はオーナーが決める。機械が足すのは「提案」の行だけ
- 新しい名前を出すときは、同じ名前のアーティストがいないかを Claude が先に Web 検索で調べてから見せる（オーナーに確認を頼まない）
- PR #5（このブランチ）は本体に合流させない（公開サイトに music-label が出てしまうため）。いずれ非公開の別リポジトリに切り出す（docs/12 の 1.）

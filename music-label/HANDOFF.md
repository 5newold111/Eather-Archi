# 引き継ぎメモ（2026-10-13・クラウドのセッションから Mac へ）

このフォルダで新しく Claude を起動したら、最初に「HANDOFF.md を読んで、続きから進めて」と頼む。

## 今どこにいるか

- レーベル名は **Postdog Records**（中の呼び名は drive）。所属は 5 組で、全員オーナーが採用済み
  - DECLAN FAY・HOLLOWPLAN・NAO EASTERLY・SOFT CANTILEVER・TIDEWOOL
  - 設定書は `templates/artists/<組>.json`。管理表から `python3 scripts/artist_book.py pull` で作る（直接は書き換えない）
- オーナーは「できるだけ早く公開したい」。デビュー曲 5 曲は **自動の流れを使わず手作業で出す**
  - Suno で作曲（Pro プラン）→ 聴いて選ぶ → DistroKid に登録（公開日はいちばん早い日）
  - ジャケットとアーティスト写真は **ChatGPT** で作る（`generate_visuals.py` は当面使わない）
  - 材料：`debut_kit/2026-10-11/`（`kit.json` に曲名・Suno の指示文・歌詞、`chatgpt_prompts.json` に画像の依頼文）
- 設定書のデビュー週は 2026-10-26（自動の配信枠は 10/28）。手作業の 5 曲が出たら、この枠を 2 曲目として作り直す

## 置き場所

| もの | 場所 |
|---|---|
| アーティストの管理表（正本） | Google スプレッドシート「アーティスト情報」 https://docs.google.com/spreadsheets/d/1q_Zfo6kW4PNJRQjcNUJGzN9gfBNWJqrBxBR6PnEPSAA/edit |
| 詰まっているところの一覧（進み具合） | 同じスプレッドシートの「詰まっているところ」タブ |
| デビュー 5 曲の作業書・ChatGPT の依頼文 | Google ドライブ「Postdog Records デビュー 2026-10」フォルダ https://drive.google.com/drive/folders/1wYCdLFFFy7w2y23arVeCcQk1_BRYpqx- |
| コピーボタン付きの作業台 | https://claude.ai/artifact/7WEKVdVL4Bg2HSN9Dfmkbq |
| 決めたこと | `docs/12_decisions.md`（1〜7） |
| 管理表の使い方 | `docs/13_artist_book.md` |

## 詰まっているところ（2026-10-13 時点）

1. Suno のプラン … **完了**（Pro）
2. DistroKid のプラン（アーティスト名を 5 つ以上持てるか）… オーナーが確認中
3. 名前の重複確認（Postdog Records と 5 組）… 未着手。Mac なら `python3 scripts/check_names.py --all` で照合できる（クラウドからは届かなかった）
4. Suno で 5 曲 … 未着手
5. ChatGPT でジャケットと写真 … 未着手。届いた画像は 3000×3000・RGB・10MB 未満の JPG か PNG に整える
6. テイク選び … 未着手
7. DistroKid に登録 … 未着手
8. 配信後に ISRC と選んだテイクを記録 … 未着手
9. 10/28 の自動配信枠の調整 … 待ち
10. 参考曲 11 曲以上の購入と解析 … 未着手（デビューには不要）
11. 自動化の鍵の設定（`python3 scripts/setup_keys.py`）… 未着手（デビューには不要）
12. 作業を Mac に移す … このメモ

## 守ること

- オーナーへの返事は日本語。専門用語はやさしく言い換え、何をしているかを日本語で書く
- 実在のアーティスト名・機種名は、Suno の指示文と画像の依頼文に入れない（プロフィールや管理表の「影響」「好きなアーティスト」の欄だけ）
- 鍵（API キー）は `.env` だけに書く。会話に貼らない・コミットしない
- DistroKid にテスト用の曲を上げない
- 管理表の「採用」はオーナーが決める。機械が足すのは「提案」の行だけ
- PR #5（このブランチ）は本体に合流させない（公開サイトに music-label が出てしまうため）。いずれ非公開の別リポジトリに切り出す（docs/12 の 1.）

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
| アーティストの管理表（唯一の正本） | 外付け SSD「DS72」の「Postdog Records／01_管理表／アーティスト情報.xlsx」。SSD をつないでいないときは読み書きしない。`music-label/artists.xlsx` は最初の 1 回だけ使う見本。Google スプレッドシートは移行前の版 |
| 楽曲管理シート | 同じ「01_管理表／楽曲管理.xlsx」。曲名・アーティスト・フェーズ・歌詞・Suno の指示・ISRC など 1 曲 1 行（`song_book.py` が自動で書き直す。ISRC・UPC・配信 URL・メモ・配信予定日はオーナーが書く） |
| 詰まっているところの一覧（進み具合） | アーティスト情報.xlsx の「詰まっているところ」タブ |
| アーティストの情報・写真・ジャケット・楽曲 | SSD の「Postdog Records／02_アーティスト／<アーティスト名>／01_情報・02_写真・03_ジャケット・04_楽曲」（自動で保管。`python3 scripts/archive_sync.py`）。Google から移すものは「09_移行前」へ |
| ローカル保管庫（公開セット） | Mac の「music-label／ローカル保管庫」。SSD と同じ並びで、SSD が無くても先にここへ入る（容量は増えないハードリンク）。`04_楽曲／<週>／` に音源・ジャケット・登録シートがそろい、ここから DistroKid に登録できる。SSD をつなぐと `archive_sync.py` が写す |
| 参考データベース | SSD の「データベース」などの名前のフォルダ（Excel・CSV・SQLite・解析シート）。`python3 scripts/reference_db.py --show` で読み方を確認。毎週のブリーフ作りの最初に自動で取り込み、傾向は `out/reference_db/digest.md` |
| デビュー 5 曲の作業書・ChatGPT の依頼文 | SSD の「Postdog Records／03_作業書／デビュー_2026-10-11／」（作業書の PDF・kit.json・chatgpt_prompts.json）。元は `music-label/debut_kit/2026-10-11/`。Google ドキュメントは移行前の版 |
| コピーボタン付きの作業台 | https://claude.ai/artifact/7WEKVdVL4Bg2HSN9Dfmkbq |
| 決めたこと | `docs/12_decisions.md`（1〜15） |
| 管理表の使い方 | `docs/13_artist_book.md` |
| 保管の現状と見直す点（SSD） | `docs/14_storage.md` |

## 詰まっているところ（2026-10-13 時点。最新は管理表の「詰まっているところ」タブ）

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
python3 scripts/kit_images.py                  # 5 組 × 3 種類を自動で作り、3000×3000 に整え、SSD の 02_アーティスト/<アーティスト名>/ に保管
python3 scripts/kit_images.py --kind photo2    # 2 枚目のアーティスト写真だけ（全身・サングラス・被り物・イラスト）
python3 scripts/kit_images.py --redo           # 作り直す（前の画像は手元にも SSD にも _old を付けて残る）
```

## 毎週の自動の流れを試す（Mac のターミナル）

```
cd music-label
python3 scripts/trial.py --all --skip-youtube --cleanup-after   # 全組を試験用の週で通し、終わったら跡を消す（結果は out/trial/report_all.md）
python3 scripts/schedule.py install                             # 試験が ✕ 0 件なら、定期実行を Mac に登録
python3 scripts/archive_sync.py --check                         # SSD の場所と、まだ保管していないものを見る
```
- 人の作業として残るのは 2 つ：Suno で作ってテイクを out/takes/<週>_<組>/ に置くこと、DistroKid への登録（どちらも規約で自動操作が禁止）
- 進み具合は out/weeks/<週>/status.md と out/dashboard.html

## 守ること

- オーナーへの返事は日本語。専門用語はやさしく言い換え、何をしているかを日本語で書く
- 実在のアーティスト名・機種名は、Suno の指示文と画像の依頼文に入れない（プロフィールや管理表の「影響」「好きなアーティスト」の欄だけ）
- 鍵（API キー）は `.env` だけに書く。会話に貼らない・コミットしない
- DistroKid にテスト用の曲を上げない
- 管理表の「採用」はオーナーが決める。機械が足すのは「提案」の行だけ
- 新しい名前を出すときは、同じ名前のアーティストがいないかを Claude が先に Web 検索で調べてから見せる（オーナーに確認を頼まない）
- PR #5（このブランチ）は本体に合流させない（公開サイトに music-label が出てしまうため）。いずれ非公開の別リポジトリに切り出す（docs/12 の 1.）

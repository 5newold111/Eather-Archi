# 15. 公開の流れ（Suno の「いいね」→ DistroKid の登録ボタンまで）

決めた経緯は `docs/12_decisions.md` の 17。仕組みは `scripts/publish.py`。

## 1. 誰が何をするか

| 段 | やること | 担当 |
|---|---|---|
| 1 | 曲名・スタイル・入れないもの・歌詞を用意する | 自動（ブリーフ／デビュー曲は作業セット） |
| 2 | Suno に貼って「Create」を押す | **オーナー**（`python3 scripts/publish.py suno --open` のページからコピーして貼る） |
| 3 | 2 つのテイクを聴いて、気に入った方に「いいね」→ その 1 本をダウンロードして「ダウンロード → SUNO楽曲」に入れる | **オーナー** |
| 4 | どの曲かを見分けて取り込む → 音量を整える → ジャケット → DistroKid の下書き → 公開セット → 楽曲管理シート → Mac に「登録できます」と通知 | 自動（定期実行 intake。フォルダに入ると数分以内） |
| 5 | DistroKid の画面に下書きを入れる（項目の入力・音源とジャケットの添付・配信日は最短） | Claude（オーナーの Chrome で。オーナーが見ている前で） |
| 6 | 内容を確かめて、最後の登録ボタンを押す | **オーナー** |
| 7 | 登録したことを記録する（`python3 scripts/publish.py registered <曲 ID>`） | Claude かオーナー |
| 8 | 配信が始まったかを毎日確かめ、ISRC・UPC・配信 URL を楽曲管理シートに入れて「8 配信中」にする | 自動（定期実行 release。Spotify の鍵が要る） |
| 9 | デビューから 6 か月ごとに、同じ人物のままアーティスト写真を新しくする | 自動（定期実行 monthly。`refresh_photos.py`） |

Suno の「Create」と DistroKid の登録ボタンを機械が押さないのは、両サービスの利用規約が自動の操作を禁じている（Suno）か、
アカウントを理由なく止められる（DistroKid。止まると配信中の全曲が消える）ため。

## 2. ファイル名の決まり

| もの | 置き場所（ローカル保存と SSD で同じ並び） | 名前 |
|---|---|---|
| Suno からダウンロードした曲 | ダウンロード/SUNO楽曲/（中にフォルダを作ってもよい） | Suno が付けた曲名のまま（例 `Due East.wav`）。同じ曲名がほかの組にもあるときは、アーティスト名のフォルダに入れる |
| ジャケット | `<アーティスト名>/03_ジャケット/` | `<アーティスト名>_<曲名>_1.jpg`。作り直すと `_2`・`_3` |
| 公開セット | `<アーティスト名>/04_楽曲/<曲名>/` | `<アーティスト名>_<曲名>_master.wav`（配信用）・`_1.jpg`（ジャケット）・`_選んだテイク.wav`・`_歌詞.txt`・`_DistroKid下書き.md` |
| 刷新した写真 | `<アーティスト名>/02_写真/` | `<アーティスト名>_photo_<年-月>.jpg`・`_photo2_<年-月>.jpg`（前の写真は消さない） |

いいねを変えて別のテイクをダウンロードし直したときは、新しいほうに差し替わる（前の版は `_old` で残る）。

## 3. 公開日

デビュー曲は「準備ができたらすぐ」：DistroKid の画面で選べるいちばん早い日にする。
登録すると、その配信週がその組のデビュー週になり、2 曲目以降の毎週の制作はそこから数えて始まる。
デビュー曲を登録するまで、その組の毎週の曲は作らない（10/28 の自動の枠と重ならないように）。

注意：Spotify の編集部のプレイリストに推薦（ピッチ）するには、配信日の 7 日以上前の登録が要る。最短の日付にすると推薦はできない。

## 4. Claude が DistroKid に下書きを入れる手順（Claude in Chrome）

1. オーナーが Mac の Chrome で DistroKid にログインし、Claude in Chrome をつなぐ
2. Claude が `python3 scripts/publish.py draft <曲 ID>` の内容を読み、Upload 画面の項目を上から入れる。
   音源とジャケットは公開セットのファイルを添付する。配信日は選べる最短の日付
3. 最後の登録ボタンの手前で止まり、オーナーに「確かめて押してください」と伝える（Claude は押さない）
4. オーナーが押したら、Claude が `publish.py registered <曲 ID> --date <配信日>` で記録する

## 5. 使うコマンド

ふだんはダッシュボード（ブラウザで http://localhost:8765）のボタンで同じことができる。

```
python3 scripts/publish.py suno --open    # Suno に貼る内容のページ
python3 scripts/publish.py status         # 曲ごとの進み具合
python3 scripts/publish.py scan           # すぐに取り込みたいとき（ふだんは自動）
python3 scripts/publish.py draft PDR-001  # DistroKid の下書き
python3 scripts/publish.py registered PDR-001 --date 2026-10-22
python3 scripts/publish.py track          # 配信の確認（ふだんは毎日自動）
python3 scripts/refresh_photos.py --list  # 写真の次の刷新日
```

## 6. Mac で最初に 1 回だけやること

| やること | コマンド・場所 |
|---|---|
| 音量を整える道具を入れる | `brew install ffmpeg` |
| Spotify の鍵と、作詞作曲者の欄の本名を入れる | `python3 scripts/setup_keys.py --only spotify,owner` |
| 「ダウンロード」を自動の処理から読めるようにする | システム設定 → プライバシーとセキュリティ → フルディスクアクセス に python3 を追加 |
| 定期実行に取り込み・配信の確認・ダッシュボードを加える | `python3 scripts/schedule.py install`（そのあと http://localhost:8765 をブックマーク） |

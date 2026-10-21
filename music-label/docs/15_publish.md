# 15. 公開の流れ（Suno の「いいね」→ DistroKid の登録ボタンまで）

決めた経緯は `docs/12_decisions.md` の 17。仕組みは `scripts/publish.py`。

## 1. 誰が何をするか

| 段 | やること | 担当 |
|---|---|---|
| 1 | 曲名・スタイル・入れないもの・歌詞を用意する | 自動（ブリーフ／デビュー曲は作業セット） |
| 2 | Suno の入力欄を埋める → 内容を確かめて「Create」を押す | 入力は**システム**（ダッシュボードの「Suno に入力する」で専用のブラウザが開き、欄が埋まる。`browser_fill.py suno`）、**Create はオーナー**（決定 22） |
| 3 | 2 つのテイクを聴いて、気に入った方に「いいね」→ その 1 本を SSD の「Postdog Records → 楽曲情報」にダウンロードする（Suno のアプリの保存先をここに。SSD が無いときは「ダウンロード → SUNO楽曲」） | **オーナー** |
| 4 | どの曲かを見分けて取り込む → 音量を整える → ジャケット → DistroKid の下書き → 公開セット → 楽曲管理シート → Mac に「登録できます」と通知 | 自動（定期実行 intake。フォルダに入ると数分以内） |
| 5 | DistroKid の画面に下書きを入れる（項目の入力・音源とジャケットの添付・配信日は最短） | システム（ダッシュボードの「DistroKid に入力する」。`browser_fill.py distrokid <曲 ID>`） |
| 6 | 内容を確かめて、最後の登録ボタンを押す | **オーナー** |
| 7 | 登録したことを記録する（ブラウザの案内の「登録した」。ダッシュボードの「DistroKid に登録した」でも同じ） | オーナー（ワンクリック） |
| 8 | 配信が始まったかを毎日確かめ、ISRC・UPC・配信 URL を楽曲管理シートに入れて「8 配信中」にする | 自動（定期実行 release。Spotify の鍵が要る） |
| 9 | デビューから 6 か月ごとに、同じ人物のままアーティスト写真を新しくする | 自動（定期実行 monthly。`refresh_photos.py`） |

Suno の「Create」と DistroKid の登録ボタンを機械が押さないのは、両サービスの利用規約が自動の操作を禁じている（Suno）か、
アカウントを理由なく止められる（DistroKid。止まると配信中の全曲が消える）ため。

## 2. ファイル名の決まり

| もの | 置き場所（ローカル保存と SSD で同じ並び） | 名前 |
|---|---|---|
| Suno からダウンロードした曲 | SSD の `Postdog Records/楽曲情報/`（Suno のアプリの保存先。SSD が無いときは `ダウンロード/SUNO楽曲/`） | ダウンロードしたときの名前でよい（例 `Due East.wav`）。取り込むと `<曲名>_<アーティスト名>_<生成日>.wav` に自動で変わる（決定 20）。同じ曲名がほかの組にもあるときは、アーティスト名のフォルダに入れる |
| ジャケット | `<アーティスト名>/03_ジャケット/` | `<アーティスト名>_<曲名>_1.jpg`。作り直すと `_2`・`_3` |
| 公開セット | `<アーティスト名>/04_楽曲/<曲名>/` | `<曲名>_<アーティスト名>_<生成日>.wav`（配信用）・`…_Suno元データ.wav`・`<アーティスト名>_<曲名>_1.jpg`（ジャケット）・`_歌詞.txt`・`_DistroKid下書き.md` |
| 刷新した写真 | `<アーティスト名>/02_写真/` | `<アーティスト名>_photo_<年-月>.jpg`・`_photo2_<年-月>.jpg`（前の写真は消さない） |

いいねを変えて別のテイクをダウンロードし直したときは、新しいほうに差し替わる（前の版は `_old` で残る）。

## 3. 公開日

デビュー曲は「準備ができたらすぐ」：DistroKid の画面で選べるいちばん早い日にする。
登録すると、その配信週がその組のデビュー週になり、2 曲目以降の毎週の制作はそこから数えて始まる。
デビュー曲を登録するまで、その組の毎週の曲は作らない（10/28 の自動の枠と重ならないように）。

注意：Spotify の編集部のプレイリストに推薦（ピッチ）するには、配信日の 7 日以上前の登録が要る。最短の日付にすると推薦はできない。

## 4. DistroKid に下書きを入れる手順（システムが自動で。決定 22）

1. ダッシュボードで、その曲の「DistroKid に登録」のマスを押し、「DistroKid に入力する（ブラウザが開きます）」を押す
2. 専用のブラウザが DistroKid の登録画面を開き、`publish.py draft <曲 ID>` の内容を上から入れ、音源とジャケットを添付する（初回だけログインを求められる）
3. 右下の案内に、手で選ぶ項目（Explicit・AI の申告など）と、入れられなかった項目が値と一緒に並ぶ。確かめて、**最後の登録ボタンを押す**（押すのはオーナーだけ）
4. 案内で配信日を選んで「登録した」を押すと記録される（`publish.py registered <曲 ID> --date <配信日>` と同じ）

## 5. 使うコマンド

ふだんはダッシュボード（ブラウザで http://localhost:8765）のボタンで同じことができる。

```
python3 scripts/browser_fill.py suno     # Suno に自動で入力（作曲待ちの全曲。曲 ID を付けるとその曲だけ）
python3 scripts/browser_fill.py distrokid PDR-001  # DistroKid に下書きを自動で入力
python3 scripts/publish.py suno --open    # Suno に貼る内容のページ（手で貼るとき）
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
| ブラウザで自動入力する部品を入れる | ダッシュボードの「ブラウザ入力の部品を入れる」（`python3 -m pip install --user playwright` と `python3 -m playwright install chromium`） |
| 専用のブラウザで Suno と DistroKid にログインしておく | ダッシュボードの「Suno にログイン（初回だけ）」「DistroKid にログイン（初回だけ）」（`browser_fill.py login suno`） |

## 7. Suno に入力する手順（システムが自動で。決定 22）

1. ダッシュボードの「作曲待ちの曲を Suno に入力する」（1 曲だけなら、その曲の「Suno で作曲」のマス →「Suno に入力する」）を押す
2. 専用の Chrome が Suno の作曲画面を開き、「高度な」に切り替えて、上から 歌詞 → スタイル →（その他のオプションを開いて）除外するスタイル・ボーカルの性別・Weirdness・Style Influence → 曲のタイトル を入れ、読み返して確かめてから止まる（組ごとの設定は `templates/suno_options.json`。見つからない欄は、案内に従って 1 回クリックして教える。次からは自動）
3. 内容を確かめて **Create を押す**（押すのはオーナーだけ）→ 案内の「次の曲へ」で次の曲に進む（最後は「終わる」）
4. できたテイクを聴き、気に入った方に「いいね」→ その 1 本を SSD の「楽曲情報」へダウンロード。ここから先は自動

Claude に頼む方法（`publish.py suno-request` の頼み文を Claude in Chrome に貼る）は予備として残している。

Suno の公式 API に通れば、3 の Create も自動にできる（申し込みは `docs/16_suno_api_application.md`）。

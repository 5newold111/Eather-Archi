# DistroKid の登録の流れ（2026-10-25 時点のまとめ）

決めたことの細かい経緯は `docs/12_decisions.md`（決定 37〜67）。ここは「いまどう動くか」だけをまとめる。

## 全体の流れ（1 曲）

| 段階 | 中身 | だれが | ダッシュボードのマス |
|---|---|---|---|
| A. 登録の準備 | 取り込み → 音量 → ジャケット → 下書き → 公開セット（配信用の歌詞も） | 自動 | 仕上げ |
| B. 登録画面への入力 | 下の「入力の 7 つの工程」 | 自動（ブラウザ） | DistroKid に登録 |
| C. 確認と登録 | 宣誓のチェック → Continue → 案内の「登録した」 | **Gon さん** | DistroKid に登録 |
| D. 歌詞を出す | 歌詞の画面に配信用の歌詞を入れる → Submit → 「出した」 | 自動（入力）＋**Gon さん**（Submit） | 今やること |
| E. 配信を確かめる | Spotify・Apple Music・Deezer で見つかったら配信中にする | 自動（毎朝）／急ぐときは Gon さん | 配信待ち |
| F. 配信のあと | アーティスト写真・2 曲目からの登録 | **Gon さん**（保存・登録）＋自動 | 今やること |

## B. 入力の 7 つの工程（ポップアップの上部に「工程 k/7」と出る。決定 65）

| 工程 | 入れるもの | 決め方 |
|---|---|---|
| 1 | DistroKid の登録画面を開く | 専用の Chrome（ログインは覚えている）。about:blank で止まったら 1 回開き直す（決定 63） |
| 2 | アーティスト名・曲名 | 入れたあと Tab で欄を離れ、DistroKid が同じ名前を探し終えるまで待つ（決定 42） |
| 3 | 丸（はい／いいえ）・AI で作った部分・Artist already in | 下の「丸」の表 |
| 4 | 配信日・言語・ジャンル・音源・ジャケット・Songwriter | 下の「欄」の表 |
| 5 | Track credits（Performer・Producer）・ジャンルの箱 | 下の「credits」の表 |
| 6 | 読み返して、DistroKid に戻された欄・丸を入れ直す | 最大 3 回。ジャンルはページの中で見張り、書き換えられたら戻す（決定 49） |
| 7 | Gon さんの確認待ち | 待っている間も 2 秒ごとに見張り、差し戻されたら入れ直す（決定 39・60） |

### 丸（はい／いいえ）

| 質問 | 選ぶもの |
|---|---|
| 前に出したことがある？ | No |
| 曲名に featured artist を出す？ | No |
| バージョン | ふつうの版 |
| Dolby Atmos | No |
| カバー曲？ | No（自分で書いた曲） |
| Explicit（不適切な言葉） | No（Clean） |
| Clean 版が別にある？ | No |
| インスト？ | HOLLOWPLAN だけ「歌詞なし」、ほかは「歌詞あり」 |
| AI を使った？ | Yes |
| AI で作った部分（Which parts … AI-generated?） | 歌のある曲：Lyrics・Music・All of the audio／インスト：Music・All of the audio。小窓の Save まで押す（決定 61） |
| 試聴の始まり | サービスにまかせる |
| Artist already in Spotify / Apple Music / YouTube Music / Instagram / Facebook? | デビュー曲：すべて No（新しいページ）。確認の小窓は、新しいページの話なら OK を押す（決定 49・50）。2 曲目から：Spotify は記録したアーティストのページで Yes、ほかは DistroKid が 0 件と出したときだけ No |

### 欄

| 欄 | 入れるもの |
|---|---|
| 配信日 | デビュー曲・集中配信の曲：選べるいちばん早い日／毎週の曲：予定の日 |
| 言語 | 曲の言語（英語など） |
| Primary genre・Secondary genre | DistroKid の一覧の名前（Pop など 44 種）。欄の名前の選ぶ箱 → 打って選ぶ箱 → 押すと一覧が開く箱の順に、入るまで試す（決定 44・61・67） |
| 音源・ジャケット | 公開セットのファイル |
| Songwriter | 本名（`.env` の `OWNER_LEGAL_NAME`。ローマ字の「名 姓」）・役割 Music and lyrics（インストは Music） |

### credits（Apple Music の決まり：Performer と Producer を 1 人ずつ以上）

| | 名前 | 役割 |
|---|---|---|
| Performer（歌のある 4 組） | アーティスト名 | Singing & vocals（決定 62） |
| Performer（HOLLOWPLAN） | 本名 | Performer |
| Producer（全組） | 本名 | Producer |

- 「Additional requirements」の欄で「＋」を押すまで出ない作りなら、＋を押してから入れ、小窓の Save まで押す（決定 66）
- 役割の箱は、名前の欄と同じ行の箱を使う（画面の上からの順番では選ばない。決定 61）

## C. Gon さんが押すもの（システムは押さない）

1. 宣誓のチェック（Important checkboxes。「Non-standard cApiTaliZatiOn detected」を含む）。文を読んで、当てはまるときだけ
2. Extras（有料の追加）・Discovery Pack は押さない
3. **Continue**（最後の登録ボタン）
4. 右下の案内で、配信日を選んで「**登録した**」（押し忘れたら、ダッシュボードのポップアップの緑の「✓ DistroKid に登録できた → 次へ進める」でも記録できる。決定 58）

## D〜F. 登録のあと

- **歌詞**（決定 52）：Suno の札（[Verse] など）と音の指示を外し、DistroKid の決まりに合わせた `歌詞_配信用.txt` を作る。登録のあと「続けて歌詞を出す」で歌詞の画面に入れる → Gon さんが曲を聞きながら確かめて Submit → 「出した」
- **配信の確認**（決定 57）：鍵なしで毎朝確かめる（Apple Music・Deezer・song.link → Spotify の曲のページ）。見つかったら配信中にし、ISRC・UPC・各サイトの住所・Spotify のアーティストのページを記録する。急ぐときは「配信待ち」のマスの「✓ 配信を見つけた → 次へ進める」に住所を貼る（決定 55）
- **2 曲目から**（決定 53）：その組の Spotify のアーティストのページがわかるまで、DistroKid の登録は待つ（デビュー曲と同じページにまとめるため）
- **アーティスト写真**（決定 32・38）：配信が始まると「今やること」に出る。Spotify for Artists・Apple Music for Artists・Amazon で、保存は Gon さん

## 続けて登録する（決定 51）

- ダッシュボードの「登録を待つ N 曲を続けて DistroKid に入力する」、またはターミナルで `python3 scripts/browser_fill.py distrokid all`
- デビュー曲を先に、曲 ID の順。1 曲ごとに Continue と「登録した」を押すと次の曲へ。ポップアップの上部に「▶ 2/5 曲目」と出る

## 差し戻されたとき（Validation Errors）

- 見張りがエラーの文を読み、丸・欄・credits・ジャンル・AI で作った部分を入れ直す。Gon さんは宣誓を確かめて、もう一度 Continue を押すだけ
- 画面の部品の一覧を `out/browser/distrokid_parts_<曲 ID>_<日時>.txt` に自動で残し、ログに「記録：差し戻されたときの画面 → …」「記録：入れ直したあとの画面 → …」を出す。直らないときは、この 2 行を Claude に貼る

| よく出たエラー | いまの対処 |
|---|---|
| Select the primary genre … | ジャンルを 3 つの作りの箱で入るまで試す（決定 67） |
| Please enter a valid Apple Artist URL … or choose "No" | Apple の No を選び、確認の小窓に OK。取り消されたら 3 回までやり直す（決定 50） |
| Add track credits to meet Apple requirements | ＋から credits を出して入れる・役割の箱を取り違えない（決定 61・66） |
| Please select at least one option for what was AI-generated | AI で作った部分に自動で答える（決定 61） |
| Songwriter の名前 | `.env` の `OWNER_LEGAL_NAME` をローマ字の「名 姓」に（`python3 scripts/setup_keys.py --only owner`） |

## うまく動かないとき

| 症状 | 対処 |
|---|---|
| 「ブラウザで入力する部品がありません」 | 案内に出るコマンドを 1 回実行（Python の版が上がると部品が見えなくなる。決定 59） |
| about:blank のまま | 専用の Chrome を ⌘Q で閉じてから、もう一度押す。だめならログの「専用の Chrome の準備でつまずきました（…）」を Claude に貼る（決定 63） |
| 「専用の Chrome につなげませんでした」 | Chrome をすべて ⌘Q で閉じて、もう一度押す（決定 46） |
| ポップアップにボタンが出ない | 最新版を取り込んで、ダッシュボードを読み込み直す（決定 58） |

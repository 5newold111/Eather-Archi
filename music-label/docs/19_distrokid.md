# DistroKid の登録の流れ（2026-10-25 時点のまとめ）

決めたことの細かい経緯は `docs/12_decisions.md`（決定 37〜68）。ここは「いまどう動くか」だけをまとめる。

## 全体の流れ（1 曲）

| 段階 | 中身 | だれが | ダッシュボードのマス |
|---|---|---|---|
| A. 登録の準備 | 取り込み → 音量 → ジャケット → 下書き → 公開セット（配信用の歌詞も） | 自動 | 仕上げ |
| B. 登録画面への入力 | 下の表の 1〜21 を画面の上から順に | 自動（ブラウザ） | DistroKid に登録 |
| C. 確認と登録 | 宣誓のチェック → Continue（「登録した」の記録は自動） | **Gon さん** | DistroKid に登録 |
| D. 歌詞を出す | 歌詞の画面に配信用の歌詞を入れる → Submit → 「出した」 | 自動（入力）＋**Gon さん**（Submit） | 今やること |
| E. 配信を確かめる | Spotify・Apple Music・Deezer で見つかったら配信中にする | 自動（毎朝）／急ぐときは Gon さん | 配信待ち |
| F. 配信のあと | アーティスト写真・2 曲目からの登録 | **Gon さん**（保存・登録）＋自動 | 今やること |

## B・C. 登録画面を上から順に（本物の画面の並び。ポップアップの一覧も、この順に ✓ ／ ✕ ／ － で出る。決定 68）

| 番号 | 画面の項目 | 入れるもの・選ぶもの | だれが |
|---|---|---|---|
| — | Stores・Number of songs | DistroKid の初期設定のまま（全ストア・1 曲） | 触らない |
| 1 | 前に出したことがある？ | No | 自動 |
| 2 | アーティスト名 | 組の名前。入れたら欄を離れ、DistroKid が同じ名前を探し終えるまで待つ | 自動 |
| 3 | Artist already in（Spotify・Apple Music・YouTube Music・Instagram・Facebook） | デビュー曲：5 つとも No。確認の小窓（新しいページの話）に OK。取り消されたら 3 回まで選び直し、ページの中でも見張る（決定 49・50）。2 曲目から：Spotify は記録したページで Yes、ほかは 0 件と出たときだけ No | 自動（2 曲目からの候補選びは Gon さん） |
| 4 | 配信日 | デビュー曲・集中配信：選べるいちばん早い日／毎週の曲：予定の日 | 自動 |
| — | Record label | Postdog Records（DistroKid が入れてある） | 触らない |
| 5 | 言語 | 曲の言語 | 自動 |
| 6 | Primary genre | DistroKid の一覧の名前。選ぶ箱 → 打って選ぶ箱 → 押すと一覧が開く箱の順に、入るまで。書き換えられたら戻す（決定 49・61・67） | 自動 |
| 7 | Secondary genre | 組の音に近い順の候補。一覧に無ければ空のまま（任意） | 自動 |
| 8 | ジャケット | 公開セットの画像 | 自動 |
| 9 | 曲名 | 曲名 | 自動 |
| 10 | featured artist を曲名に出す？ | No | 自動 |
| 11 | バージョン | ふつうの版 | 自動 |
| 12 | 音源 | 公開セットの音源 | 自動 |
| 13 | Dolby Atmos | No | 自動 |
| 14 | カバー曲？ | No（自分で書いた曲） | 自動 |
| 15 | Songwriter | 役割 Music and lyrics（インストは Music）・本名（`.env` の `OWNER_LEGAL_NAME`。ローマ字の「名 姓」） | 自動 |
| 16 | Explicit | No | 自動 |
| 17 | Clean 版が別にある？ | No | 自動 |
| 18 | インスト？ | HOLLOWPLAN だけ「歌詞なし」、ほかは「歌詞あり」 | 自動 |
| 19 | AI を使った？ | Yes → 作った部分：歌のある曲 Lyrics・Music・All of the audio／インスト Music・All of the audio。小窓の Save まで（決定 61） | 自動 |
| 20 | 試聴の始まり | サービスにまかせる | 自動 |
| — | Price | $0.99（初期設定のまま） | 触らない |
| 21 | Track credits（Apple Music の Additional requirements） | ＋を押して出た行の「役割の箱」と、その下の「名前の欄」に入れる（決定 66・70）。Performer：歌のある 4 組はアーティスト名＋Singing & vocals、HOLLOWPLAN は本名＋Performer。Producer：本名＋Producer。役割の箱は名前と同じ行（決定 61・62） | 自動 |
| — | Extras・Discovery Pack | チェックしない | **押さない** |
| 22 | 宣誓のチェック（Non-standard cApiTaliZatiOn を含む） | 文を読んで、当てはまるものだけ | **Gon さん** |
| 23 | Continue | 最後の登録ボタン | **Gon さん** |

- 入れ終わったら、上から読み返して、入っていない項目だけを上から入れ直す（最大 3 回）。待っている間も約 6 秒ごとに読み返して、DistroKid に戻された項目を黙って入れ直す。アーティスト名・ジャケット・音源は入れ直さない
- **「登録した」は自動**：Continue のあと、登録の画面から別の画面に変わり、差し戻しが無ければ、配信日（画面の欄の値）で登録を記録する。違っていたらダッシュボードの「← 戻る」で戻せる。自動で記録されないときは、案内の「登録した」か、ポップアップの緑の「✓ DistroKid に登録できた → 次へ進める」（決定 58）

## D〜F. 登録のあと

- **歌詞**（決定 52）：Suno の札（[Verse] など）と音の指示を外し、DistroKid の決まりに合わせた `歌詞_配信用.txt` を作る。登録のあと「続けて歌詞を出す」で歌詞の画面に入れる → Gon さんが曲を聞きながら確かめて Submit → 「出した」
- **配信の確認**（決定 57）：鍵なしで毎朝確かめる（Apple Music・Deezer・song.link → Spotify の曲のページ）。見つかったら配信中にし、ISRC・UPC・各サイトの住所・Spotify のアーティストのページを記録する。急ぐときは「配信待ち」のマスの「✓ 配信を見つけた → 次へ進める」に住所を貼る（決定 55）
- **2 曲目から**（決定 53）：その組の Spotify のアーティストのページがわかるまで、DistroKid の登録は待つ（デビュー曲と同じページにまとめるため）
- **アーティスト写真**（決定 32・38）：配信が始まると「今やること」に出る。Spotify for Artists・Apple Music for Artists・Amazon で、保存は Gon さん

## 続けて登録する（決定 51）

- ダッシュボードの「登録を待つ N 曲を続けて DistroKid に入力する」、またはターミナルで `python3 scripts/browser_fill.py distrokid all`
- デビュー曲を先に、曲 ID の順。1 曲ごとに宣誓と Continue を押すと（登録の記録は自動）、次の曲へ。ポップアップの上部に「▶ 2/5 曲目」「工程 k/23」と出る

## 差し戻されたとき（Validation Errors）

- 見張りがエラーの文を読み、上の表の項目を上から読み返して、入っていないものを入れ直す。Gon さんは宣誓を確かめて、もう一度 Continue を押すだけ
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

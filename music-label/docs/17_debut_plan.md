# 17. 残り 4 組のデビューの段取り（2026-10-22〜）

DECLAN FAY「Side B」で通した流れ（決定 22〜26）を、残り 4 組で 1 組ずつ繰り返す。
ダッシュボード（http://localhost:8765）の「デビュー」タブで、組ごとにマスを押して進める。

## 1. 順番と日にち

Suno のロボット確認が出にくいように、**1 日 2 組まで・組と組の間は 1 時間以上あける**。

| 順 | 日 | 組・曲 | この組で気をつけること |
|---|---|---|---|
| 1 | 10/22 | VERNALIGHT「Due East」（PDR-003） | 声の性別は「女」。Side B と同じ形なので、いちばん手順どおりに進むはず |
| 2 | 10/22（1 時間以上あけて） | SOFT CANTILEVER「Two Lanes Over」（PDR-004） | 声の性別は「男」。女性のハモりはスタイル欄の言葉で出す |
| 3 | 10/23 | HOLLOWPLAN「Services at Three」（PDR-002） | 歌詞を歌わず、声の切れ端だけ。声の性別は「選ばない」。DistroKid の Instrumental は「No」（声が入っているため） |
| 4 | 10/23（1 時間以上あけて） | TIDEWOOL「The Low Part」（PDR-005） | 男女 2 人。声の性別は「選ばない」 |

声の性別を選ぶ組（女・男）を先に、選ばない組をあとにした。選ばない組は、Suno で両方を外せるかを見ながら進めるため。

## 2. 1 組ぶんの流れ（Side B と同じ）

| # | 誰が | やること | どこで |
|---|---|---|---|
| 1 | Gon さん | 「Suno に入力する」を押す | ダッシュボード → その組の「Suno で作曲」のマス |
| 2 | 自動 | 歌詞・スタイル・除外・声の性別・Duration・Max Mode・Weirdness・Style Influence・Variety・Personalize・曲名が入り、読み返して確かめる | 専用の Chrome |
| 3 | Gon さん | 中身を確かめて **Create**（ロボット確認が出たら自分でチェック） | Suno |
| 4 | Gon さん | 2 つを聴いて、気に入った方に**いいね** → その 1 本を**ダウンロード**（SSD の「楽曲情報」に入る） | Suno |
| 5 | 自動 | 取り込み・音量・ジャケット・DistroKid の下書き・公開セット。確認ページが開く | 「公開の準備」ページ |
| 6 | Gon さん | ページでジャケット・音・下書きを確かめて「DistroKid に入力する」 | 「公開の準備」ページ |
| 7 | 自動 | アーティスト名・曲名・配信日・言語・ジャンル・サブジャンル・音源・ジャケットが入る | 専用の Chrome |
| 8 | Gon さん | 右下の案内の「画面で確かめて選ぶもの」を選ぶ（下の 3）→ **最後の登録ボタン** → 案内の「**登録した**」 | DistroKid |

## 3. DistroKid で Gon さんが選ぶもの（2026-10-22 からほぼ自動。決定 37）

システムが選ぶ：Track credits（Performer＝組の名前・Producer＝本名）・前に出したことがある？（No）・featured artist（No）・ふつうの版・Dolby Atmos（No）・自分で書いた曲・Explicit（No）・Clean 版（No）・インスト（歌詞あり／なし）・AI の申告（Yes）・試聴の始まり（サービスにまかせる）・Songwriter の役割（Music and lyrics）と本名（名・姓）・Artist already in 〜?（DistroKid が「0 件」と出す組は No）

| Gon さんに残るもの | やること |
|---|---|
| Important checkboxes（5 つの宣誓） | 文を読んで自分でチェック（法的な約束なので本人が押す。Suno の Pro で作った曲か、を必ず確かめる） |
| Extras・Discovery Pack | チェックしない |
| AI の申告で Yes のあとに質問が出たら | 使ったもの（Suno で作った部分）を正直に選ぶ |
| 最後の「Continue」 | Gon さんが押す |

| 組 | Performer の役割 | 1 つ目のジャンル | サブジャンル・2 つ目（自動で近いものを選ぶ） |
|---|---|---|---|
| VERNALIGHT | Vocals | Pop | Synth-Pop → Indie Pop → Electropop |
| SOFT CANTILEVER | Vocals | Pop | City Pop → Indie Rock → Funk Pop |
| HOLLOWPLAN | Performer（声の切れ端・演奏。DistroKid の役割は Performer） | Dance | House → Lo-Fi House → Deep House |
| TIDEWOOL | Vocals | Singer/Songwriter | Acoustic Pop → Folk Pop → Indie Folk |

## 4. 始める前に 1 回だけ

- `cd ~/Eather-Archi && git pull`（10/22 までの直し：ジャンル・サブジャンルの自動、クレジットの出し分け など）
- ダッシュボードの「仕組みの詰まり」が空か確かめる。特に **OWNER_LEGAL_NAME（本名）** と **Spotify の鍵**（配信の確認と ISRC の記録に使う）
- 専用の Chrome が開いていれば ⌘Q で閉じてから始める

## 5. うまくいかないとき

| 出たもの | やること |
|---|---|
| Suno の右下に「入力が足りません」 | 「画面の部品をコピー」を押して Claude に貼る |
| Suno のロボット確認 | Gon さんがチェックする（システムは押さない）。続けて作らず時間をあける。何度も出て通らないときは、ふだんの Chrome か Safari で `http://localhost:8765/phone` を開き、「写す」ボタンで貼る（決定 34） |
| DistroKid で「欄が見つかりません」 | 箱そのものをクリックして教える。だめなら「この欄はとばす」で手で入れ、「画面の部品をコピー」を Claude に貼る |
| DistroKid の Validation Errors | 文をそのまま Claude に貼る |

## 6. 4 組が終わったら

- 毎朝の確認で配信が始まった組から「配信中」になり、ISRC・UPC・配信 URL・Spotify のアーティストのページの ID が記録される
- 各組の配信週がデビュー週になり、次の週から毎週の制作（定期配信）が始まる。ダッシュボードの「定期配信」タブに移る

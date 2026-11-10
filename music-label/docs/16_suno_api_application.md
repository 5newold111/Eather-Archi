# 16. Suno の公式 API への申し込み（決定 19）

Suno の作曲を「Create まで自動」にできるのは、Suno の公式の窓口（API）が使えるようになってから。
それまでは、Claude が入力欄を埋めて、オーナーが Create を押す（`docs/15_publish.md`）。

## 1. いまわかっていること（2026-10-20 時点）

| こと | 中身 |
|---|---|
| 公式 API | まだ一般には公開されていない。2026-07-01 に Suno の CPO（製品責任者）Jack Brody 氏が LinkedIn で「開発者 API を検討中。まず選んだ少数の提携先から始める」と投稿し、申し込みフォーム（Developer API Intake Form。Typeform）へのリンクを載せた |
| 申し込みの流れ | フォームで関心を伝える → Suno が少数の相手を選んで連絡する。料金・規約・開始時期は未発表 |
| 選ばれやすいもの | 報道によると「生成音楽で初めてできるようになる体験」をつくるアプリ |
| 第三者の「Suno API」 | Suno 公式ではない。規約上の危険があるので使わない |

フォームの正確な URL と質問の項目は、こちらからは確かめられなかった。下の回答文は、よくある質問（会社・用途・量・連絡先）に合わせて用意してある。

## 2. 申し込みの手順（オーナー）

1. LinkedIn で **Jack Brody（Suno）** のページを開き、2026 年 7 月 1 日ごろの投稿「Developer API Intake Form」のリンクを開く
   （見つからなければ、Suno の公式 X・LinkedIn・`platform.suno.com` の案内を探す）
2. 下の英語の回答文をコピーして、フォームの近い質問に貼る
3. 連絡先は **etherarchi@gmail.com**（レーベルの正しいアカウント）
4. 出したら、この会話か管理表の「詰まっているところ」に「申し込み済み（日付）」と書く

## 3. 回答文（英語。フォームに貼る用。下に日本語の意味）

**Company / project name**
> EtherArchi — Postdog Records (independent virtual-artist label, Japan)

**What are you building? (short)**
> An independent label that runs a small roster of original virtual artists, each with a written persona, sound rules and a visual world. We release one song per artist every week or two through DistroKid. Suno is the instrument at the center of every release; everything around it (briefs, lyrics, mastering, artwork, distribution metadata, release tracking) is already automated on our side.

**How would you use the Suno API?**
> For each scheduled release our pipeline writes a brief (style prompt, exclude styles, full lyrics with section tags) from the artist's persona and our reference notes. Through the API we would: (1) submit the generation with title, style, exclude styles and lyrics; (2) retrieve the two takes, metadata and WAV; (3) hand them to our owner, who listens and picks one; (4) master the chosen take to -14 LUFS / -1 dBTP and prepare the release. A human always makes the final musical choice and the final distribution submission. Today our owner pastes the briefs into the Suno web app by hand; the API would replace only that step.

**Why is this new / what does it unlock?**
> A label where each artist is a consistent, evolving character: songs follow the artist's life timeline, vocal habits and recurring motifs, and the artist photos are refreshed every six months to match the next era of songs. The API would let that consistency run every week without manual copy-paste, while keeping human judgment on what gets released.

**Expected volume**
> About 5 artists today, roughly 5–10 generations per week (2 takes each), growing to 10–15 artists over the next year.

**Plan / account**
> Suno Pro subscriber. All releases disclose AI use at distribution.

**Contact**
> etherarchi@gmail.com

### 日本語の意味

- **名前**：EtherArchi — Postdog Records（日本の独立レーベル。オリジナルの仮想アーティストを運営）
- **作っているもの**：人物像・音の決まり・見た目の世界を持つ少数のアーティストを抱え、各組が 1〜2 週に 1 曲を DistroKid で配信している。Suno はすべての曲の中心の楽器で、その前後（ブリーフ・歌詞・音量・ジャケット・配信の情報・配信の確認）はすでに自動化している
- **API の使い道**：配信の予定ごとに、組の人物像と参考メモからブリーフ（スタイル・入れないもの・歌詞）を作る。API で ①生成を依頼 ②2 つのテイク・情報・WAV を受け取り ③オーナーが聴いて 1 本選び ④音量を整えて配信の準備をする。最後の選曲と配信の登録は必ず人が行う。いまはオーナーが手で貼っていて、API はその部分だけを置き換える
- **新しさ**：各アーティストが一貫して育っていくレーベル。曲は人生の年表・歌い方の癖・繰り返しのモチーフに沿い、写真も半年ごとに次の時期の曲に合わせて新しくする。API があれば、人の判断を残したまま、その一貫性を毎週回せる
- **量**：いま 5 組、週 5〜10 回の生成（1 回 2 テイク）。1 年で 10〜15 組に増やす予定
- **プラン**：Suno Pro。配信ではすべて AI の使用を申告している
- **連絡先**：etherarchi@gmail.com

## 4. 通ったら

- 鍵は `music-label/.env` に入れる（`SUNO_API_KEY` など。会話には貼らない）
- Claude が公式の資料を読んで、`publish.py` の「Suno で作曲」の段を API につなぐ。いいね（選曲）と DistroKid の最後のボタンは、これまでどおりオーナー
- 資料が出るまで、API につなぐ仕組みは推測では作らない

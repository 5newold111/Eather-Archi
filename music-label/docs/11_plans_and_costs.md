# 11. 契約するプランと月の費用（2026-10-08 時点の調査）

価格は公開情報（公式ページの写しと第三者のまとめ）から集めた。契約の前に、各社の公式ページで最新の値を確かめること。
為替は 1 ドル 150 円で計算している。

## 結論（本体 5 組で始める場合）

| サービス | 契約するもの | 料金 | 理由 |
|---|---|---|---|
| Suno | **Premier** | 月 $30（年払いなら月 $24） | 2026-09-03 から月のダウンロードに上限ができた。Pro は 20 曲、Premier は 60 曲。週 5 曲で月 約 22 曲になり、Pro では足りない |
| DistroKid | **Ultimate（5 組）** | 年 $89.99 | Musician は 1 組、Musician Plus は 2 組まで。6 組目を足すときは 10 組の段（年 約 $158）に上げる |
| Supabase | **Pro** | 月 $25 | 無料版は容量が小さく、使わない期間があると止まる |
| Cloudflare R2 | 従量（無料枠あり） | 月 $0〜2 | 10 GB まで無料、取り出しは無料 |
| OpenAI | 前払いの残高 | 月 $6〜8 の見込み | ジャケット 1 曲 6 枚 × 5 曲 ＝ 週 30 枚 |
| Claude（Anthropic） | 前払いの残高 | 月 $10 前後の見込み | ブリーフ・話題曲の調査・画像の採点 |
| YouTube / Instagram / TikTok の API | 無料 | — | 審査が必要なだけ |

**月の合計の目安：約 $80（約 12,000 円）**。年払いにできるもの（Suno・DistroKid）を年払いにすると少し下がる。
子レーベルを始めると、レーベルごとに DistroKid と Suno の契約が増える（下の「子レーベル」）。

## Suno：ダウンロード上限で運用が変わる

- 2026-09-03 から、**Pro は月 20 曲、Premier は月 60 曲**までしかダウンロードできない。使わなかった分は翌月に繰り越さない
- 聴く・共有するのは上限なし。上限があるのはファイルとして取り出すときだけ
- 商用利用の権利は「**契約中にダウンロードした曲**」に付く（以前は「契約中に作った曲」だった）。配信する曲は必ず契約中にダウンロードする
- 同じ曲を別の形式で取り直したり、パート別の音（ステム）を取ったりしても、追加では数えないとする情報がある（公式で確認）
- 追加のダウンロードは買えるが、価格は公表されていない
- Suno Studio（Premier の機能）の中の作業は上限の対象外と Suno が案内している（範囲は公式で確認）

**運用の変更**：テイクは Suno の画面で聴いて選び、**選んだ 1 本だけ**をダウンロードする。
機械の計測（`select_takes.py`）は、ダウンロードした 1 本が配信の条件（長さ・無音・終わり方など）を満たすかの最終確認に使う。
本体レーベルは元々「全部人が聴く（Tier A）」なので影響は小さい。
機械に複数のテイクを比べさせる Tier B / C は、テイクの数だけダウンロードを使うため、上限の中では回らない。
子レーベルも当面は「聴いて 1 本だけダウンロード」で始め、Suno Studio で上限の対象外になるかを確かめてから Tier B / C に戻す。

| 使い方 | 月のダウンロード数 | 必要なプラン |
|---|---|---|
| 本体 5 組・選んだ 1 本だけ | 約 22 | Premier（60） |
| 本体 5 組＋毎月 1 組ずつ追加して 10 組 | 約 43 | Premier（60） |
| 本体 14 組前後 | 約 60 | Premier の上限。これ以上は追加購入か、組の一部を隔週に |
| 子レーベル 3 組・選んだ 1 本だけ | 約 13 | そのレーベル専用の Pro（20） |

子レーベルは配信アカウントを別にする設計なので、Suno も**レーベルごとに別の契約**にすると上限を分けられる。

## DistroKid

| プラン | 組数 | 年額（米国） |
|---|---|---|
| Musician | 1 | $24.99 |
| Musician Plus | 2 | $44.99 |
| Ultimate | 5 | $89.99 |
| Ultimate | 10 | 約 $157.99 |
| Ultimate | 20 | 約 $269.99 |

- 本体は Ultimate（5 組）で始め、月 1 組の追加で 6 組目が出る月の前に 10 組の段へ上げる
- 配信日の指定は Musician Plus 以上でできる（Ultimate に含まれる）
- **AI の申告**：2026 年 5 月から、アップロード画面に AI の使用を尋ねる質問がある（歌詞・ボーカル・演奏・作曲）。全曲で、使ったものをすべて選ぶ
- 「Leave a Legacy」（契約が切れても曲を残すオプション、1 曲 $29）は任意。長く残したい曲だけでよい

## OpenAI（画像）

- **gpt-image-1 は 2026-10-23 に提供終了**。後継（gpt-image-2.5 系など）を `setup_keys.py` と `generate_visuals.py` が自動で選ぶ
- gpt-image-1 の参考価格：1024×1024 で中品質 $0.042、高品質 $0.167。後継の価格は公式で確認
- 目安：デビュー時に 5 組 × 14 枚 ＝ 70 枚（約 $3）、毎週ジャケット 30 枚（約 $1.3）

## Claude（Anthropic）

- 使うモデル：Claude Opus 5.5（入力 100 万トークンあたり $4、出力 $20）
- 目安：ブリーフ 1 曲 約 $0.2、話題曲の調査 週 約 $0.5、画像の採点 週 約 $0.5、新人の下書き 月 約 $0.5
- 本体 5 組で月 約 $10。組が増えると、ほぼ組数に比例して増える

## 子レーベルを始めるとき（1 レーベル 3 組あたり）

| 追加の契約 | 料金 |
|---|---|
| DistroKid Ultimate（5 組） | 年 $89.99（月 約 $7.5） |
| Suno Pro | 月 $10（年払いなら $8） |
| SNS アカウント | 無料（レーベル単位で 1 組ずつ） |

1 レーベルあたり月 約 $20（約 3,000 円）が増える。Supabase・R2・Claude・OpenAI は共通のまま使える。

## 出典（2026-10-08 に確認）

- Suno の価格とダウンロード上限：[Variety](https://variety.com/2026/music/news/suno-unveils-download-caps-for-free-paid-tiers-generator-1236831589/)、[Suno Help：Upcoming Changes FAQ](https://help.suno.com/en/articles/13614785)、[Suno Help：How do downloads work?](https://help.suno.com/en/articles/13876865)、[RouteNote](https://routenote.com/blog/suno-announces-20-monthly-downloads-and-new-music-industry-models/)、[layer3labs](https://www.layer3labs.io/guides/suno-pricing)
- DistroKid のプラン：[RouteNote 2026](https://routenote.com/radar/distrokid-pricing-how-much-does-distrokid-actually-cost-in-2026/)、[RouteNote 値上げ](https://routenote.com/blog/distrokid-up-their-pricing/)、[DistroKid 公式](https://distrokid.com/product/distrokid/plans-and-pricing-2)
- DistroKid の AI 申告：[Digital Music News](https://www.digitalmusicnews.com/2026/05/14/distrokid-ai-credit-disclosure-what-it-looks-like/)、[Music In Africa](https://www.musicinafrica.net/magazine/distrokid-introduces-ai-disclosure-process-new-music-uploads)
- Supabase：[makerkit](https://makerkit.dev/blog/saas/supabase-pricing)、[jetadmin](https://www.jetadmin.io/blog/supabase-pricing-2026-guide-to-plans-limits-and-real-world-costs/)
- Cloudflare R2：[公式](https://developers.cloudflare.com/r2/pricing)
- OpenAI 画像：[公式 GPT Image 1](https://developers.openai.com/api/docs/models/gpt-image-1)、[公式 Deprecations](https://developers.openai.com/api/docs/deprecations)、[GitHub の終了告知の例](https://github.com/microsoft/semantic-kernel/issues/14526)

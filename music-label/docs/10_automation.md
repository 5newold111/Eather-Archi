# 10. 自動運転：人が手をつけない部分の全体像

Mac に定期実行を 1 回登録すると（`python3 scripts/schedule.py install`）、下の表の仕事が決まった時刻に動く。
人がやるのは「Suno で作る・聴いて選ぶ・DistroKid に登録する・最初の 5 回だけ画像を選ぶ」だけになる。

## 1 週間の時間割（時刻は日本時間。Mac がスリープ中に過ぎた仕事は、起きたときに 1 回だけ動く）

| いつ | 仕事 | 中身 | 使うスクリプト |
|---|---|---|---|
| 月 23:30 | trends | Claude が Web で今週のチャートを調べ、トレンド言語を決め、場面ごとの話題曲を参考曲フォルダに置く | `fetch_trends.py` |
| 火 06:00 | brief | 全組の参考曲の割り当て → コラボの組み合わせ → Claude がブリーフを書く | `run_week.py brief` |
| 火〜木 8〜23 時の毎時 | auto | Suno の節が置かれたら歌詞を合体 → テイクが置かれたら計測 → 選ばれたら音量・ジャケット・登録シート・DB・SNS の準備 | `run_week.py auto` |
| 15 分ごと | post | 配信時刻を過ぎた SNS 投稿を出す（YouTube は前もって公開予約） | `post_social.py post --week all` |
| 木 10:00 | growth | 成績を集める → 成長分析（重み・ヒント・転換の提案）→ 転換を設定書に反映 | `collect_metrics.py --analyze` → `apply_pivot.py --from-growth` |
| 毎日 03:30 | backup | 音源・ジャケット・記録を Cloudflare R2 に予備保管（変わったものだけ） | `backup_r2.py` |
| 毎月 1 日 | monthly | 週の曲数の確認（上限超えで隔週へ）・月 1 組の追加（下書きとデビュー予約）・Instagram の鍵の延長・四半期の EP 計画 | `expand_label.py check --apply` ほか |

ログは `out/logs/<仕事>.log`。止めるときは `python3 scripts/schedule.py uninstall`。

## 人がやること（毎週）

| いつ | やること | 機械が用意しておくもの |
|---|---|---|
| 火曜 | Suno の Write Lyrics に指示文を貼り、出てきた節を `out/briefs/<週>_<組>.verses.txt` に保存 | 指示文（ブリーフの `suno_lyric_prompt`） |
| 火曜 | Suno で 4〜6 テイク作り、`out/takes/<週>_<組>/take_01.mp3 …` に保存 | 最終歌詞・Style 指示文 |
| 火〜水 | 機械が絞ったテイクを聴いて選ぶ（`select_takes.py … --choose 番号`）。Tier C の組は抜き取りの週だけ | 聴く順番・落ちた理由 |
| 水曜 | DistroKid に登録（`out/distrokid/<週>/sheet.md` を見てコピペ）。ISRC と UPC を `<組>.metadata.json` に書く | 登録シート・確認リスト |
| 月 1 回 | DistroKid の明細と Spotify for Artists の書き出しを `out/metrics/inbox/` に置く | 取り込み・分析は自動 |
| 最初の 5 回 | ロゴ・写真・ジャケットを選ぶ（理由を一言） | Claude が採点した候補。5 回で好みを学び、以後は自動 |
| 転換のたび | 撮り直した写真を選ぶ | 候補と転換の説明 |

## 自動で決めていること（ルールの置き場所）

| 判断 | ルール | 変えたいとき |
|---|---|---|
| その週に作る組 | デビュー週の 2 週間前から。隔週の組は偶数週だけ。休止は作らない | 設定書の `debut_week` / `cadence` |
| コラボ | 新人は既存の組と feat.／伸びている組は客演／4 週に 1 回は相性のよい組を順番に／リミックス型は 5 週ごと | `plan_collabs.py`（`--pair` で手で指定、`--none` で無し） |
| 話題曲 | 上位 50 曲で英語以外が 10% 以上ならトレンド言語。話題曲は 2 週間だけ優先。音源なしなので「世界観」「歌詞」の枠だけ | `fetch_trends.py` の `THRESHOLD` |
| トレンド曲の枠 | 通常 2、横ばい（flat）か転換レベル 1 の組は 3 | `select_references.py` |
| テイク選び | 長さ・無音・サビの位置・終わり方・歌詞の一致・癖 | `docs/07_sublabels.md` |
| 画像選び | Claude が 5 観点で採点。顔・他社ロゴ・余計な文字は除外。1 位と 2 位が僅差なら聞く | `templates/visual_criteria.json` |
| 方針転換 | 下降 2 回でレベル 1（BPM±5）、さらに悪ければレベル 2（場面と見た目を隣へ・写真撮り直し）。2 回目のレベル 2 で隔週、3 回目で休止。転換から 8 週は判定しない | `analyze_growth.py` / `apply_pivot.py` |
| 拡大 | 本体は毎月 1 組（1 日のある週ごとに 1 組）。子レーベルは 3 か月後から、1,000 再生到達率 50% 超えで月 1 組。週の曲数が 8 を超えたら古い組から隔週 | `expand_label.py` |
| デビュー週 | **毎月 1 日のある週**（月曜始まり）。1 日が日曜の月は前の月曜からの週になり、本体の配信は前月末の水曜になる | `debut.py --month YYYY-MM` |
| 新人のデビュー | 毎月 1 日に下書きし、4 週間以上あとの「1 日のある週」に予約。名前確認・ロゴと写真の候補は自動。取り消しは `expand_label.py cancel --artist <組>` | `expand_label.py` |

## 鍵が無いときの動き

どの仕事も、鍵が無い部分は飛ばすか「Claude に渡すはずだった内容」「SQL」などのファイルを書き出して止まる。
エラーで全体が止まることはない。鍵の入れ方は `docs/09_auth_batch.md`。

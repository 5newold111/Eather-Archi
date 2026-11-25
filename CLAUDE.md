# このリポジトリで守ること（EtherArchi / Postdog Records）

オーナー（Gon さん）は、空間デザインのブランド EtherArchi と、音楽レーベル Postdog Records を主宰している。
音楽レーベルの仕組みは `music-label/` にある（入口は `music-label/HANDOFF.md`）。

## 話し方

- **日本語で答える。** オーナーはプログラムの専門家ではないので、専門用語は言い換えるか、短い説明を添える（例：「Python の部品（ライブラリ）」）。
- 何をしているか・何をするかを、処理ごとに日本語で示す（ターミナルの命令の説明も日本語）。
- オーナーに打ってもらう命令は、`cd ~/Eather-Archi/music-label && …` から始まる、そのまま貼れる 1 行にする（新しいターミナルはホームフォルダから始まるため）。
- 1 度に頼むのは 1〜3 手まで。「送ってほしい表示」（どの行か）も添える。
- 「空間は舞台」のような大げさな言い回しは使わない。洗練されていて、親しみやすい調子で。

## 鍵と個人の情報（いちばん大事）

- **鍵・トークン・パスワード・復旧キットを、チャットに貼ってもらわない。** 入れるのは、オーナーの Mac のターミナルの入力欄（`python3 scripts/setup_keys.py`）か、ダッシュボードの設定ページだけ。
- 鍵は `music-label/.env` にだけ置く（GitHub には入れない）。暗号にした写しは R2 の金庫（`vault.py`）。
- **オーナーの本名・Mac の利用者名・メールアドレスを、コード・説明書・コミットの文・PR に書かない。** 本名は Mac の `.env` の OWNER_LEGAL_NAME にだけある。会話の中で本名を見ても、くり返さない。
- 送る前に `python3 music-label/scripts/name_guard.py` で確かめる（手順は `.claude/skills/finish-change/SKILL.md`）。

## GitHub

- 作業のブランチは、会話で指示されたものだけ。PR は下書きのまま。**PR は合流（マージ）しない。**
- 変更の締め方（試験・見張り・記録・送り方・GitHub 側の試験）は、`finish-change` スキルに従う。
- 大きな変更のあとや Python の版が変わったときは、`system-sweep` スキルで全部を試しに流す。

## 音楽レーベルの仕組みの決まり（抜粋。全部は `music-label/docs/12_decisions.md`）

- 仕組みが押さないもの：Suno の Create・DistroKid の最後の登録（Continue）・ロボット確認・Extras・Discovery Pack・宣誓のチェック・取り下げ。Spotify for Artists のピッチの送信も自動にしない。
- 登録済み・配信中の曲を、DistroKid にもう一度入力しない（二重の登録になる）。
- 曲づくりは「1 曲 1 枠」：参考曲から借りるのは枠ごとに 1 曲から。メロディーと歌詞を写さない（`music-label/docs/04_borrowing_rules.md`）。分析はどこまで細かくしてよいが、1 曲をまるごと再現する指示は作らない。
- オーナーの Mac は Python 3.15。部品は `music-label/.venv`、音の解析（Demucs・Basic Pitch）は `~/.postdog-audio`（Python 3.11）。Mac 用の台本は、Mac の古い bash で動く書き方にする（変数は `${VAR}`、人の操作が要る所は案内して止まる）。
- Spotify の API は使わない（Premium なし）。配信の確認は Apple Music・Deezer、ISRC・UPC は DistroKid の明細から。

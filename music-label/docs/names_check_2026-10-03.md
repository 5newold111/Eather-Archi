# 名前の重複確認（2026-10-03）

この実行環境はネットワーク制限で配信サービスの API に届かなかったため（`check_names.py` は unverified を返した）、Web 検索で 1 件ずつ照合した。
商標と SNS ハンドルは未確認（手動）。API 鍵のある環境で `python3 scripts/check_names.py --all --apply` を再実行すること。

| 区分 | 旧名 | 新名 | 結果 |
|---|---|---|---|
| drive | AURELINE | **LUMENLINE** | **改名**（旧名が衝突） |
| drive | LINEN & SALT | **LINEN & SALT** | clear |
| drive | NULLROOM | **NULLFLOOR** | **改名**（旧名が衝突） |
| drive | GIRDER CLUB | **GIRDER CLUB** | near：Girder Music というレーベルが近い。混同は低い |
| drive | MERIDIAN TAPE | **MERIDIAN TAPE** | near：Meridian Records（クラシックのレーベル）が近い。混同は低い |
| label | DEEP WORK DEPT. | **MONOTASK** | **改名**（旧名が衝突） |
| focus | GRIDPAPER | **GRIDPAPER** | clear |
| focus | LONGFORM | **LONGFORM** | near：一般語。同名バンドは見つからず |
| focus | IRIS PATCH | **IRIS PATCH** | clear |
| label | FIRST POUR | **FIRST POUR** | clear |
| morning | JUNE CARAWAY | **JUNE CARAWAY** | clear |
| morning | PALMA & REED | **PALMA & REED** | clear |
| morning | TOASTER TAPES | **TOASTER TAPES** | clear |
| label | STRIDE | **PACENOTE** | **改名**（旧名が衝突） |
| move | KILO VERA | **KILO VERA** | clear |
| move | CROSSWALK | **KOSATEN** | **改名**（旧名が衝突） |
| move | LAST HILL | **LAST HILL** | near：『The Last Hill』という曲名のみ。同名バンドなし |
| label | LOW TIDE | **SLACK WATER** | **改名**（旧名が衝突） |
| sleep | VESSEL LOW | **VESSEL LOW** | clear |
| sleep | FELT HOUR | **FELT HOUR** | clear |
| sleep | RAINLETTER | **RAINLETTER** | clear |

## 衝突の内容

- AURELINE：Beatport に同名アーティスト／AURELIGHT 案も Aureliaslight に近く却下 → LUMENLINE（完全一致なし）
- NULLROOM：同名の音楽プロジェクト（公式サイト・Suno 上の活動あり）→ NULLFLOOR（完全一致なし）
- CROSSWALK：Spotify / Apple に Crosswalk Band、Crosswalk Records（パリのレーベル）→ KOSATEN（完全一致なし）
- LOW TIDE：Low Tide Recordings（UK グライム）、Low Tide / Lowtide のバンド複数 → SLACK WATER（同名の曲が 1 つあるだけ）
- STRIDE：Stride Records（複数）→ FOOTFALL 案も Footfall デュオ・Footfalls Records と衝突 → PACENOTE（完全一致なし）
- DEEP WORK DEPT.：Deep Work Records / Deep Work Music が存在 → GRIDLINE 案も同名バンドと衝突 → MONOTASK（完全一致なし）

## 次に必要な手動確認（デビュー前）

1. 商標：J-PlatPat（称呼検索）、USPTO、WIPO Global Brand Database で各名を検索
2. SNS：Instagram / TikTok / YouTube のハンドルが取れるか（取れなければ `.official` や `.music` を付ける）
3. Spotify：API 鍵を `.env` に入れて `check_names.py --all --apply` を再実行（手元の PC で）

# 保管庫：2026-10-08 に白紙にした組

アーティスト台帳（`artists.xlsx`、`docs/13_artist_book.md`）で一から作り直すため、それまでの 17 組をここへ移した。
制作の流れ（`templates/artists/`・`templates/labels/*.json` の `artists`）からは外れているので、どのスクリプトも読まない。

| 場所 | 中身 |
|---|---|
| `artists/*.json` | 本体 5 組（LUMENLINE・MERIDIAN TAPE・GIRDER CLUB・LINEN & SALT・NULLFLOOR） |
| `labels/<レーベル>.artists.json` | 子レーベル 4 つ × 3 組（レーベルの設定そのものは `templates/labels/` に残っている） |

戻したいとき：`artists/*.json` を `templates/artists/` に戻す。子レーベルの組は、配列をレーベルの設定書の `artists` に貼り戻す。
名前の確認結果（`name_check`）も中に残っているので、同じ名前を使い直すときの参考になる。

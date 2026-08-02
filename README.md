# エーテルアーキ | 公式サイト

デザインとアプリ開発の事業「エーテルアーキ（Ether Archi）」のコーポレートサイトです。
フレームワークやビルドツールに依存しない、静的な HTML / CSS / JavaScript で構成されています。

## デザインの方向性

社名の「アーキ（＝設計）」に由来する **製図シート** を全体のモチーフにしています。

- **完全な無彩色**。差し色は使わず、階層は文字組み・余白・罫線の太さだけで表現します
- **ライト単一テーマ**。自動反転はさせず、コンクリートのようなオフホワイト（`#EDEDEA`）に統一します
- **角丸・影・グラデーション・発光を使いません**。面は 1px の罫で仕切ります
- **装飾記号やアイコンを使いません**。項目は図面の項目番号（モノスペース）で示します
- 背景の方眼は Canvas で描画しています（`js/main.js` の `drawSheet`）

### タイポグラフィ

| 役割 | 書体 |
|---|---|
| 和文 | Zen Kaku Gothic New |
| 欧文見出し | Archivo |
| 番号・ラベル・注記 | IBM Plex Mono |

**フォントはセルフホストしています**（`fonts/` 以下、`css/fonts.css` で定義）。
外部CDNを使わないため、訪問者のIPアドレスが第三者へ送信されません。
日本語は `unicode-range` で分割されており、ブラウザはページに必要な範囲だけを取得します
（実測: 500ファイル中56ファイル・約540KB）。

読み込みに失敗した場合も崩れないよう、`css/style.css` の
`--ja` / `--en` / `--mo` にフォールバックを併記しています。

`css/fonts.css` は生成物です。書体や字幅を変更する場合は、
Google Fonts の配信CSSを取得し直して `fonts/` ごと作り直してください。

## 構成

```
.
├── index.html                        # トップページ（ワンページ構成）
├── SECURITY.md                       # セキュリティ方針・脆弱性の報告先
├── css/
│   ├── style.css                     # スタイルシート
│   └── fonts.css                     # @font-face 定義（生成物）
├── fonts/                            # セルフホストしたwoff2（500ファイル）
├── js/
│   └── main.js                       # 方眼描画・ヘッダー・メニュー・出現演出
└── .github/workflows/
    └── static.yml                    # GitHub Pages への自動デプロイ
```

## セクション

| No. | セクション |
|---|---|
| — | ヒーロー |
| 01 | 私たちについて |
| 02 | サービス |
| 03 | 強み |
| 04 | 制作の流れ |
| 05 | お問い合わせ |

## ローカルでの確認

`index.html` をブラウザで開くだけで表示できます。ローカルサーバーを使う場合:

```bash
python3 -m http.server 8000
# → http://localhost:8000
```

## 公開

GitHub Pages に自動デプロイされます。**初回のみ**、リポジトリの
Settings → Pages で `Source` を **GitHub Actions** に切り替えてください
（この操作だけは管理者権限が必要なため自動化できません）。

以降は既定ブランチへプッシュするたびに自動で反映されます。

- 設定: https://github.com/5newold111/Eather-Arch/settings/pages
- 公開先: https://5newold111.github.io/Eather-Arch/

## カスタマイズのポイント

- **お問い合わせフォーム**: `index.html` の `.contact__form` 内の `iframe` に Google フォームの公開用 URL を指定しています。差し替えるときは以下に注意してください
  - フォーム編集画面の「公開」（旧 UI では「送信」）から取得したリンクを使う。編集用 URL（末尾が `/edit`）は訪問者が開けないため使えません
  - 同じ URL を、直下のフォールバック用リンクと `<meta http-equiv="Content-Security-Policy">` の `frame-src` にも反映する。`frame-src` に無いドメインは表示されません
  - 表示の高さは `css/style.css` の `.contact__form iframe` の `height` で調整します
- **配色**: `css/style.css` 冒頭の `:root` で定義しています
- **方眼の密度**: `js/main.js` の `step`（既定 32px）と `major`（既定 5 本ごと）で調整できます

# エーテルアーキ | 公式サイト

デザインとアプリ開発の事業「エーテルアーキ（Ether Archi）」のコーポレートサイトです。
フレームワークやビルドツールに依存しない、静的な HTML / CSS / JavaScript で構成されています。

## 構成

```
.
├── index.html      # トップページ（ワンページ構成）
├── css/
│   └── style.css   # スタイルシート
└── js/
    └── main.js     # ヘッダー・メニュー・スクロール演出
```

## セクション

- ヒーロー（キャッチコピー / CTA）
- 私たちについて（ABOUT）
- サービス（UI/UXデザイン、Webデザイン/ブランディング、アプリ開発、保守・グロース支援）
- 強み（STRENGTHS）
- 制作の流れ（FLOW）
- お問い合わせ（CONTACT）

## ローカルでの確認

`index.html` をブラウザで開くだけで表示できます。ローカルサーバーを使う場合:

```bash
python3 -m http.server 8000
# → http://localhost:8000
```

## 公開

静的サイトのため、GitHub Pages / Netlify / Vercel などにそのまま公開できます。
GitHub Pages の場合は、リポジトリの Settings → Pages からブランチを選択してください。

## カスタマイズのポイント

- **お問い合わせ先**: `index.html` 内の `contact@example.com` を実際のメールアドレスに差し替えてください。
- **配色**: `css/style.css` 冒頭の `:root` のカスタムプロパティ（`--accent-1` など）で変更できます。
- **OGP**: `index.html` の `<meta property="og:*">` を必要に応じて調整してください。

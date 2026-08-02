# セキュリティポリシー

エーテルアーキ公式サイト（<https://5newold111.github.io/Eather-Arch/>）のセキュリティに関する方針です。

## 脆弱性の報告

このサイトに関する脆弱性を見つけた場合は、公開の Issue ではなく
**GitHub の非公開報告機能でご連絡ください**。

- 報告フォーム: <https://github.com/5newold111/Eather-Arch/security/advisories/new>

リポジトリの **Security** タブ → **Report a vulnerability** からも同じ画面を開けます。
やり取りは修正が公開されるまで非公開に保たれます。

報告の際は、可能な範囲で以下をお知らせいただけると助かります。

- 事象の概要と影響範囲
- 再現手順
- 確認に使ったブラウザ・OS

原則として3営業日以内に受領のご連絡をします。
公開の Issue に脆弱性の詳細を書くと、修正前に悪用される恐れがあるためお控えください。

## このサイトの構成

静的サイトです。サーバーサイドの処理・データベース・ユーザー認証は存在せず、
このサイト自身が訪問者の入力を受け取ることはありません。そのため一般的な
Web アプリケーションの攻撃面（SQLインジェクション、CSRF、セッション奪取など）は
該当しません。

- ホスティング: GitHub Pages
- 外部リソースの読み込み: **お問い合わせフォームのみ**
  （Google フォームを `iframe` で埋め込み。Webフォントは同一オリジンから配信）
- トラッキング・広告・アクセス解析: **なし**
- Cookie: サイト自身は**使用していません**。ただし埋め込んだ Google フォームは
  Google の Cookie を使用します
- 依存パッケージ: **なし**（ビルドツールを使用していません）

### お問い合わせフォームの取り扱い

入力内容の送信先と保管先は Google であり、このサイトは内容を受け取りません。
お問い合わせページを表示した時点で、訪問者のIPアドレスとユーザーエージェントが
Google に送信されます。適用されるのは
[Google のプライバシーポリシー](https://policies.google.com/privacy)です。

## 実施している対策

| 対策 | 内容 |
|---|---|
| Content-Security-Policy | `index.html` の `<meta>` で宣言。外部の読み込みは `frame-src` の Google フォームのみに限定 |
| Referrer-Policy | `strict-origin-when-cross-origin` |
| Webフォントのセルフホスト | フォント配信を通じた第三者へのIPアドレス送信がない |
| GitHub Actions の SHA 固定 | タグの差し替えによるサプライチェーン攻撃を防ぐ |
| ワークフローの最小権限 | `contents: read` / `pages: write` / `id-token: write` のみ |
| 非公開の脆弱性報告 | GitHub の Private vulnerability reporting を有効化 |
| Secret scanning / Push protection | 認証情報の誤コミットを検知・ブロック |
| ブランチ保護 | デフォルトブランチへの直接pushを制限 |

## 既知の制約

**GitHub Pages はカスタムHTTPヘッダーに対応していません。**
そのため以下は現状設定できません。

- `frame-ancestors`（クリックジャッキング対策）
  — CSP の仕様上 `<meta>` では無効で、HTTPヘッダーでしか指定できません
- `X-Content-Type-Options`、`Permissions-Policy` などのヘッダー

このサイトにログインや会員向けの操作は存在せず、クリックジャッキングで
奪える権限がないため、現時点の実害は限定的と判断しています。
お問い合わせフォームは Google 側のオリジンで動作しており、
このサイトの `frame-ancestors` の有無に影響されません。
これらのヘッダーが要件になる場合は、Cloudflare Pages や Netlify など
ヘッダーを設定できるホスティングへの移行が必要です。

## 依存アクションの更新について

`.github/workflows/static.yml` のアクションはコミットSHAで固定しています。
更新する場合は、必ず SHA を確認してから書き換えてください。

```bash
git ls-remote https://github.com/actions/checkout refs/tags/v4
```

行末のコメント（`# v4.4.0` など）は、その SHA が対応するバージョンの記録です。

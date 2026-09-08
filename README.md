# うにコロ（Unikoro）

シーズーの「うに」と一緒に、続けたいことを毎日ちょっとずつ続ける iOS アプリ。
叱らない。休んだ日はうにも寝ている。続けた分だけ、保護犬・保護猫の支援につながる。

- 企画書: [Eather-Arch/docs/uni-app](https://github.com/5newold111/Eather-Arch/tree/main/docs/uni-app)
- 対応: iPhone / iOS 17 以上
- 技術: SwiftUI ＋ SwiftData（端末内のデータベース）＋ UserNotifications（端末内の通知）
- バンドル ID: `com.gonation.unikoro`

## いまの状態（v0.1.0 骨組み）

| できること | 状態 |
|---|---|
| 習慣の追加・編集・削除（最大5つ、曜日指定、リマインド時刻） | できる |
| ホームで1タップで達成／取り消し | できる |
| うにの5表情とセリフ（仮画像） | できる |
| 月カレンダーと累計（回数・うにと歩いた距離） | できる |
| リマインド通知 | できる |
| うにのイラスト | **未**（写真待ち。いまは SF Symbols の仮画像） |
| バックアップ書き出し | 未 |
| サポーターパス（課金） | 未（v1.1） |

## Mac でのはじめかた

このリポジトリには `.xcodeproj`（Xcode のプロジェクトファイル）を入れていません。
`project.yml` から **XcodeGen** という道具で生成します。設定の差分を Git で読みやすくするためです。

```bash
# 1. XcodeGen を入れる（初回のみ。Homebrew が必要）
brew install xcodegen

# 2. プロジェクトを生成する
cd unikoro
xcodegen generate

# 3. Xcode で開く
open Unikoro.xcodeproj
```

Xcode で:

1. 左上のスキームが `Unikoro`、実行先が iPhone のシミュレータになっていることを確認
2. ⌘R で実行。ホーム画面の右上「＋」から習慣を追加し、丸をタップしてうにの表情が変わるか確認
3. ⌘U でテスト実行（うにの表情判定のテストが通る）

実機で動かすときは、Xcode の Signing & Capabilities で自分の Apple ID（Team）を選びます。

`project.yml` を変えたら、もう一度 `xcodegen generate` を実行してください。

## フォルダ構成

```
Unikoro/
├── App/            アプリの入口とタブ
├── Models/         Habit（習慣）、HabitLog（達成記録）、UniMood（うにの表情と判定）
├── Views/          画面。Uni/ はうにの表示
├── Services/       通知
├── Theme/          色と余白のきまり
└── Resources/      画像・アイコン（Assets.xcassets）
UnikoroTests/       ユニットテスト
docs/               設計メモ
```

## 設計のきまり（企画書のバリューより）

- **叱らない**: 未達成の日に赤や × を出さない。通知は1日1回、急かさない文面
- **小さく、確かに**: 1日の操作は1タップ。機能を足すときは「1タップが増えないか」を問う
- **お金を隠さない**: アプリ内に「寄付する」ボタンは置かない（ストア規約）。収益の使い道は説明画面で明示
- **静かな上質さ**: オフホワイトの背景、派手な演出なし

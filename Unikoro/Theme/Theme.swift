import SwiftUI

/// 色と余白のきまり。
/// 企画書のバリュー「静かな上質さ」「叱らない」に沿って、赤や×は使いません。
enum Theme {
    /// 背景。コンクリートのようなオフホワイト
    static let background = Color(red: 0.957, green: 0.953, blue: 0.937)
    /// 文字色。真っ黒ではなく、少し暖かい墨色
    static let ink = Color(red: 0.227, green: 0.212, blue: 0.196)
    /// 補助の文字色
    static let inkSoft = Color(red: 0.55, green: 0.53, blue: 0.50)
    /// アクセント。うにの毛色を想定した薄い茶
    static let accent = Color(red: 0.79, green: 0.64, blue: 0.49)
    /// 達成の色。やわらかい緑
    static let done = Color(red: 0.66, green: 0.76, blue: 0.63)
    /// カードの面
    static let surface = Color.white.opacity(0.7)

    static let cornerRadius: CGFloat = 16
    static let spacing: CGFloat = 16
}

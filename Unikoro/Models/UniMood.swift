import Foundation

/// うにの5つの表情。企画書 第4章「最初に必要な表情」に対応。
enum UniMood: String, CaseIterable {
    case normal    // ふつう
    case happy     // うれしい
    case delighted // ごきげん
    case sleepy    // ねむい
    case resting   // おやすみ

    /// 画面に出すセリフ（短く、ひらがな多め、命令しない）
    var line: String {
        switch self {
        case .normal:    return "きょうは なにする？"
        case .happy:     return "やったね"
        case .delighted: return "ぜんぶ おわった。さんぽ いこ"
        case .sleepy:    return "ねるまえに 1こだけ、どう？"
        case .resting:   return "おはよ。きょうも いっしょに"
        }
    }

    /// 本番のイラストが入るまでの仮画像（SF Symbols の名前）
    var placeholderSymbol: String {
        switch self {
        case .normal:    return "face.smiling"
        case .happy:     return "face.smiling.inverse"
        case .delighted: return "sparkles"
        case .sleepy:    return "moon.zzz"
        case .resting:   return "zzz"
        }
    }

    /// 本番で使う画像アセット名（Assets.xcassets に同名で入れる）
    var imageName: String { "uni-\(rawValue)" }
}

/// 「今日の達成状況と時刻」から表情を決める。
/// 画面や DB に依存しない純粋な計算なので、テストしやすいように分けています。
enum UniMoodResolver {
    /// - Parameters:
    ///   - scheduledToday: 今日やる予定の習慣の数
    ///   - completedToday: 今日達成した数
    ///   - scheduledYesterday: 昨日やる予定だった数
    ///   - completedYesterday: 昨日達成した数
    ///   - now: 現在時刻（テストで差し替えられるように引数にしている）
    static func resolve(
        scheduledToday: Int,
        completedToday: Int,
        scheduledYesterday: Int,
        completedYesterday: Int,
        now: Date = .now,
        calendar: Calendar = .current
    ) -> UniMood {
        // 1. 今日の分がすべて終わっていれば「ごきげん」
        if scheduledToday > 0 && completedToday >= scheduledToday {
            return .delighted
        }
        // 2. 1つでも達成していれば「うれしい」
        if completedToday > 0 {
            return .happy
        }
        // 3. 20時以降でまだ残っていれば「ねむい」（責めずに、1つだけ誘う）
        let hour = calendar.component(.hour, from: now)
        if hour >= 20 && scheduledToday > 0 {
            return .sleepy
        }
        // 4. 昨日ゼロ達成で、今日はまだ何もしていなければ「おやすみ」から目覚める
        if scheduledYesterday > 0 && completedYesterday == 0 {
            return .resting
        }
        return .normal
    }
}

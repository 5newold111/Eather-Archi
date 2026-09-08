import Foundation
import SwiftData

/// 「この習慣を、この日に達成した」という1件の記録。
/// 未達成の日には記録を作りません（×を出さない設計のため、存在しないこと自体が未達成）。
@Model
final class HabitLog {
    /// 達成した日（その日の 0 時に丸めた日付）
    var day: Date
    /// 実際にタップした時刻
    var completedAt: Date
    var habit: Habit?

    init(day: Date, habit: Habit?, completedAt: Date = .now) {
        self.day = Calendar.current.startOfDay(for: day)
        self.completedAt = completedAt
        self.habit = habit
    }
}

import Foundation
import SwiftData

/// 習慣（ユーザーが続けたいこと）。
/// `@Model` を付けると SwiftData が自動で保存・読み込みしてくれます。
@Model
final class Habit {
    /// 通知の識別などに使う固定の ID（`id` は SwiftData が予約しているので uuid という名前にしている）
    var uuid: UUID
    var name: String
    /// 実施する曜日。Calendar の weekday と同じで 1=日, 2=月 … 7=土
    var weekdays: [Int]
    /// リマインド時刻（設定しない場合は nil）
    var reminderHour: Int?
    var reminderMinute: Int?
    var sortOrder: Int
    var createdAt: Date

    /// この習慣の達成記録。習慣を消すと記録も一緒に消える（cascade）
    @Relationship(deleteRule: .cascade, inverse: \HabitLog.habit)
    var logs: [HabitLog] = []

    init(
        name: String,
        weekdays: [Int] = Array(1...7),
        reminderHour: Int? = nil,
        reminderMinute: Int? = nil,
        sortOrder: Int = 0
    ) {
        self.uuid = UUID()
        self.name = name
        self.weekdays = weekdays
        self.reminderHour = reminderHour
        self.reminderMinute = reminderMinute
        self.sortOrder = sortOrder
        self.createdAt = .now
    }

    /// その日にやる予定の習慣か
    func isScheduled(on date: Date, calendar: Calendar = .current) -> Bool {
        weekdays.contains(calendar.component(.weekday, from: date))
    }

    /// その日に達成済みか
    func isCompleted(on date: Date, calendar: Calendar = .current) -> Bool {
        let day = calendar.startOfDay(for: date)
        return logs.contains { $0.day == day }
    }

    /// 達成記録があれば探す
    func log(on date: Date, calendar: Calendar = .current) -> HabitLog? {
        let day = calendar.startOfDay(for: date)
        return logs.first { $0.day == day }
    }

    /// 登録できる習慣の上限（企画書 第5章）
    static let maxCount = 5
}

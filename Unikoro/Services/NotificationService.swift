import Foundation
import UserNotifications

/// ローカル通知（端末内で完結するリマインド）の登録と解除。
/// 習慣ごと・曜日ごとに1件ずつ予約します。文面はうにのセリフだけ。
final class NotificationService {
    static let shared = NotificationService()
    private let center = UNUserNotificationCenter.current()

    private init() {}

    /// 通知の許可を求める。許可されたら true
    func requestAuthorization() async -> Bool {
        (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
    }

    /// その習慣の通知を組み直す（設定が変わったときに呼ぶ）
    func reschedule(for habit: Habit) async {
        cancel(for: habit)
        guard UserDefaults.standard.bool(forKey: "notificationsEnabled"),
              let hour = habit.reminderHour, let minute = habit.reminderMinute else { return }

        for weekday in habit.weekdays {
            var comps = DateComponents()
            comps.weekday = weekday
            comps.hour = hour
            comps.minute = minute

            let content = UNMutableNotificationContent()
            content.title = "うにコロ"
            content.body = "「\(habit.name)」、1こだけ どう？"
            content.sound = .default

            let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: true)
            let request = UNNotificationRequest(
                identifier: Self.identifier(habit: habit, weekday: weekday),
                content: content,
                trigger: trigger
            )
            try? await center.add(request)
        }
    }

    func cancel(for habit: Habit) {
        let ids = (1...7).map { Self.identifier(habit: habit, weekday: $0) }
        center.removePendingNotificationRequests(withIdentifiers: ids)
    }

    func cancelAll() {
        center.removeAllPendingNotificationRequests()
    }

    private static func identifier(habit: Habit, weekday: Int) -> String {
        "habit-\(habit.uuid.uuidString)-\(weekday)"
    }
}

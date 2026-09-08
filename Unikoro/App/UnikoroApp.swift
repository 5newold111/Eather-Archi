import SwiftUI
import SwiftData

/// アプリの入口。
/// SwiftData（スマホ内の小さなデータベース）の入れ物をここで作り、全画面から使えるようにします。
@main
struct UnikoroApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
        .modelContainer(for: [Habit.self, HabitLog.self])
    }
}

/// 下部タブ。ホーム／きろく／せってい の3つ。
struct RootView: View {
    var body: some View {
        TabView {
            HomeView()
                .tabItem { Label("ホーム", systemImage: "pawprint") }
            CalendarView()
                .tabItem { Label("きろく", systemImage: "calendar") }
            SettingsView()
                .tabItem { Label("せってい", systemImage: "slider.horizontal.3") }
        }
        .tint(Theme.accent)
    }
}

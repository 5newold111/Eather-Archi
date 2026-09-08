import SwiftUI
import SwiftData

/// せってい画面。通知のオン／オフと、説明ページへの入口。
struct SettingsView: View {
    @Query private var habits: [Habit]
    @AppStorage("notificationsEnabled") private var notificationsEnabled = false
    @State private var deniedBySystem = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle("リマインド通知", isOn: $notificationsEnabled)
                        .onChange(of: notificationsEnabled) { _, on in
                            Task { await updateNotifications(on) }
                        }
                } footer: {
                    if deniedBySystem {
                        Text("iPhone の「設定」アプリで、うにコロの通知が許可されていません。")
                    } else {
                        Text("各習慣に設定した時刻に、うにのひとことが届きます。")
                    }
                }
                Section {
                    NavigationLink("うにと、お金のこと") { AboutView() }
                }
                Section {
                    LabeledContent("バージョン", value: Bundle.main.shortVersion)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("せってい")
        }
    }

    private func updateNotifications(_ on: Bool) async {
        if on {
            let granted = await NotificationService.shared.requestAuthorization()
            deniedBySystem = !granted
            if granted {
                for habit in habits { await NotificationService.shared.reschedule(for: habit) }
            } else {
                notificationsEnabled = false
            }
        } else {
            NotificationService.shared.cancelAll()
        }
    }
}

extension Bundle {
    var shortVersion: String {
        infoDictionary?["CFBundleShortVersionString"] as? String ?? "-"
    }
}

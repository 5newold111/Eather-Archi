import SwiftUI
import SwiftData

/// 習慣の追加・編集。名前・曜日・リマインド時刻だけ。
struct HabitEditView: View {
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss

    let habit: Habit?
    let nextSortOrder: Int

    @State private var name = ""
    @State private var weekdays: Set<Int> = Set(1...7)
    @State private var remind = false
    @State private var reminderTime = Calendar.current.date(from: DateComponents(hour: 21, minute: 0)) ?? .now

    /// 「日 月 火 …」の短い曜日名。index 0 が weekday 1（日曜）
    private let symbols = Calendar.current.shortWeekdaySymbols

    var body: some View {
        NavigationStack {
            Form {
                Section("なにを つづける？") {
                    TextField("例：ストレッチ", text: $name)
                }
                Section("どの曜日に？") {
                    HStack {
                        ForEach(1...7, id: \.self) { day in
                            let on = weekdays.contains(day)
                            Button(symbols[day - 1]) {
                                if on { weekdays.remove(day) } else { weekdays.insert(day) }
                            }
                            .buttonStyle(.plain)
                            .frame(maxWidth: .infinity, minHeight: 36)
                            .background(on ? Theme.accent.opacity(0.35) : Color.clear, in: Circle())
                            .foregroundStyle(on ? Theme.ink : Theme.inkSoft)
                        }
                    }
                }
                Section {
                    Toggle("リマインドする", isOn: $remind)
                    if remind {
                        DatePicker("時刻", selection: $reminderTime, displayedComponents: .hourAndMinute)
                    }
                } footer: {
                    Text("通知は1日1回、うにのひとことだけ。急かしません。")
                }
            }
            .navigationTitle(habit == nil ? "習慣を追加" : "習慣を編集")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("やめる") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("ほぞん") { save() }
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || weekdays.isEmpty)
                }
            }
            .onAppear(perform: load)
        }
    }

    private func load() {
        guard let habit else { return }
        name = habit.name
        weekdays = Set(habit.weekdays)
        if let h = habit.reminderHour, let m = habit.reminderMinute {
            remind = true
            reminderTime = Calendar.current.date(from: DateComponents(hour: h, minute: m)) ?? reminderTime
        }
    }

    private func save() {
        let comps = Calendar.current.dateComponents([.hour, .minute], from: reminderTime)
        let target = habit ?? Habit(name: name, sortOrder: nextSortOrder)
        target.name = name.trimmingCharacters(in: .whitespaces)
        target.weekdays = weekdays.sorted()
        target.reminderHour = remind ? comps.hour : nil
        target.reminderMinute = remind ? comps.minute : nil
        if habit == nil { context.insert(target) }
        Task { await NotificationService.shared.reschedule(for: target) }
        dismiss()
    }
}

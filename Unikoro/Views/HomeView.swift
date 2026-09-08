import SwiftUI
import SwiftData

/// ホーム画面。うに ＋ 今日の習慣リスト。チェックも取り消しも1タップ。
struct HomeView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \Habit.sortOrder) private var habits: [Habit]
    @State private var showingEditor = false
    @State private var editingHabit: Habit?

    private let calendar = Calendar.current
    private var today: Date { .now }
    private var yesterday: Date { calendar.date(byAdding: .day, value: -1, to: today) ?? today }

    /// 今日やる予定の習慣
    private var todaysHabits: [Habit] { habits.filter { $0.isScheduled(on: today) } }

    private var mood: UniMood {
        let scheduledY = habits.filter { $0.isScheduled(on: yesterday) }
        return UniMoodResolver.resolve(
            scheduledToday: todaysHabits.count,
            completedToday: todaysHabits.filter { $0.isCompleted(on: today) }.count,
            scheduledYesterday: scheduledY.count,
            completedYesterday: scheduledY.filter { $0.isCompleted(on: yesterday) }.count
        )
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: Theme.spacing * 1.5) {
                    UniView(mood: mood)
                        .padding(.top, Theme.spacing)

                    if habits.isEmpty {
                        emptyState
                    } else if todaysHabits.isEmpty {
                        Text("きょうは おやすみの日")
                            .foregroundStyle(Theme.inkSoft)
                    } else {
                        VStack(spacing: 10) {
                            ForEach(todaysHabits) { habit in
                                HabitRow(habit: habit, isDone: habit.isCompleted(on: today)) {
                                    toggle(habit)
                                }
                                .contextMenu {
                                    Button("へんしゅう") { editingHabit = habit }
                                    Button("けす", role: .destructive) { context.delete(habit) }
                                }
                            }
                        }
                        .padding(.horizontal)
                    }
                }
                .padding(.bottom, 40)
            }
            .background(Theme.background)
            .navigationTitle("うにコロ")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingEditor = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .disabled(habits.count >= Habit.maxCount)
                    .accessibilityLabel("習慣を追加")
                }
            }
            .sheet(isPresented: $showingEditor) {
                HabitEditView(habit: nil, nextSortOrder: habits.count)
            }
            .sheet(item: $editingHabit) { habit in
                HabitEditView(habit: habit, nextSortOrder: habits.count)
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Text("つづけたいことを 1つ、おしえて")
                .foregroundStyle(Theme.ink)
            Text("右上の ＋ から追加できます（\(Habit.maxCount)つまで）")
                .font(.footnote)
                .foregroundStyle(Theme.inkSoft)
        }
        .padding()
    }

    /// 達成の記録をつける／取り消す
    private func toggle(_ habit: Habit) {
        if let log = habit.log(on: today) {
            context.delete(log)
        } else {
            context.insert(HabitLog(day: today, habit: habit))
        }
    }
}

/// 習慣1行。左が名前、右が丸いチェック。
struct HabitRow: View {
    let habit: Habit
    let isDone: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack {
                Text(habit.name)
                    .foregroundStyle(Theme.ink)
                Spacer()
                Circle()
                    .strokeBorder(isDone ? Theme.done : Theme.inkSoft.opacity(0.4), lineWidth: 2)
                    .background(Circle().fill(isDone ? Theme.done : .clear))
                    .frame(width: 28, height: 28)
                    .overlay {
                        if isDone {
                            Image(systemName: "checkmark")
                                .font(.caption.bold())
                                .foregroundStyle(.white)
                        }
                    }
            }
            .padding()
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
        }
        .buttonStyle(.plain)
        .sensoryFeedback(.success, trigger: isDone)
    }
}

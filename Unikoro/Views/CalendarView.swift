import SwiftUI
import SwiftData

/// きろく画面。月表示のカレンダーと累計。
/// 達成した日だけ色をつけ、未達成の日には何も出しません（×を出さない）。
struct CalendarView: View {
    @Query private var logs: [HabitLog]
    @State private var month: Date = Calendar.current.startOfDay(for: .now)

    private let calendar = Calendar.current

    /// 1チェック＝100m 換算で「うにと歩いた距離」
    private var distanceKm: Double { Double(logs.count) * 0.1 }

    /// 達成記録がある日の集合
    private var doneDays: Set<Date> { Set(logs.map { calendar.startOfDay(for: $0.day) }) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: Theme.spacing * 1.5) {
                    summary
                    monthHeader
                    monthGrid
                }
                .padding()
            }
            .background(Theme.background)
            .navigationTitle("きろく")
        }
    }

    private var summary: some View {
        HStack {
            stat(title: "これまでに", value: "\(logs.count) 回")
            stat(title: "うにと歩いた", value: String(format: "%.1f km", distanceKm))
        }
    }

    private func stat(title: String, value: String) -> some View {
        VStack(spacing: 4) {
            Text(title).font(.caption).foregroundStyle(Theme.inkSoft)
            Text(value).font(.title3.weight(.medium)).foregroundStyle(Theme.ink)
        }
        .frame(maxWidth: .infinity)
        .padding()
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.cornerRadius))
    }

    private var monthHeader: some View {
        HStack {
            Button { shift(-1) } label: { Image(systemName: "chevron.left") }
            Spacer()
            Text(month, format: .dateTime.year().month(.wide))
                .foregroundStyle(Theme.ink)
            Spacer()
            Button { shift(1) } label: { Image(systemName: "chevron.right") }
        }
        .foregroundStyle(Theme.inkSoft)
    }

    private func shift(_ delta: Int) {
        if let d = calendar.date(byAdding: .month, value: delta, to: month) { month = d }
    }

    private var monthGrid: some View {
        let columns = Array(repeating: GridItem(.flexible()), count: 7)
        return LazyVGrid(columns: columns, spacing: 10) {
            ForEach(calendar.shortWeekdaySymbols, id: \.self) { s in
                Text(s).font(.caption2).foregroundStyle(Theme.inkSoft)
            }
            ForEach(cells, id: \.self) { cell in
                if let day = cell.date {
                    let done = doneDays.contains(day)
                    Text("\(calendar.component(.day, from: day))")
                        .font(.callout)
                        .foregroundStyle(done ? .white : Theme.ink)
                        .frame(width: 36, height: 36)
                        .background(done ? Theme.done : Color.clear, in: Circle())
                } else {
                    Color.clear.frame(width: 36, height: 36)
                }
            }
        }
    }

    /// 月のマス目。月初より前の空白は date が nil。
    private struct Cell: Hashable { let index: Int; let date: Date? }

    private var cells: [Cell] {
        guard let interval = calendar.dateInterval(of: .month, for: month) else { return [] }
        let first = interval.start
        let leading = calendar.component(.weekday, from: first) - 1
        let count = calendar.range(of: .day, in: .month, for: first)?.count ?? 0
        var result: [Cell] = (0..<leading).map { Cell(index: $0, date: nil) }
        for i in 0..<count {
            result.append(Cell(index: leading + i, date: calendar.date(byAdding: .day, value: i, to: first)))
        }
        return result
    }
}

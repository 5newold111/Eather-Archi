import SwiftUI

/// うにの表情とセリフ。
/// いまは仮画像（SF Symbols）。本番のイラストが Assets に入ったら `Image(mood.imageName)` に差し替えます。
struct UniView: View {
    let mood: UniMood

    var body: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(Theme.accent.opacity(0.25))
                    .frame(width: 160, height: 160)
                Image(systemName: mood.placeholderSymbol)
                    .font(.system(size: 64, weight: .light))
                    .foregroundStyle(Theme.ink)
            }
            .accessibilityLabel("うに")

            Text(mood.line)
                .font(.body)
                .foregroundStyle(Theme.ink)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(Theme.surface, in: Capsule())
                .animation(.easeInOut, value: mood)
        }
    }
}

#Preview {
    VStack {
        ForEach(UniMood.allCases, id: \.self) { UniView(mood: $0) }
    }
    .background(Theme.background)
}

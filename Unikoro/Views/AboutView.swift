import SwiftUI

/// うにと、収益の使い道についての説明。
/// 寄付先の団体名は掲載許諾が取れるまで出しません（企画書 第6章）。
/// 「寄付する」「募金」といったボタンや文言は置きません（ストアのルール）。
struct AboutView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.spacing) {
                Text("うにについて").font(.title3.weight(.medium))
                Text("うには、このアプリを作った人と暮らしているシーズーです。あなたが習慣を続けると、うにがよろこびます。休んだ日は、うにも寝ています。")

                Text("収益の使い道").font(.title3.weight(.medium)).padding(.top)
                Text("このアプリは無料で使えます。今後追加するサポーターパス（有料の追加機能）の売上のうち、手数料を除いた 30% は保護犬・保護猫の支援団体の活動に使われ、残りはうにの養育費とアプリの運営費にあてます。内訳は毎月公開します。")

                Text("データについて").font(.title3.weight(.medium)).padding(.top)
                Text("記録はこの iPhone の中にだけ保存されます。アカウント登録はなく、サーバーに送られることもありません。")
            }
            .foregroundStyle(Theme.ink)
            .padding()
        }
        .background(Theme.background)
        .navigationTitle("うにと、お金のこと")
        .navigationBarTitleDisplayMode(.inline)
    }
}

import XCTest
@testable import Unikoro

/// うにの表情判定のテスト。企画書 第4章の5表情が仕様どおりに出るかを確かめます。
final class UniMoodTests: XCTestCase {
    private func at(hour: Int) -> Date {
        Calendar.current.date(bySettingHour: hour, minute: 0, second: 0, of: .now)!
    }

    func test全部達成でごきげん() {
        let m = UniMoodResolver.resolve(scheduledToday: 3, completedToday: 3,
                                        scheduledYesterday: 3, completedYesterday: 0, now: at(hour: 10))
        XCTAssertEqual(m, .delighted)
    }

    func test一部達成でうれしい() {
        let m = UniMoodResolver.resolve(scheduledToday: 3, completedToday: 1,
                                        scheduledYesterday: 3, completedYesterday: 0, now: at(hour: 22))
        XCTAssertEqual(m, .happy)
    }

    func test夜に未達成でねむい() {
        let m = UniMoodResolver.resolve(scheduledToday: 2, completedToday: 0,
                                        scheduledYesterday: 2, completedYesterday: 2, now: at(hour: 21))
        XCTAssertEqual(m, .sleepy)
    }

    func test昨日ゼロで朝はおやすみ() {
        let m = UniMoodResolver.resolve(scheduledToday: 2, completedToday: 0,
                                        scheduledYesterday: 2, completedYesterday: 0, now: at(hour: 8))
        XCTAssertEqual(m, .resting)
    }

    func test通常はふつう() {
        let m = UniMoodResolver.resolve(scheduledToday: 2, completedToday: 0,
                                        scheduledYesterday: 2, completedYesterday: 1, now: at(hour: 12))
        XCTAssertEqual(m, .normal)
    }

    func test予定ゼロの日は夜でもねむくならない() {
        let m = UniMoodResolver.resolve(scheduledToday: 0, completedToday: 0,
                                        scheduledYesterday: 0, completedYesterday: 0, now: at(hour: 23))
        XCTAssertEqual(m, .normal)
    }
}

"""決定 179 の試験：Mac の中にしか無い記録が、R2 の予備保管の対象に入っている（鍵とログは入らない）"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixture import Project  # noqa: E402


class BackupScopeTest(unittest.TestCase):
    def test_state_and_records_are_backed_up(self):
        p = Project(ssd=False)
        try:
            key = p.weekly("hollowplan", p.week(-2), "Keep Me", stage="registered")
            for rel in ("out/money/ledger.json", "out/mastering/settings.json", "out/plays/snapshots.json", "out/notion/map.json",
                        "out/knowledge/artist_links.json", "out/logs/intake.log"):
                f = p.root / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("{}", encoding="utf-8")
            (p.root / ".env").write_text("SECRET=1\n", encoding="utf-8")
            b = p.mod("backup_r2")
            got = {f.relative_to(p.root).as_posix() for f in b.targets()}
            for want in (f"out/publish/state/{key}.json", f"out/publish/{key}/DistroKid下書き.md", "out/money/ledger.json",
                         "out/mastering/settings.json", "out/plays/snapshots.json", "out/notion/map.json", "out/knowledge/artist_links.json",
                         f"out/briefs/{key}.json", f"out/masters/{key}/master.wav"):
                self.assertIn(want, got, want)
            self.assertFalse(any(".env" in g for g in got))             # 鍵は入れない（金庫が暗号にして預ける）
            self.assertFalse(any(g.startswith("out/logs/") for g in got))
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()

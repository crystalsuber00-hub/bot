"""Tests for poster.py against a fake Instagram API. Run: python3 -m unittest autopost/test_poster.py"""
import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import poster

REAL = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("America/New_York")
CFG = {"timezone": "America/New_York", "start_date": "2026-10-01", "public_base_url": "https://me.github.io/rtt",
       "post_reels": False, "max_late_minutes": 180, "api_version": "v25.0"}


class FakeInstagram:
    """Records calls; containers finish immediately unless told otherwise."""

    def __init__(self, recent=None, status="FINISHED"):
        self.calls, self.n, self._recent, self.status = [], 0, recent or [], status

    def __call__(self, method, path, params):
        self.calls.append((method, path, dict(params)))
        if path == "me":
            return {"user_id": "1789", "username": "readthistwice"}
        if path.endswith("/media") and method == "GET":
            return {"data": self._recent}
        if path.endswith("/media") or path.endswith("/media_publish"):
            self.n += 1
            return {"id": f"c{self.n}"}
        if params.get("fields", "").startswith("status_code"):
            return {"status_code": self.status, "status": "Error: media fetch failed"}
        if params.get("fields") == "permalink":
            return {"permalink": f"https://www.instagram.com/p/{path}/"}
        raise AssertionError(f"unexpected call {method} {path}")

    def creates(self):
        return [p for m, path, p in self.calls if m == "POST" and path.endswith("/media")]


class PosterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("output", "posts", "reels"):
            (self.root / d).symlink_to(REAL / d)
        (self.root / "autopost").mkdir()
        self.out = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_at(self, when, fake, cfg=CFG, dry=False):
        api = poster.InstagramAPI("tok", "v25.0", request=fake, sleep=lambda s: None)
        return poster.run(cfg, api, when, root=self.root, dry_run=dry, out=self.out.append)

    def log(self):
        return poster.read_log(self.root)

    def test_queue_covers_every_post_and_file(self):
        q = poster.build_queue(CFG, self.root)
        self.assertEqual(len(q), 100)
        self.assertEqual(sum(i.kind == "carousel" for q_ in [q] for i in q_), 15)
        for item in q:
            for f in item.files:
                self.assertTrue((self.root / f).exists(), f)
        self.assertEqual(q[0].due, datetime(2026, 10, 5, 7, 30, tzinfo=TZ))
        with_reels = poster.build_queue({**CFG, "post_reels": True}, self.root)
        reels = [i for i in with_reels if i.kind == "reel"]
        self.assertEqual(len(reels), 10)
        self.assertTrue(all((self.root / r.cover).exists() for r in reels))

    def test_start_date_skips_earlier_posts(self):
        self.assertEqual(poster.build_queue({**CFG, "start_date": "2026-11-07"}, self.root)[0].key, "RTT100")

    def test_publishes_single_image_once(self):
        fake = FakeInstagram()
        self.assertTrue(self.run_at(datetime(2026, 10, 5, 7, 35, tzinfo=TZ), fake))
        (create,) = fake.creates()
        self.assertTrue(create["image_url"].startswith("https://me.github.io/rtt/posts/2026-10-05_0730_RTT001.jpg"))
        self.assertIn("Follow @readthistwice", create["caption"])
        self.assertTrue(create["alt_text"].startswith("Nobody talks about"))
        self.assertEqual([r["key"] for r in self.log()], ["RTT001"])
        self.assertEqual(self.log()[0]["permalink"], "https://www.instagram.com/p/c2/")
        # Next run in the same slot: nothing new is due.
        fake2 = FakeInstagram()
        self.assertFalse(self.run_at(datetime(2026, 10, 5, 7, 50, tzinfo=TZ), fake2))
        self.assertEqual(fake2.creates(), [])
        self.assertIn("Next: RTT002", self.out[-1])

    def test_late_posts_are_missed_not_burst(self):
        fake = FakeInstagram()
        self.run_at(datetime(2026, 10, 5, 12, 5, tzinfo=TZ), fake)
        self.assertEqual([(r["key"], r["status"]) for r in self.log()], [("RTT001", "missed"), ("RTT002", "published")])
        self.assertEqual(len(fake.creates()), 1)

    def test_one_post_per_run_when_two_are_due(self):
        fake = FakeInstagram()
        self.run_at(datetime(2026, 10, 5, 12, 5, tzinfo=TZ), fake, cfg={**CFG, "max_late_minutes": 600})
        self.assertEqual([r["key"] for r in self.log()], ["RTT001"])
        self.assertIn("Next run: RTT002", self.out[-1])

    def test_carousel_builds_children_then_parent(self):
        fake = FakeInstagram()
        self.run_at(datetime(2026, 10, 6, 7, 31, tzinfo=TZ), fake, cfg={**CFG, "start_date": "2026-10-06"})
        creates = fake.creates()
        self.assertEqual(len(creates), 8)
        self.assertTrue(all(c["is_carousel_item"] == "true" for c in creates[:7]))
        self.assertTrue(creates[6]["image_url"].endswith("RTT004-7.jpg"))
        self.assertEqual(creates[7]["media_type"], "CAROUSEL")
        self.assertEqual(creates[7]["children"], "c1,c2,c3,c4,c5,c6,c7")

    def test_reel(self):
        fake = FakeInstagram()
        self.run_at(datetime(2026, 10, 7, 17, 2, tzinfo=TZ), fake,
                    cfg={**CFG, "start_date": "2026-10-07", "post_reels": True, "max_late_minutes": 5})
        reel = fake.creates()[-1]
        self.assertEqual(reel["media_type"], "REELS")
        self.assertTrue(reel["video_url"].endswith("reels/2026-10-07_1700_REEL-RTT002.mp4"))
        self.assertTrue(reel["cover_url"].endswith("reels/2026-10-07_1700_REEL-RTT002-cover.jpg"))
        self.assertEqual(self.log()[-1]["key"], "REEL-RTT002")

    def test_recovers_post_that_was_published_but_not_logged(self):
        now = datetime(2026, 10, 5, 7, 50, tzinfo=TZ)
        caption = poster.build_queue(CFG, self.root)[0].caption
        stamp = (now - timedelta(minutes=18)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S+0000")
        fake = FakeInstagram(recent=[{"id": "m9", "caption": caption, "timestamp": stamp, "permalink": "https://x/p/m9"}])
        self.run_at(now, fake)
        self.assertEqual(fake.creates(), [])
        self.assertEqual((self.log()[0]["status"], self.log()[0]["media_id"]), ("published", "m9"))

    def test_failed_container_is_not_logged(self):
        with self.assertRaises(poster.APIError):
            self.run_at(datetime(2026, 10, 5, 7, 35, tzinfo=TZ), FakeInstagram(status="ERROR"))
        self.assertEqual(self.log(), [])

    def test_dry_run_changes_nothing(self):
        fake = FakeInstagram()
        self.run_at(datetime(2026, 10, 5, 12, 5, tzinfo=TZ), fake, dry=True)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.log(), [])
        self.assertTrue(any(line.startswith("WOULD PUBLISH RTT002") for line in self.out))

    def test_shipped_log_has_header_only(self):
        with open(REAL / "autopost" / "published.csv", newline="") as f:
            self.assertEqual(next(csv.reader(f)), poster.LOG_FIELDS)


if __name__ == "__main__":
    unittest.main()

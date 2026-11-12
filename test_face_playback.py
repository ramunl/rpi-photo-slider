"""Deterministic gated preparation; no model or Tk required."""

import unittest

from playback import Playback


class GateWorker:
    def __init__(self):
        self.jobs = []
        self.ready = []
        self.cancelled = 0
        self.closed = False

    def submit(self, job):
        self.jobs.append(job)

    def poll(self):
        return self.ready.pop(0) if self.ready else None

    def cancel(self):
        self.cancelled += 1

    def close(self):
        self.closed = True

    def release(self, job=None, **result):
        job = job or self.jobs[-1]
        self.ready.append(
            (
                job["token"],
                dict(
                    image="pixels",
                    stamp=1,
                    zoom=1.4 if job["enabled"] else 1,
                    pan=(0.8, 0),
                    **result,
                ),
            )
        )


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.worker = GateWorker()
        self.frames = []
        self.failures = []
        self.stamp = 1
        self.player = Playback(
            self.worker,
            lambda *args: self.frames.append(args),
            lambda: self.failures.append(True),
            clock=lambda: self.now,
            stamp=lambda path: self.stamp,
        )
        self.photos = [{"path": str(n)} for n in range(4)]

    def display(self):
        self.worker.release()
        self.player.tick()

    def test_priority_deduplication_and_bounded_prefetch(self):
        self.player.enabled = True
        self.player.start(self.photos + [self.photos[1]])
        self.assertEqual([j["photo"]["path"] for j in self.worker.jobs], ["0"])
        for _ in range(100):
            self.player.tick()
        self.assertEqual(len(self.worker.jobs), 1)
        self.display()
        self.assertEqual(self.worker.jobs[-1]["photo"]["path"], "1")
        self.assertFalse(self.worker.jobs[-1]["cache_only"])
        self.display()
        self.assertEqual(self.worker.jobs[-1]["photo"]["path"], "2")
        self.assertTrue(self.worker.jobs[-1]["cache_only"])
        self.display()
        self.display()
        self.assertEqual(
            [j["photo"]["path"] for j in self.worker.jobs], ["0", "1", "2", "3"]
        )
        self.assertEqual(len(self.frames), 1)
        self.player.request(1)
        self.assertEqual(len(self.frames), 2)  # reuse next image, no decode

    def test_first_paint_and_timer_start_on_display(self):
        self.player.enabled = True
        self.player.start(self.photos)
        self.now = 100
        self.player.tick()
        self.assertEqual(self.frames, [])
        self.display()
        self.assertEqual(self.frames[0][2]["zoom"], 1.4)
        self.assertEqual(self.frames[0][2]["pan"], (0.8, 0))
        self.assertEqual(self.player.deadline, 108)
        self.player.request(2)
        self.assertEqual(len(self.frames), 1)  # previous stays visible

    def test_controls_and_stale_request_album_settings_manual(self):
        self.player.start(self.photos)
        old = self.worker.jobs[-1]
        self.player.pause(True)
        self.player.request(3)
        self.assertEqual(self.worker.jobs[-1]["photo"]["path"], "3")
        self.player.start([{"path": "new"}])
        album_job = self.worker.jobs[-1]
        self.player.settings(enabled=True)
        setting_job = self.worker.jobs[-1]
        self.player.manual_frame(2, (0.2, 0))
        for obsolete in (old, album_job, setting_job):
            self.worker.release(obsolete)
            self.player.tick()
        self.assertEqual(self.frames, [])
        self.assertEqual(self.worker.jobs[-1]["zoom"], 2)
        self.assertTrue(self.worker.jobs[-1]["manual"])
        self.display()
        self.assertEqual(self.frames[-1][1]["path"], "new")
        self.player.manual_frame(1)
        self.assertEqual(self.worker.jobs[-1]["pan"], (0, 0))
        self.player.close()
        self.assertTrue(self.worker.closed)

    def test_timeout_fallback_never_late_reframes(self):
        self.player.enabled = True
        self.player.start(self.photos)
        late = self.worker.jobs[-1]
        self.worker.release(fatal="timeout")
        self.player.tick()
        self.assertFalse(self.worker.jobs[-1]["enabled"])
        self.display()
        self.assertEqual(self.frames[-1][2]["zoom"], 1)
        self.worker.release(late)
        self.player.tick()
        self.assertEqual(len(self.frames), 1)

    def test_fallback_stays_ordinary_after_resize(self):
        self.player.enabled = True
        self.player.start(self.photos)
        self.worker.release(fatal="timeout")
        self.player.tick()
        self.display()
        self.player.resize((500, 300))
        self.assertFalse(self.worker.jobs[-1]["enabled"])

    def test_source_change_and_bounded_failure(self):
        self.player.start([self.photos[0]])
        self.stamp = 2
        self.display()
        self.assertEqual(self.frames, [])
        self.worker.release(fatal="unreadable")
        self.player.tick()
        self.assertEqual(self.failures, [True])
        for _ in range(20):
            self.player.tick()
        self.assertEqual(len(self.worker.jobs), 2)

    def test_disabled_and_paused_playback(self):
        self.player.start(self.photos)
        self.assertFalse(self.worker.jobs[-1]["enabled"])
        self.display()
        self.player.pause(True)
        self.now = 100
        self.player.tick()
        self.assertEqual(self.player.index, 0)
        self.player.pause(False)
        self.now = 109
        self.player.tick()
        self.assertEqual(self.player.index, 1)

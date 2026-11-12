"""Worker integration uses real images/cache with a deterministic detector."""

import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from face_cache import FaceCache, sha256_file
from face_worker import ProcessWorker, prepare


def hung_worker(data, jobs, results):
    jobs.get()
    time.sleep(60)


def blocked_cache_worker(data, jobs, results):
    job = jobs.get()
    data["entered"].set()
    results.put((job["token"], prepare(data["directory"], job)))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "photo.jpg"
        Image.new("RGB", (100, 60), "red").save(self.path)
        self.job = {
            "token": 1,
            "photo": {"path": str(self.path)},
            "enabled": True,
            "percent": 40,
            "zoom": 1,
            "pan": (0, 0),
            "manual": False,
            "viewport": (80, 80),
            "cache_only": False,
        }

    def test_cache_reuse_and_disabled_does_not_touch_cache(self):
        faces = {
            "width": 100,
            "height": 60,
            "faces": [{"x": 0.8, "y": 0.2, "width": 0.1, "height": 0.2}],
        }
        with patch("face_detector.detect", return_value=faces) as detect:
            first = prepare(self.temp.name, self.job)
            second = prepare(self.temp.name, self.job)
        self.assertEqual(detect.call_count, 1)
        self.assertEqual(first["zoom"], 1.4)
        self.assertEqual(first["pan"], second["pan"])
        with patch("face_worker.FaceCache", side_effect=AssertionError("cache opened")):
            result = prepare(self.temp.name, dict(self.job, enabled=False))
        self.assertEqual(result["zoom"], 1)

    def test_exif_and_normalized_edge_framing(self):
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (100, 60)).save(self.path, exif=exif)
        result = {
            "width": 60,
            "height": 100,
            "faces": [{"x": 0.1, "y": 0.85, "width": 0.2, "height": 0.1}],
        }
        with FaceCache(self.temp.name) as cache:
            cache.put(self.job["photo"], result, sha256_file(self.path))
        prepared = prepare(self.temp.name, self.job)
        self.assertEqual(prepared["size"], (60, 100))
        self.assertEqual(prepared["pan"][1], 1)

    def test_detector_error_returns_ordinary_frame(self):
        with patch("face_detector.detect", side_effect=RuntimeError("no model")):
            result = prepare(self.temp.name, self.job)
        self.assertEqual(result["zoom"], 1)
        self.assertEqual(result["error"], "no model")

    def test_process_transfers_bounded_pixels_and_closes(self):
        worker = ProcessWorker(self.temp.name)
        self.addCleanup(worker.close)
        worker.submit(dict(self.job, enabled=False))
        end = time.monotonic() + 5
        result = None
        while result is None and time.monotonic() < end:
            result = worker.poll()
            time.sleep(0.01)
        self.assertIsNotNone(result)
        self.assertEqual(result[1]["image"].size, (80, 48))
        worker.close()
        self.assertIsNone(worker.process)
        self.assertIsNone(worker.memory)

    def test_hung_worker_deadlines_and_restart_circuit(self):
        now = [0]
        worker = ProcessWorker(
            self.temp.name,
            clock=lambda: now[0],
            timeout=1,
            target=hung_worker,
            max_failures=2,
        )
        self.addCleanup(worker.close)
        for token in range(2):
            worker.submit(dict(self.job, token=token))
            process = worker.process
            now[0] += 2
            result = worker.poll()
            self.assertIn("deadline", result[1]["fatal"])
            self.assertIsNone(worker.process)
            self.assertNotIn(process, multiprocessing.active_children())
        worker.submit(dict(self.job, token=3))
        self.assertIn("unavailable", worker.poll()[1]["fatal"])
        self.assertEqual(worker.failures, 2)

    def test_deadline_includes_sqlite_wait(self):
        context = multiprocessing.get_context("spawn")
        entered = context.Event()
        now = [0]
        with FaceCache(self.temp.name) as cache:
            cache._conn.execute("BEGIN IMMEDIATE")
            worker = ProcessWorker(
                {"directory": self.temp.name, "entered": entered},
                target=blocked_cache_worker,
                context=context,
                clock=lambda: now[0],
                timeout=1,
            )
            self.addCleanup(worker.close)
            worker.submit(self.job)
            self.assertTrue(entered.wait(timeout=3))
            self.assertIsNone(worker.poll())
            now[0] = 2
            self.assertIn("deadline", worker.poll()[1]["fatal"])
            self.assertIsNone(worker.process)
            cache._conn.rollback()

"""Settings validation/persistence unit tests and review HTTP integration tests."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import face_settings
from review import make_server


class FaceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"

    def test_defaults_when_file_missing(self):
        self.assertEqual(
            face_settings.load(self.data),
            {"enabled": False, "zoom_percent": 40},
        )

    def test_validate_accepts_range_and_applies_defaults(self):
        self.assertEqual(
            face_settings.validate({"enabled": True, "zoom_percent": 0}),
            {"enabled": True, "zoom_percent": 0},
        )
        self.assertEqual(
            face_settings.validate({"enabled": True, "zoom_percent": 300}),
            {"enabled": True, "zoom_percent": 300},
        )
        self.assertEqual(
            face_settings.validate({"enabled": True}),
            {"enabled": True, "zoom_percent": 40},
        )
        self.assertEqual(
            face_settings.validate({"zoom_percent": 75}),
            {"enabled": False, "zoom_percent": 75},
        )

    def test_validate_rejects_invalid_values(self):
        for value in [
            "not an object",
            {"enabled": "yes", "zoom_percent": 40},
            {"enabled": 1, "zoom_percent": 40},
            {"enabled": True, "zoom_percent": "40"},
            {"enabled": True, "zoom_percent": True},
            {"enabled": True, "zoom_percent": 301},
            {"enabled": True, "zoom_percent": -1},
            {"enabled": True, "zoom_percent": float("nan")},
            {"enabled": True, "zoom_percent": float("inf")},
        ]:
            with self.assertRaises(ValueError, msg=repr(value)):
                face_settings.validate(value)

    def test_save_and_load_roundtrip(self):
        saved = face_settings.save(self.data, {"enabled": True, "zoom_percent": 125})
        self.assertEqual(saved, {"enabled": True, "zoom_percent": 125})
        self.assertEqual(
            face_settings.load(self.data),
            {"enabled": True, "zoom_percent": 125},
        )

    def test_invalid_save_does_not_change_saved_settings(self):
        face_settings.save(self.data, {"enabled": True, "zoom_percent": 50})
        path = self.data / face_settings.SETTINGS_FILE
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            face_settings.save(self.data, {"enabled": True, "zoom_percent": 999})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(
            face_settings.load(self.data),
            {"enabled": True, "zoom_percent": 50},
        )

    def test_corrupt_file_raises(self):
        self.data.mkdir()
        (self.data / face_settings.SETTINGS_FILE).write_text("not json")
        with self.assertRaises(ValueError):
            face_settings.load(self.data)


class ReviewHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self._start()

    def tearDown(self):
        if hasattr(self, "server"):
            self._stop()

    def _start(self):
        self.server = make_server(self.data, 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def host_header(self, host=None):
        return {"Host": host or f"127.0.0.1:{self.port}"}

    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    def post_headers(self, **extra):
        return {**self.host_header(), "Origin": self.origin(), **extra}

    def call(self, method, path, body=None, headers=None, raw=None):
        headers = dict(headers or {})
        if raw is not None:
            payload = raw
        elif body is not None:
            payload = json.dumps(body).encode()
        else:
            payload = None
        if payload is not None:
            headers.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            conn.close()

    def json(self, method, path, body=None, headers=None):
        status, _, data = self.call(method, path, body, headers)
        try:
            return status, json.loads(data) if data else None
        except json.JSONDecodeError:
            return status, data

    def test_settings_defaults_and_persist_across_restart(self):
        self.assertFalse((self.data / "catalog.json").exists())
        status, data = self.json("GET", "/api/settings", headers=self.host_header())
        self.assertEqual(status, 200)
        self.assertEqual(data, {"settings": {"enabled": False, "zoom_percent": 40}})

        with mock.patch("review.request", return_value={}):
            status, data = self.json(
                "POST",
                "/api/settings",
                {"enabled": True, "zoom_percent": 75},
                headers=self.post_headers(),
            )
        self.assertEqual(status, 200)
        self.assertTrue(data["applied"])
        self.assertEqual(data["settings"], {"enabled": True, "zoom_percent": 75})

        # Restart the review service on the same data directory.
        self._stop()
        self._start()
        status, data = self.json("GET", "/api/settings", headers=self.host_header())
        self.assertEqual(status, 200)
        self.assertEqual(data, {"settings": {"enabled": True, "zoom_percent": 75}})

    def test_invalid_bodies_rejected_without_changing_saved(self):
        headers = self.post_headers()
        with mock.patch("review.request", return_value={}):
            status, _ = self.json(
                "POST",
                "/api/settings",
                {"enabled": True, "zoom_percent": 50},
                headers=headers,
            )
            self.assertEqual(status, 200)
        path = self.data / face_settings.SETTINGS_FILE
        before = path.read_bytes()
        for body in [
            {"enabled": "yes", "zoom_percent": 50},
            {"enabled": True, "zoom_percent": 301},
            {"enabled": True, "zoom_percent": -1},
            {"enabled": True, "zoom_percent": "abc"},
            {"enabled": True, "zoom_percent": None},
        ]:
            status, data = self.json("POST", "/api/settings", body, headers=headers)
            self.assertEqual(status, 400, msg=repr(body))
            self.assertIn("error", data)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(
            face_settings.load(self.data), {"enabled": True, "zoom_percent": 50}
        )

    def test_invalid_json_and_empty_bodies_rejected(self):
        headers = self.post_headers()
        status, _, _ = self.call(
            "POST", "/api/settings", raw=b"{not json", headers=headers
        )
        self.assertEqual(status, 400)
        status, _, _ = self.call("POST", "/api/settings", raw=b"", headers=headers)
        self.assertEqual(status, 400)

    def test_player_failure_reported_and_settings_still_saved(self):
        with mock.patch(
            "review.request", side_effect=OSError("connection refused")
        ):
            status, data = self.json(
                "POST",
                "/api/settings",
                {"enabled": True, "zoom_percent": 25},
                headers=self.post_headers(),
            )
        self.assertEqual(status, 200)
        self.assertFalse(data["applied"])
        self.assertIn("player", data["message"].lower())
        self.assertEqual(data["settings"], {"enabled": True, "zoom_percent": 25})
        self.assertEqual(
            face_settings.load(self.data), {"enabled": True, "zoom_percent": 25}
        )

    def test_localhost_host_enforcement(self):
        status, data = self.json(
            "GET", "/api/settings", headers={"Host": "example.com"}
        )
        self.assertEqual(status, 403)
        self.assertIn("error", data)

        status, data = self.json(
            "GET", "/api/settings", headers={"Host": "127.0.0.1"}
        )
        self.assertEqual(status, 403)

        status, data = self.json(
            "GET", "/api/settings", headers={"Host": f"localhost:{self.port}"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data, {"settings": {"enabled": False, "zoom_percent": 40}})

        status, data = self.json("GET", "/api/settings", headers=self.host_header())
        self.assertEqual(status, 200)

    def test_missing_and_foreign_origin_rejected(self):
        # Missing Origin is rejected for writes.
        status, data = self.json(
            "POST",
            "/api/settings",
            {"enabled": True, "zoom_percent": 10},
            headers=self.host_header(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(data, {"error": "Same-origin requests required"})

        # A foreign Origin is rejected even with a local Host.
        status, data = self.json(
            "POST",
            "/api/settings",
            {"enabled": True, "zoom_percent": 10},
            headers={**self.host_header(), "Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)

        # The matching local Origin is accepted.
        with mock.patch("review.request", return_value={}):
            status, data = self.json(
                "POST",
                "/api/settings",
                {"enabled": True, "zoom_percent": 10},
                headers=self.post_headers(),
            )
        self.assertEqual(status, 200)
        self.assertTrue(data["applied"])

    def test_existing_endpoint_protections_retained(self):
        status, data = self.json("GET", "/api/catalog", headers=self.host_header())
        self.assertEqual(status, 503)
        self.assertEqual(data, {"error": "Audit is still running"})

        with mock.patch("review.request") as player_request:
            status, data = self.json(
                "POST",
                "/api/player",
                {"command": "rm -rf"},
                headers=self.post_headers(),
            )
            self.assertEqual(status, 400)
            self.assertIn("Invalid command", data["error"])
            player_request.assert_not_called()

        status, _, raw = self.call("GET", "/", headers=self.host_header())
        self.assertEqual(status, 200)
        self.assertIn(b"review.css", raw)

        status, _, raw = self.call(
            "GET", "/static/review.js", headers=self.host_header()
        )
        self.assertEqual(status, 200)
        self.assertIn(b"PhotoReviewApi", raw)

        status, _, raw = self.call(
            "GET", "/static/review.css", headers=self.host_header()
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()

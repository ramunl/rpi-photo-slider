"""Local review UI. Access remotely through an SSH tunnel; no cloud uploads."""
import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import face_settings
from catalog import atomic_json, build
from folders import add_folder, choose_folder, summaries
from player import request

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static" / "review"


def make_server(data, port, library=None):
    writes = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, body, content_type="application/json", status=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def local_request(self, write=False):
            host = self.headers.get("Host", "")
            port = self.server.server_address[1]
            if host not in {f"localhost:{port}", f"127.0.0.1:{port}"}:
                return False
            origin = self.headers.get("Origin")
            return not write or origin == f"http://{host}"

        def do_GET(self):
            if not self.local_request():
                return self.respond(
                    {"error": "Use localhost through an SSH tunnel"}, status=403
                )
            path = urlsplit(self.path).path
            if path == "/":
                return self.respond(
                    (BASE / "review.html").read_bytes(), "text/html; charset=utf-8"
                )
            if path == "/static/review.css":
                return self.respond(
                    (STATIC / "review.css").read_bytes(), "text/css; charset=utf-8"
                )
            if path == "/static/api.js":
                return self.respond(
                    (STATIC / "api.js").read_bytes(),
                    "application/javascript; charset=utf-8",
                )
            if path == "/static/review.js":
                return self.respond(
                    (STATIC / "review.js").read_bytes(),
                    "application/javascript; charset=utf-8",
                )
            if path == "/static/settings.js":
                return self.respond(
                    (STATIC / "settings.js").read_bytes(),
                    "application/javascript; charset=utf-8",
                )
            if path == "/api/settings":
                try:
                    return self.respond({"settings": face_settings.load(data)})
                except ValueError as exc:
                    return self.respond({"error": str(exc)}, status=400)
            if path == "/api/catalog":
                if not (data / "catalog.json").exists():
                    return self.respond({"error": "Audit is still running"}, status=503)
                return self.respond((data / "catalog.json").read_bytes())
            if path == "/api/folders":
                return self.respond(summaries(data))
            if re.fullmatch(r"/thumb/[a-f0-9]{20}", path):
                thumb = data / "thumbs" / (path.rsplit("/", 1)[1] + ".jpg")
                if thumb.exists():
                    return self.respond(thumb.read_bytes(), "image/jpeg")
            return self.respond({"error": "Not found"}, status=404)

        def do_POST(self):
            with writes:
                self.handle_post()

        def handle_post(self):
            if not self.local_request(write=True):
                return self.respond(
                    {"error": "Same-origin requests required"}, status=403
                )
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 1_000_000:
                    raise ValueError("Invalid body length")
                body = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError) as exc:
                return self.respond({"error": str(exc)}, status=400)
            path = urlsplit(self.path).path
            try:
                if path == "/api/settings":
                    return self.save_settings(body)
                if path == "/api/folders/add":
                    folder = choose_folder()
                    if folder is None:
                        return self.respond(
                            {"cancelled": True, "message": "No folder added."}
                        )
                    album = add_folder(data, folder)
                    return self.respond(
                        {
                            "album": album,
                            "message": f"Added {album['name']} — {album['count']} photos."
                            + (
                                f" Skipped {album['skipped']} unreadable images."
                                if album["skipped"]
                                else ""
                            ),
                        }
                    )
                if path == "/api/decision":
                    catalog = json.loads((data / "catalog.json").read_text())
                    valid = {r["id"] for r in catalog["items"]}
                    ids = body.get("ids", [])
                    classification = body.get("classification")
                    year = body.get("year")
                    if not ids or any(i not in valid for i in ids):
                        raise ValueError("Select valid items")
                    if classification not in {None, "photo", "exclude", "reset"}:
                        raise ValueError("Invalid classification")
                    if year and (
                        not str(year).isdigit() or not 1900 <= int(year) <= 2100
                    ):
                        raise ValueError("Invalid year")
                    decisions = (
                        json.loads((data / "decisions.json").read_text())
                        if (data / "decisions.json").exists()
                        else {}
                    )
                    for id in ids:
                        if classification == "reset":
                            decisions.pop(id, None)
                        else:
                            decision = decisions.setdefault(id, {})
                            if classification:
                                decision["classification"] = classification
                            if year:
                                decision["year"] = int(year)
                    atomic_json(data / "decisions.json", decisions)
                    return self.respond(
                        {
                            "saved": len(ids),
                            "message": "Decisions saved. Apply changes to update playlists.",
                        }
                    )
                if path == "/api/apply":
                    return self.respond(build(data, library, partial=not (data / "audit.json").exists()))
                if path == "/api/player":
                    if body.get("command") not in {
                        "start",
                        "stop",
                        "pause",
                        "resume",
                        "next",
                        "previous",
                        "status",
                        "picker",
                        "zoom_in",
                        "zoom_out",
                        "zoom_reset",
                        "pan_left",
                        "pan_right",
                        "pan_up",
                        "pan_down",
                        "settings",
                    }:
                        raise ValueError("Invalid command")
                    return self.respond(request(body))
                return self.respond({"error": "Not found"}, status=404)
            except Exception as exc:  # noqa: BLE001
                return self.respond({"error": str(exc)}, status=400)

        def save_settings(self, body):
            try:
                saved = face_settings.save(data, body)
            except ValueError as exc:
                return self.respond({"error": str(exc)}, status=400)
            try:
                request(
                    {
                        "command": "settings",
                        "enabled": saved["enabled"],
                        "zoom_percent": saved["zoom_percent"],
                    }
                )
            except (OSError, RuntimeError) as exc:
                return self.respond(
                    {
                        "settings": saved,
                        "applied": False,
                        "message": (
                            "Saved for next start; the player could not be updated"
                            f" ({exc})."
                        ),
                    }
                )
            return self.respond(
                {
                    "settings": saved,
                    "applied": True,
                    "message": "Face settings saved and applied to the player.",
                }
            )

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(data, port, library):
    server = make_server(data, port, library)
    print(f"Review: http://127.0.0.1:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, default=BASE / "data")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--library", default="/mnt/backup2tb/Library")
    args = p.parse_args()
    serve(args.data, args.port, args.library)

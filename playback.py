"""Tk-independent asynchronous slideshow coordination, driven by a UI tick."""

import logging
import time

from face_worker import source_stamp

LOG = logging.getLogger(__name__)


def identity(photo):
    return photo.get("id"), photo["path"]


class Playback:
    """Hold the old frame until one atomic image/framing commit.

    Only the requested and next photo transfer pixels. Remaining jobs warm the
    disk cache, with one in flight and no process-side backlog.
    """

    def __init__(
        self,
        worker,
        render,
        failed=lambda: None,
        clock=time.monotonic,
        stamp=source_stamp,
    ):
        self.worker, self.render, self.failed = worker, render, failed
        self.clock, self.stamp = clock, stamp
        self.enabled, self.zoom_percent = False, 40
        self.zoom, self.pan, self.manual = 1.0, (0.0, 0.0), False
        self.viewport = (1920, 1080)
        self.photos = []
        self.index = 0
        self.seconds, self.paused = 8, False
        self.deadline = float("inf")
        self.serial = 0
        self.pending = None
        self.active = None
        self.prefetched = None
        self.warmed = set()
        self.skipped = 0
        self.order = []
        self.display_fallback = False

    def start(self, photos, seconds=8):
        self.stop()
        self.photos = list(photos)
        self.seconds, self.paused = seconds, False
        self.skipped = 0
        if self.photos:
            self.request(0)

    def stop(self):
        self.worker.cancel()
        self.serial += 1
        self.photos = []
        self.pending = self.active = self.prefetched = None
        self.warmed.clear()
        self.order = []
        self.deadline = float("inf")

    def settings(self, enabled=None, zoom_percent=None):
        if enabled is not None:
            self.enabled = bool(enabled)
        if zoom_percent is not None:
            if not 0 <= zoom_percent <= 300:
                raise ValueError("face zoom must be between 0 and 300 percent")
            self.zoom_percent = zoom_percent
        self.prefetched = None
        self.warmed.clear()
        if self.photos:
            self.request(self.index, reset_pan=False)

    def manual_frame(self, zoom, pan=(0, 0)):
        self.zoom, self.pan, self.manual = zoom, pan, True
        self.prefetched = None
        if self.photos:
            self.request(self.index, reset_pan=False)

    def resize(self, viewport):
        # Bound IPC pixels even on unexpectedly large virtual desktops.
        viewport = tuple(max(1, min(4096, int(v))) for v in viewport)
        if viewport != self.viewport:
            self.viewport = viewport
            self.prefetched = None
            if self.photos:
                self.request(
                    self.index, reset_pan=False, fallback=self.display_fallback
                )

    def pause(self, paused):
        self.paused = paused
        self.deadline = self.clock() + self.seconds

    def request(self, index, reset_pan=True, fallback=False):
        if not self.photos:
            return
        self.worker.cancel()
        self.active = None
        self.serial += 1
        self.index = index % len(self.photos)
        if reset_pan:
            self.pan = (0, 0)
        self.pending = self.serial
        self.deadline = float("inf")
        self.order = []
        seen = {identity(self.photos[self.index])}
        for offset in range(1, len(self.photos)):
            candidate = (self.index + offset) % len(self.photos)
            key = identity(self.photos[candidate])
            if key not in seen:
                self.order.append(candidate)
                seen.add(key)
        cached = self.prefetched
        self.prefetched = None
        if cached and cached[0] == self.index and self._valid(cached[1]):
            self._commit(cached[1])
        else:
            self._submit(self.index, "display", fallback=fallback)

    def _submit(self, index, kind, fallback=False):
        self.serial += 1
        job = {
            "token": self.serial,
            "photo": self.photos[index],
            "enabled": self.enabled
            and not fallback
            and (not self.manual or kind == "cache"),
            "percent": self.zoom_percent,
            "zoom": self.zoom,
            "pan": self.pan if kind == "display" else (0, 0),
            "manual": self.manual,
            "viewport": self.viewport,
            "cache_only": kind == "cache",
        }
        self.active = (job["token"], index, kind, fallback)
        self.worker.submit(job)

    def _valid(self, result, index=None):
        try:
            return result.get("stamp") == self.stamp(
                self.photos[self.index if index is None else index]["path"]
            )
        except OSError:
            return False

    def _commit(self, result, fallback=False):
        self.display_fallback = fallback or bool(result.get("error"))
        self.pending = None
        self.skipped = 0
        self.render(self.index, self.photos[self.index], result)
        self.deadline = self.clock() + self.seconds

    def tick(self, advance=True):
        received = self.worker.poll()
        if received and self.active and received[0] == self.active[0]:
            _, index, kind, fallback = self.active
            self.active = None
            result = received[1]
            if result.get("error"):
                LOG.warning("Face preparation failed: %s", result["error"])
            valid = "fatal" not in result and self._valid(result, index)
            if kind == "display":
                if valid:
                    self._commit(result, fallback=fallback)
                elif not fallback:
                    LOG.warning("Using ordinary framing after failed preparation")
                    self._submit(index, "display", fallback=True)
                else:
                    LOG.warning("Skipping photo after bounded preparation failure")
                    self.skipped += 1
                    if self.skipped >= len(self.photos):
                        self.stop()
                        self.failed()
                    else:
                        self.request(index + 1)
            elif kind == "next" and valid:
                self.prefetched = index, result
            if kind != "display":
                self.warmed.add(identity(self.photos[index]))
        if not self.photos:
            return
        if (
            advance
            and not self.paused
            and self.pending is None
            and self.clock() >= self.deadline
        ):
            self.request(self.index + 1)
        if self.active is None and self.pending is None and self.order:
            index = self.order.pop(0)
            if index == (self.index + 1) % len(self.photos):
                self._submit(index, "next")
            elif self.enabled and identity(self.photos[index]) not in self.warmed:
                self._submit(index, "cache")

    def close(self):
        self.stop()
        self.worker.close()

"""Single-flight process preparation. No cache connection crosses a process boundary."""

import logging
import multiprocessing
import queue
import time
from multiprocessing import shared_memory
from pathlib import Path

from face_cache import FaceCache, SourceChangedError
from face_framing import face_frame, fit_photo

LOG = logging.getLogger(__name__)


def source_stamp(path):
    stat = Path(path).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def prepare(data, job):
    from PIL import Image, ImageOps

    photo = job["photo"]
    before = source_stamp(photo["path"])
    faces = []
    error = None
    face_count = None
    face_status = "manual" if job["manual"] else "off"
    if job["enabled"]:
        try:
            with FaceCache(data) as cache:
                result, _ = cache.faces(photo)
                faces = result["faces"]
                face_count = len(faces)
                face_status = "detected"
        except SourceChangedError:
            raise
        except Exception as exc:  # noqa: BLE001 - isolate optional detector/job failures
            error = str(exc)
            face_status = "unavailable"
    if job.get("cache_only"):
        return {"error": error, "stamp": before}
    with Image.open(photo["path"]) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    zoom, pan = job["zoom"], job["pan"]
    if job["enabled"] and not job["manual"]:
        zoom, pan = face_frame(image.size, job["viewport"], faces, job["percent"])
    fitted = fit_photo(image, *job["viewport"], zoom, pan)
    if source_stamp(photo["path"]) != before:
        raise SourceChangedError("source changed during preparation")
    return {
        "face_count": face_count,
        "face_status": face_status,
        "image": fitted,
        "size": image.size,
        "zoom": zoom,
        "pan": pan,
        "stamp": before,
        "error": error,
    }


def _run(data, jobs, results):
    while True:
        job = jobs.get()
        if job is None:
            return
        try:
            result = prepare(data, job)
            if "image" in result:
                image = result.pop("image")
                memory = shared_memory.SharedMemory(name=job["memory"])
                try:
                    pixels = image.tobytes()
                    memory.buf[: len(pixels)] = pixels
                    result["pixels_size"] = image.size
                finally:
                    memory.close()
        except Exception as exc:  # noqa: BLE001 - isolate optional detector/job failures
            result = {"fatal": str(exc)}
        results.put((job["token"], result))


class ProcessWorker:
    """One job and one result maximum; deadlines include SQLite and hashing.

    Repeated hangs open a circuit after three failures, until service restart.
    Cancellation for navigation does not count as a worker failure.
    """

    def __init__(
        self,
        data,
        timeout=5.0,
        clock=time.monotonic,
        context=None,
        target=_run,
        max_failures=3,
    ):
        self.data, self.timeout, self.clock = data, timeout, clock
        self.context = context or multiprocessing.get_context("spawn")
        self.target, self.max_failures = target, max_failures
        self.process = None
        self.active = None
        self.failures = 0
        self.closed = False
        self.memory = None

    def submit(self, job):
        if self.active is not None:
            raise RuntimeError("worker already busy")
        self.active = job
        self.started = self.clock()
        if self.closed or self.failures >= self.max_failures:
            return
        if self.process is None:
            self.jobs = self.context.Queue(maxsize=1)
            self.results = self.context.Queue(maxsize=1)
            self.process = self.context.Process(
                target=self.target,
                args=(self.data, self.jobs, self.results),
                daemon=True,
            )
            self.process.start()
        if not job.get("cache_only"):
            width, height = job["viewport"]
            self.memory = shared_memory.SharedMemory(
                create=True, size=width * height * 3
            )
            job = dict(job, memory=self.memory.name)
        self.jobs.put_nowait(job)

    def poll(self):
        if self.active is None:
            return None
        token = self.active["token"]
        result = None
        unhealthy = False
        if self.closed or self.failures >= self.max_failures:
            result = {"fatal": "preparation worker unavailable"}
        elif self.clock() - self.started >= self.timeout:
            result = {"fatal": "preparation deadline exceeded"}
            unhealthy = True
        elif not self.process.is_alive():
            result = {"fatal": "preparation worker exited"}
            unhealthy = True
        else:
            try:
                received, result = self.results.get_nowait()
                if received != token:
                    return None
            except queue.Empty:
                return None
        self.active = None
        if "pixels_size" in result:
            from PIL import Image

            width, height = result.pop("pixels_size")
            result["image"] = Image.frombytes(
                "RGB", (width, height), bytes(self.memory.buf[: width * height * 3])
            )
        self._release_memory()
        if "fatal" in result:
            LOG.warning("Photo preparation failed: %s", result["fatal"])
            self.failures += int(unhealthy)
            self._terminate()
        return token, result

    def _release_memory(self):
        if self.memory is not None:
            self.memory.close()
            self.memory.unlink()
            self.memory = None

    def _terminate(self):
        if self.process is not None:
            self.process.terminate()
            self.process.join(timeout=0.05)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=0.05)
            self.process.close()
            for channel in (self.jobs, self.results):
                channel.cancel_join_thread()
                channel.close()
            self.process = None
        self._release_memory()

    def cancel(self):
        if self.active is not None:
            self._terminate()
            self.active = None

    def close(self):
        self.cancel()
        self._terminate()
        self.closed = True

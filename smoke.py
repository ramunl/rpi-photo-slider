"""Manual Pi smoke checks; emit a JSON report even when a check fails."""

import argparse
import json
import math
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from face_framing import face_frame

BASE = Path(__file__).resolve().parent


def command(argv, timeout):
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(f"exit {result.returncode}: {detail}")
    return result.stdout.strip()


def record(check):
    started = time.monotonic()
    try:
        result = {"passed": True, "result": check()}
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        subprocess.TimeoutExpired,
    ) as exc:
        result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photos", type=Path, default=Path.home() / "bin/photos")
    parser.add_argument(
        "--fixture", type=Path, default=BASE / "tests/fixtures/faces/single.jpg"
    )
    parser.add_argument("--viewport", type=int, nargs=2, default=(1920, 1080))
    parser.add_argument("--status-budget-ms", type=float, default=1000)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    if any(size <= 0 for size in args.viewport) or any(
        not math.isfinite(value) or value <= 0
        for value in (args.timeout, args.status_budget_ms)
    ):
        parser.error("viewport, timeout and status budget must be positive and finite")
    detector = [sys.executable, str(BASE / "face_detector.py")]

    def status():
        started = time.monotonic()
        response = json.loads(
            command([str(args.photos.expanduser()), "status"], args.timeout)
        )
        elapsed = (time.monotonic() - started) * 1000
        if not isinstance(response, dict) or "error" in response:
            raise RuntimeError(f"Invalid status response: {response}")
        if response.get("playing") is not True:
            raise RuntimeError(
                "Start a slideshow before measuring playback responsiveness"
            )
        if elapsed > args.status_budget_ms:
            raise RuntimeError(
                f"Status took {elapsed:.2f} ms; budget is {args.status_budget_ms} ms"
            )
        return {"round_trip_ms": round(elapsed, 2), "status": response}

    def framing():
        detection = json.loads(
            command([*detector, str(args.fixture.expanduser())], args.timeout)
        )
        faces = detection["faces"]
        if not faces:
            raise RuntimeError("Fixture must contain detectable faces")
        size = (detection["width"], detection["height"])
        zoom, pan = face_frame(size, args.viewport, faces)
        fit = min(v / s for v, s in zip(args.viewport, size))
        for axis, source, screen, offset in zip(("x", "y"), size, args.viewport, pan):
            crop = min(1.0, screen / (source * fit * zoom))
            left = (1 - crop) * (offset + 1) / 2
            extent = "width" if axis == "x" else "height"
            if any(
                box[axis] < left - 1e-6 or box[axis] + box[extent] > left + crop + 1e-6
                for box in faces
            ):
                raise RuntimeError("Framing cropped a detected face")
        return {"detection": detection, "zoom": zoom, "pan": pan}

    checks = {
        "model_verification": record(
            lambda: command([*detector, "--verify"], args.timeout)
        ),
        "playback_responsiveness": record(status),
        "model_loading_and_framing": record(framing),
    }
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "fixture": str(args.fixture),
        "viewport": args.viewport,
        "status_budget_ms": args.status_budget_ms,
        "checks": checks,
    }
    print(json.dumps(report, indent=2))
    return 0 if all(check["passed"] for check in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

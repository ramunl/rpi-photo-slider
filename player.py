"""Fullscreen Raspberry Pi photo player with a private local control socket."""

import argparse
import json
import os
import queue
import random
import signal
import socket
import threading
from pathlib import Path

from face_framing import fit_photo
from folders import playlist as folder_playlist
from folders import summaries as folder_summaries

__all__ = ["fit_photo", "main", "pan_after_drag", "request", "serve"]
from face_worker import ProcessWorker
from playback import Playback

BASE = Path(__file__).resolve().parent
SOCKET = (
    Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    / "photo-library.sock"
)


def pan_after_drag(image_size, viewport, zoom, pan, delta):
    """Normalized crop offsets; drag moves the photo with the pointer."""
    scale = min(viewport[0] / image_size[0], viewport[1] / image_size[1]) * zoom
    result = []
    for source, screen, offset, movement in zip(image_size, viewport, pan, delta):
        travel = max(0, (source * scale - screen) / 2)
        result.append(max(-1, min(1, offset - movement / travel)) if travel else 0)
    return tuple(result)


def request(message):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(10)
        s.connect(str(SOCKET))
        s.sendall((json.dumps(message) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = s.recv(65536)
            if not chunk:
                raise RuntimeError("Player disconnected")
            data += chunk
        result = json.loads(data.split(b"\n")[0])
        if "error" in result:
            raise RuntimeError(result["error"])
        return result


def serve(data):
    import tkinter as tk
    from tkinter import ttk

    os.environ.setdefault("DISPLAY", ":0")
    os.environ.setdefault("XAUTHORITY", str(Path.home() / ".Xauthority"))
    root = tk.Tk()
    root.title("Photo Years")
    root.configure(bg="#101418")
    root.withdraw()
    canvas = tk.Canvas(root, bg="black", highlightthickness=0)
    state = {
        "playing": False,
        "paused": False,
        "year": None,
        "seconds": 8,
        "shuffle": False,
        "index": 0,
        "count": 0,
        "path": None,
        "zoom": 1.0,
        "pan_x": 0.0,
        "pan_y": 0.0,
    }
    playlist = []
    current = None
    rendered = None
    incoming = queue.Queue()
    drag = None

    def catalog():
        return json.loads((data / "catalog.json").read_text())

    def years():
        return catalog()["summary"]["years"]

    def clear():
        for child in root.winfo_children():
            if child is not canvas:
                child.destroy()
        canvas.pack_forget()

    def stop():
        nonlocal current, rendered, drag
        drag = None
        playback.stop()
        state.update(playing=False, paused=False, path=None)
        current = rendered = None
        canvas.delete("all")
        root.withdraw()

    def paint():
        nonlocal rendered
        if current is None:
            return
        w, h = max(1, root.winfo_width()), max(1, root.winfo_height())
        fitted = current
        ppm = f"P6\n{fitted.width} {fitted.height}\n255\n".encode() + fitted.tobytes()
        rendered = tk.PhotoImage(data=ppm, format="PPM")
        canvas.delete("all")
        canvas.create_image(w // 2, h // 2, image=rendered)
        caption = f"{state['year']}   {state['index'] + 1} / {state['count']}" + (
            "   Paused" if state["paused"] else ""
        )
        if effective_zoom != 1:
            caption += f"   Zoom {effective_zoom:.0%}"
        canvas.create_rectangle(14, h - 46, 270, h - 12, fill="#101418", outline="")
        canvas.create_text(
            25, h - 29, text=caption, fill="white", anchor="w", font=("Sans", 14)
        )

    effective_zoom = 1.0
    image_size = (1, 1)

    def commit(index, row, result):
        nonlocal current, effective_zoom, image_size
        current = result["image"]
        image_size = result["size"]
        effective_zoom = result["zoom"]
        state.update(
            index=index,
            path=row["path"],
            pan_x=result["pan"][0],
            pan_y=result["pan"][1],
        )
        paint()

    playback = Playback(ProcessWorker(data), commit, failed=stop)

    def show(index):
        nonlocal drag
        drag = None
        playback.request(index)

    def start(message):
        nonlocal playlist
        folder = message.get("folder")
        year = str(message.get("year") or "")
        seconds = float(message.get("seconds", 8))
        if not 1 <= seconds <= 3600:
            raise ValueError("seconds must be between 1 and 3600")
        if folder:
            year, selected = folder_playlist(data, folder)
        else:
            selected = [
                r
                for r in catalog()["items"]
                if r["status"] == "included"
                and r["kind"] == "photo"
                and r["year"] == year
            ]
        if not selected:
            raise ValueError(f"No approved photos for {year}")
        playlist = selected
        shuffle = bool(message.get("shuffle", False))
        if shuffle:
            random.shuffle(playlist)
        state.update(
            playing=True,
            paused=False,
            year=year,
            seconds=seconds,
            shuffle=shuffle,
            count=len(playlist),
            index=0,
        )
        clear()
        canvas.pack(fill="both", expand=True)
        root.deiconify()
        root.attributes("-fullscreen", True)
        root.lift()
        root.focus_force()
        root.update_idletasks()
        playback.resize((root.winfo_width(), root.winfo_height()))
        playback.start(playlist, seconds)

    def picker():
        stop()
        clear()
        root.attributes("-fullscreen", False)
        root.geometry("580x650")
        root.deiconify()
        panel = ttk.Frame(root, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Photos by year", font=("Sans", 24)).pack(pady=(0, 12))
        ttk.Label(
            panel, text="Select a year or saved folder to start a slideshow."
        ).pack()
        listing = tk.Listbox(panel, font=("Sans", 17), height=17, exportselection=False)
        listing.pack(fill="both", expand=True, pady=15)
        available = []
        for year, count in years().items():
            listing.insert("end", f"{year}     {count:,} photos")
            available.append({"year": year})
        for folder in folder_summaries(data):
            listing.insert(
                "end", f"{folder['name']}     {folder['count']:,} photos (folder)"
            )
            available.append({"folder": folder["id"]})
        seconds = tk.StringVar(value="8")
        shuffled = tk.BooleanVar(value=False)
        controls = ttk.Frame(panel)
        controls.pack(fill="x")
        ttk.Label(controls, text="Seconds per photo").pack(side="left")
        ttk.Spinbox(controls, from_=1, to=3600, textvariable=seconds, width=6).pack(
            side="left", padx=8
        )
        ttk.Checkbutton(controls, text="Shuffle", variable=shuffled).pack(side="right")

        def selected(event=None):
            if listing.curselection():
                start(
                    {
                        **available[listing.curselection()[0]],
                        "seconds": float(seconds.get()),
                        "shuffle": shuffled.get(),
                    }
                )

        ttk.Button(panel, text="Start slideshow", command=selected).pack(
            fill="x", pady=12
        )
        listing.bind("<Double-Button-1>", selected)
        ttk.Label(panel, text="Space: pause   ← →: previous / next   Esc: exit").pack()

    def execute(message):
        action = message.get("command")
        if action == "start":
            start(message)
        elif action == "stop":
            stop()
        elif action == "picker":
            picker()
        elif action == "years":
            return years()
        elif action in {"pause", "resume", "toggle"}:
            if state["playing"]:
                state["paused"] = (
                    not state["paused"] if action == "toggle" else action == "pause"
                )
                playback.pause(state["paused"])
                paint()
        elif action in {"next", "previous"}:
            if state["playing"]:
                show(playback.index + (1 if action == "next" else -1))
        elif action in {"pan_left", "pan_right", "pan_up", "pan_down"}:
            if not state["playing"] or current is None:
                raise ValueError("Start a slideshow before panning.")
            if effective_zoom <= 1:
                raise ValueError("Zoom in before panning.")
            w, h = max(1, root.winfo_width()), max(1, root.winfo_height())
            dx, dy = {
                "pan_left": (-w * 0.1, 0),
                "pan_right": (w * 0.1, 0),
                "pan_up": (0, -h * 0.1),
                "pan_down": (0, h * 0.1),
            }[action]
            state["pan_x"], state["pan_y"] = pan_after_drag(
                image_size,
                (w, h),
                effective_zoom,
                (state["pan_x"], state["pan_y"]),
                (dx, dy),
            )
            state["zoom"] = effective_zoom
            playback.manual_frame(state["zoom"], (state["pan_x"], state["pan_y"]))
            return dict(
                state, message="Photo moved. Use Reset Zoom to fit and center it."
            )
        elif action in {"zoom_in", "zoom_out", "zoom_reset"}:
            if not state["playing"]:
                raise ValueError("Start a slideshow before zooming.")
            state["zoom"] = (
                1.0
                if action == "zoom_reset"
                else max(
                    0.5,
                    min(4.0, state["zoom"] * (1.25 if action == "zoom_in" else 0.8)),
                )
            )
            if action == "zoom_reset" or state["zoom"] <= 1:
                state.update(pan_x=0.0, pan_y=0.0)
            playback.manual_frame(state["zoom"], (state["pan_x"], state["pan_y"]))
            return dict(state, message=f"Zoom: {state['zoom']:.0%}")
        elif action != "status":
            raise ValueError("Unknown player command")
        return dict(state)

    def tick():
        try:
            while True:
                message, result = incoming.get_nowait()
                try:
                    result.put(execute(message))
                except Exception as e:  # noqa: BLE001 - keep the control server alive
                    result.put({"error": str(e)})
        except queue.Empty:
            pass
        playback.tick(advance=drag is None)
        root.after(100, tick)

    def listen():
        with socket.socket(socket.AF_UNIX) as server:
            if SOCKET.exists():
                try:
                    request({"command": "status"})
                except (OSError, RuntimeError):
                    SOCKET.unlink()
                else:
                    os._exit(1)
            server.bind(str(SOCKET))
            os.chmod(SOCKET, 0o600)
            server.listen(8)
            while True:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(10)
                    try:
                        raw = b""
                        while b"\n" not in raw and len(raw) < 65536:
                            part = conn.recv(4096)
                            if not part:
                                break
                            raw += part
                        message = json.loads(raw.split(b"\n")[0])
                        response = queue.Queue()
                        incoming.put((message, response))
                        conn.sendall(
                            (json.dumps(response.get(timeout=8)) + "\n").encode()
                        )
                    except Exception as e:  # noqa: BLE001 - keep the control server alive
                        try:
                            conn.sendall(
                                (json.dumps({"error": str(e)}) + "\n").encode()
                            )
                        except OSError:
                            pass

    def begin_drag(event):
        nonlocal drag
        if state["playing"] and current is not None and effective_zoom > 1:
            drag = (event.x, event.y)
            canvas.configure(cursor="fleur")

    def move_drag(event):
        nonlocal drag
        if drag is None or current is None:
            return
        viewport = (max(1, root.winfo_width()), max(1, root.winfo_height()))
        state["pan_x"], state["pan_y"] = pan_after_drag(
            image_size,
            viewport,
            effective_zoom,
            (state["pan_x"], state["pan_y"]),
            (event.x - drag[0], event.y - drag[1]),
        )
        drag = (event.x, event.y)
        state["zoom"] = effective_zoom
        playback.manual_frame(state["zoom"], (state["pan_x"], state["pan_y"]))

    def end_drag(event):
        nonlocal drag
        if drag is not None:
            move_drag(event)
            drag = None
            playback.pause(state["paused"])
        canvas.configure(cursor="")

    # X11 exposes single-finger touchscreen drags as primary pointer events.
    canvas.bind("<ButtonPress-1>", begin_drag)
    canvas.bind("<B1-Motion>", move_drag)
    canvas.bind("<ButtonRelease-1>", end_drag)
    root.bind("<space>", lambda e: execute({"command": "toggle"}))
    root.bind("<Right>", lambda e: execute({"command": "next"}))
    root.bind("<Left>", lambda e: execute({"command": "previous"}))
    root.bind("<Escape>", lambda e: stop())
    root.bind(
        "<plus>",
        lambda e: execute({"command": "zoom_in"}) if state["playing"] else None,
    )
    root.bind(
        "<equal>",
        lambda e: execute({"command": "zoom_in"}) if state["playing"] else None,
    )
    root.bind(
        "<minus>",
        lambda e: execute({"command": "zoom_out"}) if state["playing"] else None,
    )
    root.bind(
        "<Key-0>",
        lambda e: execute({"command": "zoom_reset"}) if state["playing"] else None,
    )
    root.bind(
        "<Configure>",
        lambda e: playback.resize((e.width, e.height)) if e.widget is root else None,
    )
    root.protocol("WM_DELETE_WINDOW", stop)
    signal.signal(signal.SIGTERM, lambda *_: root.quit())
    signal.signal(signal.SIGINT, lambda *_: root.quit())
    threading.Thread(target=listen, daemon=True).start()
    try:
        tick()
        root.mainloop()
    finally:
        playback.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=[
            "serve",
            "picker",
            "start",
            "stop",
            "pause",
            "resume",
            "next",
            "previous",
            "status",
            "years",
        ],
    )
    p.add_argument("year", nargs="?")
    p.add_argument("--seconds", type=float, default=8)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument("--data", type=Path, default=BASE / "data")
    args = p.parse_args()
    try:
        if args.command == "serve":
            serve(args.data)
        else:
            if args.command == "start" and (
                not args.year
                or not args.year.isdigit()
                or not 1900 <= int(args.year) <= 2100
            ):
                p.error("start requires a valid year, for example: photos start 2010")
            print(
                json.dumps(
                    request(
                        {
                            "command": args.command,
                            "year": args.year,
                            "seconds": args.seconds,
                            "shuffle": args.shuffle,
                        }
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
    except (OSError, RuntimeError) as e:
        p.exit(
            1,
            f"{e}\nStart the service with: systemctl --user start photo-library-player\n",
        )


if __name__ == "__main__":
    main()

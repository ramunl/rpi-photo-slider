"""Isolated integration check on the Pi; never touches its saved albums."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image

base = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='photo-folder-check-') as tmp:
    root = Path(tmp)
    data = root / 'data'; data.mkdir()
    folder = root / 'Test photos with spaces'; folder.mkdir()
    for name, color in [('one.jpg', 'red'), ('two.png', 'blue')]:
        Image.new('RGB', (320, 200), color).save(folder / name)
    (data / 'catalog.json').write_text(json.dumps({'items': [{
        'path': str(folder / 'one.jpg'), 'status': 'included', 'kind': 'photo', 'year': '2010'}]}))
    env = dict(os.environ, XDG_RUNTIME_DIR=tmp, DISPLAY=':0',
               XAUTHORITY=str(Path.home() / '.Xauthority'), TEST_FOLDER=str(folder), TEST_DATA=str(data))
    processes = []
    def api(path, body=None, origin=True):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = 'http://127.0.0.1:8788'
        request = Request('http://127.0.0.1:8788' + path,
                          data=None if body is None else json.dumps(body).encode(), headers=headers)
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    try:
        # Exercise real Tk pointer bindings without injecting input into other apps.
        player_code = '''
import os, tkinter as tk
from pathlib import Path
import player
original_tk = tk.Tk
runtime = Path(os.environ['XDG_RUNTIME_DIR'])
def make_root():
    root = original_tk()
    def inject():
        marker = runtime / 'drag-request'
        if marker.exists():
            canvas = next(c for c in root.winfo_children() if isinstance(c, tk.Canvas))
            canvas.event_generate('<ButtonPress-1>', x=200, y=200)
            canvas.event_generate('<B1-Motion>', x=260, y=230)
            canvas.event_generate('<ButtonRelease-1>', x=260, y=230)
            marker.unlink()
            (runtime / 'drag-done').touch()
        root.after(30, inject)
    root.after(30, inject)
    return root
tk.Tk = make_root
player.serve(Path(os.environ['TEST_DATA']))
'''
        processes.append(subprocess.Popen([sys.executable, '-c', player_code], cwd=base, env=env))
        code = "import os,review; from pathlib import Path; review.choose_folder=lambda:os.environ['TEST_FOLDER']; review.serve(Path(os.environ['TEST_DATA']),8788,'/unused')"
        processes.append(subprocess.Popen([sys.executable, '-c', code], cwd=base, env=env))
        for _ in range(50):
            try:
                api('/api/folders')
                if (root / 'photo-library.sock').exists():
                    break
            except URLError:
                pass
            time.sleep(.1)
        result = api('/api/folders/add', {})
        key = result['album']['id']
        assert result['album']['count'] == 2, result
        assert api('/api/folders')[0]['name'] == folder.name
        result = api('/api/player', {'command': 'start', 'folder': key, 'seconds': 30})
        assert result['playing'] and result['count'] == 2 and result['year'] == folder.name, result
        assert api('/api/player', {'command': 'pause'})['paused']
        zoomed = api('/api/player', {'command': 'zoom_in'})
        assert zoomed['zoom'] == 1.25 and zoomed['paused']
        (root / 'drag-request').touch()
        deadline = time.monotonic() + 5
        while not (root / 'drag-done').exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert (root / 'drag-done').exists(), 'Tk drag events did not run'
        panned = api('/api/player', {'command': 'status'})
        assert panned['pan_x'] < 0 and panned['pan_y'] < 0, panned
        assert api('/api/player', {'command': 'zoom_out'})['zoom'] == 1.0
        centered = api('/api/player', {'command': 'status'})
        assert centered['pan_x'] == centered['pan_y'] == 0
        api('/api/player', {'command': 'zoom_in'})
        assert api('/api/player', {'command': 'pan_left'})['pan_x'] > 0
        api('/api/player', {'command': 'pan_right'})
        assert api('/api/player', {'command': 'pan_right'})['pan_x'] < 0
        assert api('/api/player', {'command': 'pan_up'})['pan_y'] > 0
        api('/api/player', {'command': 'pan_down'})
        assert api('/api/player', {'command': 'pan_down'})['pan_y'] < 0
        assert api('/api/player', {'command': 'zoom_reset'})['zoom'] == 1.0
        for _ in range(9):
            assert api('/api/player', {'command': 'zoom_in'})['zoom'] <= 4.0
        assert api('/api/player', {'command': 'zoom_reset'})['zoom'] == 1.0
        api('/api/player', {'command': 'zoom_in'})
        next_photo = api('/api/player', {'command': 'next'})
        assert next_photo['index'] == 1 and next_photo['zoom'] == 1.25
        assert next_photo['pan_x'] == next_photo['pan_y'] == 0
        previous_photo = api('/api/player', {'command': 'previous'})
        assert previous_photo['index'] == 0 and previous_photo['zoom'] == 1.25
        assert not api('/api/player', {'command': 'resume'})['paused']
        assert not api('/api/player', {'command': 'stop'})['playing']
        result = api('/api/player', {'command': 'start', 'year': '2010'})
        assert result['playing'] and result['count'] == 1, result
        assert result['zoom'] == 1.25
        api('/api/player', {'command': 'start', 'folder': key, 'seconds': 1})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = api('/api/player', {'command': 'status'})
            if result['index'] == 1:
                break
            time.sleep(.1)
        assert result['index'] == 1 and result['zoom'] == 1.25, result
        assert api('/api/player', {'command': 'zoom_reset'})['zoom'] == 1.0
        api('/api/player', {'command': 'stop'})
        try:
            api('/api/folders/add', {}, origin=False)
            raise AssertionError('Missing origin was accepted')
        except HTTPError as error:
            assert error.code == 403
        print('PASS: add/list folder, folder playback, pause/next/resume/stop, year playback, origin protection', flush=True)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

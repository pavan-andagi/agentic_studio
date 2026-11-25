"""Live web dashboard for the studio (standard library only).

Started automatically by `python -m studio.main` (config: dashboard:), or
on its own to browse any project:

    python -m studio.dashboard [--root projects] [--port 8765] [--demo]

It shows the agent flow with live status, each stage's output, the Master
agent's decisions, the feedback log, a timeline and the console, and it can
answer the approval gates from the browser.
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from email.utils import formatdate, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import gpumon

WEB = Path(__file__).parent / "web"
STAGES = ["trend", "plan", "script", "cast", "images", "clips",
          "finish", "render", "audio"]
IMG = {".png", ".jpg", ".jpeg", ".webp"}
VID = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
VID = {".mp4", ".webm", ".mov"}
# A project name becomes a directory name and a command-line argument,
# so it is the one piece of user input here that must be boring.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_UPLOAD = 64 * 1024 * 1024
RUNS = {}                      # project name -> Popen, while it lives

FILES = {"trends": "1_trends.json", "chosen": "1_trend_chosen.json",
         "plan": "2_plan.json", "script": "3_script.json",
         "edit_notes": "5_edit_notes.json", "audio": "6_audio.json"}


def read_styles(config):
    """Style names for the form. Read on demand so editing config.yaml
    does not need the server restarted."""
    try:
        import yaml
        cfg = yaml.safe_load(Path(config).read_text())
        return sorted(k for k in (cfg.get("styles") or {})
                      if k != "default")
    except Exception:                                    # noqa: BLE001
        return []


def _save_uploads(stage, files, allow=None):
    """[{name, data}] with base64 data -> saved paths, in order.

    Names are taken apart rather than trusted: an uploaded file called
    ../../etc/passwd is a file called passwd."""
    allow = allow or IMG
    stage.mkdir(parents=True, exist_ok=True)
    out = []
    for i, f in enumerate(files, 1):
        raw = base64.b64decode(str(f.get("data", "")).split(",")[-1])
        if not raw:
            continue
        stem = Path(str(f.get("name") or f"img{i}")).name
        if Path(stem).suffix.lower() not in allow:
            stem += ".png" if allow is IMG else ".mp4"
        dest = stage / stem
        dest.write_bytes(raw)
        out.append(dest)
    return out


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def tail(path, n, window=200_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - window))
            data = f.read().decode("utf-8", "replace")
        return data.splitlines()[-n:]
    except OSError:
        return []


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def list_projects(root):
    root = Path(root)
    items = sorted(((d.stat().st_mtime, d.name) for d in root.glob("*")
                    if (d / "state.json").exists()), reverse=True)
    return [n for _, n in items]


def project_data(root, name):
    d = (Path(root) / name).resolve()
    if not (d / "state.json").exists():
        return None
    st = read_json(d / "state.json", {})

    def rel(p):
        try:
            return str(Path(p).resolve().relative_to(d)).replace("\\", "/")
        except ValueError:
            return None

    events = []
    for line in tail(d / "events.jsonl", 2000, 3_000_000):
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    pend = read_json(d / "pending.json")
    run_pid = (st.get("run") or {}).get("pid")
    is_alive = alive(run_pid)
    if pend and not is_alive:
        pend = None                       # stale: the studio is not running
    if pend and pend.get("artifact"):
        pend["artifact"] = rel(Path(pend["artifact"]))

    def images_in(folder):
        return sorted(rel(f) for f in (d / folder).glob("*")
                      if f.suffix.lower() in IMG) if (d / folder).exists() else []

    candidates = {}
    if (d / "candidates").exists():
        for sub in sorted((d / "candidates").iterdir()):
            if sub.is_dir():
                candidates[sub.name] = images_in(f"candidates/{sub.name}")
    takes = {}
    if (d / "clips" / "takes").exists():
        for f in sorted((d / "clips" / "takes").glob("*"),
                        key=lambda x: x.stat().st_mtime):
            m = re.search(r"_(s\d+)_", f.name)
            if m and f.suffix.lower() in VID:
                takes.setdefault(m.group(1), []).append(rel(f))
    finished = {}
    for sid, v in (st.get("finished") or {}).items():
        r = rel(v)
        if r and (d / r).exists():
            finished[sid] = r
    composed = {}
    for sid, v in (st.get("composed") or {}).items():
        r = rel(v)
        if r and (d / r).exists():
            composed[sid] = r
    clips = {}
    for sid, v in (st.get("clips") or {}).items():
        r = rel(v) or (f"clips/{Path(v).name}"
                       if (d / "clips" / Path(v).name).exists() else None)
        if r and (d / r).exists():
            clips[sid] = r
    keyframes = {}
    for k, v in (st.get("keyframes") or {}).items():
        r = rel(v)
        if r and (d / r).exists():
            keyframes[k] = r
    data = {
        "name": name, "state": {k: st.get(k) for k in (
            "approved", "inputs", "current", "feedback_log", "guidance",
            "entry", "stop_after", "settings")},
        "run": {"alive": is_alive, "pid": run_pid,
                "started": (st.get("run") or {}).get("started")},
        "files": {k: read_json(d / f) for k, f in FILES.items()},
        "masters": {s: read_json(d / f"master_{s}.json") for s in STAGES},
        "events": events, "pending": pend,
        "console": tail(d / "console.log", 200),
        "candidates": candidates, "keyframes": keyframes, "takes": takes,
        "clips": clips, "finished": finished, "composed": composed,
        "cost": gpumon.report(d),
        "sheets": images_in("sheets"),
        "cast": {n: rel(Path(f)) for n, f in (st.get("cast") or {}).items()
                 if f and Path(f).exists()},
        "final": sorted(rel(f) for f in (d / "final").glob("*")
                        if f.suffix.lower() in VID) if (d / "final").exists() else [],
        "previews": sorted(rel(f) for f in (d / "audio").glob("preview_*")
                           if f.suffix.lower() in VID)
        if (d / "audio").exists() else [],
        "now": time.time()}
    return data


class Handler(BaseHTTPRequestHandler):
    root = Path("projects")
    token = None
    default = None
    cwd = Path(".")          # where a spawned run is started from
    config = "config.yaml"

    def log_message(self, *a):
        pass

    # ---------- helpers ----------
    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, query):
        return (not self.token) or query.get("key", [""])[0] == self.token

    def _file(self, path):
        st = path.stat()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size, start, end, code = st.st_size, 0, st.st_size - 1, 200
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            a, b = m.groups()
            if a == "":
                start = max(0, size - int(b))
            else:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            if start > end or start >= size:
                return self._send(416, b"", extra={
                    "Content-Range": f"bytes */{size}"})
            code = 206
        else:
            ims = self.headers.get("If-Modified-Since")
            if ims:
                try:
                    if parsedate_to_datetime(ims).timestamp() >= int(st.st_mtime):
                        return self._send(304, b"")
                except (TypeError, ValueError):
                    pass
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
        # Candidates and takes carry a sequence number in the filename, so a
        # given name never changes content: let the browser keep them. Every
        # redraw of the option board recreates its <image> elements, and
        # without this each one has to revalidate before it can paint, which
        # is the black flash you see when picking an option. Keyframes,
        # sheets and the final video are rewritten in place, so they stay on
        # no-cache.
        pstr = path.as_posix()
        immutable = "/candidates/" in pstr or "/clips/takes/" in pstr
        self.send_header("Cache-Control",
                         "public, max-age=86400" if immutable else "no-cache")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------- routes ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authorized(q):
            return self._send(401, "Add ?key=YOUR_TOKEN to the address.",
                              "text/plain")
        if u.path in ("/", "/index.html"):
            return self._send(200, (WEB / "index.html").read_bytes(),
                              "text/html; charset=utf-8")
        if u.path == "/api/styles":
            # The form shows the installed defaults as its starting
            # point, so "720p, 5s" is a visible choice rather than a
            # number buried in config.yaml.
            d = {}
            try:
                import yaml
                d = (yaml.safe_load(Path(self.config).read_text())
                     .get("defaults") or {})
            except Exception:                                # noqa: BLE001
                pass
            return self._send(200, {
                "styles": read_styles(self.config),
                "defaults": {"video_size": d.get("video_size", [704, 1280]),
                             "image_size": d.get("image_size",
                                                  [1056, 1920]),
                             "fps": d.get("fps", 24),
                             "max_clip_seconds": d.get("max_clip_seconds", 5)}})
        if u.path == "/api/voice":
            return self._send(200, voice_caps())
        if u.path == "/api/projects":
            names = list_projects(self.root)
            default = self.default if self.default in names else (names[0] if names else None)
            return self._send(200, {"projects": names, "default": default})
        if u.path == "/api/project":
            data = project_data(self.root, q.get("name", [""])[0])
            return (self._send(200, data) if data
                    else self._send(404, {"error": "unknown project"}))
        if u.path.startswith("/files/"):
            parts = unquote(u.path[len("/files/"):]).split("/", 1)
            if len(parts) == 2:
                base = (self.root / parts[0]).resolve()
                target = (base / parts[1]).resolve()
                if target.is_file() and target.is_relative_to(base):
                    return self._file(target)
            return self._send(404, {"error": "not found"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._authorized(parse_qs(u.query)):
            return self._send(401, {"error": "bad key"})
        if u.path == "/api/new":
            return self._new_run()
        if u.path == "/api/transcribe":
            return self._transcribe()
        if u.path == "/api/speak":
            return self._speak()
        if u.path != "/api/answer":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(n, 200_000)) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        d = (self.root / str(body.get("project", ""))).resolve()
        pend = read_json(d / "pending.json")
        if not pend or pend.get("id") != body.get("id"):
            return self._send(409, {"error": "that question is no longer open"})
        tmp = d / "response.json.tmp"
        tmp.write_text(json.dumps({"id": pend["id"],
                                   "text": str(body.get("text", ""))}))
        os.replace(tmp, d / "response.json")
        return self._send(200, {"ok": True})


    # ---------- starting a run ----------
    def _new_run(self):
        """Create a project and launch `studio.main new` for it.

        The run is a child process, not a thread: it has to be able to
        die, be killed, or finish without taking the dashboard with it.
        That is also the shape the pipeline needs later, when a run has
        to release its GPU at a gate and resume elsewhere."""
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > MAX_UPLOAD:
                return self._send(413, {"error": "too much data"})
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})

        name = str(body.get("name", "")).strip()
        if not SAFE_NAME.match(name):
            return self._send(400, {"error": "the name may use letters, "
                                    "digits, - and _ only"})
        d = (self.root / name).resolve()
        if not d.is_relative_to(self.root.resolve()):
            return self._send(400, {"error": "bad name"})
        if d.exists():
            return self._send(409, {"error": f"{name} already exists"})
        if any(p.poll() is None for p in RUNS.values()):
            busy = [k for k, p in RUNS.items() if p.poll() is None]
            return self._send(409, {"error": f"{busy[0]} is still running; "
                                    f"one run at a time on this pod"})

        brief = str(body.get("brief", "")).strip()
        style = str(body.get("style", "")).strip()
        mode = str(body.get("mode", "topic")).strip()
        describe = str(body.get("describe", "")).strip()
        until = str(body.get("until", "")).strip()
        motion = str(body.get("motion", "")).strip()
        size = str(body.get("size", "")).strip()
        image_size = str(body.get("image_size", "")).strip()
        seconds = body.get("seconds")
        fps = body.get("fps")
        point = body.get("point")
        if point is not None:
            try:
                point = [float(point[0]), float(point[1])]
            except (TypeError, ValueError, IndexError):
                return self._send(400, {"error": "point must be two numbers"})
        if until and until not in STAGES:
            return self._send(400, {"error": f"no stage called {until}"})
        if mode == "topic" and not brief:
            return self._send(400, {"error": "a topic needs a brief"})
        if mode in ("swap", "swapvid") and not brief:
            return self._send(400, {"error": "say who is being replaced"})
        if mode == "describe":
            if not describe:
                return self._send(400, {"error": "describe the picture"})
            # Only a run that goes on to animate the picture needs to be
            # told what moves. Pictures only, and there is no motion.
            if not brief and until != "images":
                return self._send(400, {"error": "say what should move"})

        # The project directory has to exist before anything is written
        # into it - including spawn.log, which is written even when there
        # is nothing to upload. Only the modes that save a file used to
        # create it, so topic and describe died here with ENOENT.
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return self._send(500, {"error": f"could not create {name}: {e}"})

        # Uploaded files land in the project before it starts, so the
        # paths handed to studio.main are ones it can read.
        stage = d / "supplied"
        try:
            cmd = [sys.executable, "-m", "studio.main", "new", name,
                   "--no-dashboard"]
            if brief:
                cmd += ["--brief", brief]
            if style:
                cmd += ["--style", style]
            if size:
                cmd += ["--size", size]
            if image_size:
                cmd += ["--image-size", image_size]
            if seconds:
                cmd += ["--seconds", str(int(seconds))]
            if fps:
                cmd += ["--fps", str(int(fps))]
            # Nothing to grade and nothing to draw: Finish and Overlays
            # would run, change nothing, and still ask for approval.
            if not style and until in ("audio", "render", ""):
                cmd += ["--skip", "finish,render"]
            if until and until != STAGES[-1]:
                cmd += ["--until", until]
            if mode == "describe":
                cmd += ["--describe", describe]
            if mode in ("script", "frames"):
                script = body.get("script")
                if not script:
                    return self._send(400, {"error": "no script supplied"})
                stage.mkdir(parents=True, exist_ok=True)
                f = stage / "shots.json"
                f.write_text(str(script), encoding="utf-8")
                cmd += ["--script", str(f)]
            if mode == "swapvid":
                clip = _save_uploads(stage, body.get("files") or [], VID)
                if not clip:
                    return self._send(400, {"error": "upload a video"})
                refs = _save_uploads(d / "refs", body.get("refs") or [])
                if not refs:
                    return self._send(
                        400, {"error": "add a picture of the character "
                                       "that replaces it"})
                cmd += ["--video", str(clip[0]), "--refs", str(d / "refs")]
                if motion:
                    cmd += ["--motion", motion]
                if point:
                    cmd += ["--point", f"{point[0]},{point[1]}"]
            if mode in ("frames", "image", "swap"):
                saved = _save_uploads(stage, body.get("files") or [])
                if not saved:
                    return self._send(400, {"error": "no pictures supplied"})
                if mode == "swap":
                    refs = _save_uploads(d / "refs", body.get("refs") or [])
                    if not refs:
                        return self._send(
                            400, {"error": "add a picture of the character "
                                           "that replaces it"})
                    cmd += ["--image", str(saved[0]),
                            "--refs", str(d / "refs")]
                elif mode == "image":
                    cmd += ["--image", str(saved[0])]
                else:
                    cmd += ["--images", str(stage)]
        except (OSError, ValueError) as e:
            shutil.rmtree(d, ignore_errors=True)
            return self._send(400, {"error": f"could not save the upload: {e}"})

        # The child's output goes to a file, not to DEVNULL. A run that
        # dies in its first second - a bad argument, a missing import -
        # otherwise vanishes and the page just sits there.
        log = d / "spawn.log"
        try:
            with open(log, "wb") as fh:
                fh.write(("$ " + " ".join(cmd) + "\n\n").encode())
            proc = subprocess.Popen(cmd, cwd=str(self.cwd),
                                    stdout=open(log, "ab"),
                                    stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
        except OSError as e:
            shutil.rmtree(d, ignore_errors=True)
            return self._send(500, {"error": f"could not start the run: {e}"})

        # Give it a moment to fall over, so the browser hears about it now
        # rather than showing an empty project for ever.
        time.sleep(1.5)
        if proc.poll() is not None:
            tail = ""
            try:
                tail = log.read_text(errors="replace")[-700:]
            except OSError:
                pass
            return self._send(500, {
                "error": f"the run exited immediately (code {proc.returncode})",
                "log": tail})
        RUNS[name] = proc
        return self._send(200, {"ok": True, "project": name, "pid": proc.pid})

    # ---------- voice ----------
    def _body(self, limit):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0 or n > limit:
            return None
        return self.rfile.read(n)

    def _transcribe(self):
        """Browser recording (webm/ogg/mp4) -> text, with Whisper on the pod."""
        from . import stt
        if not stt.available():
            return self._send(501, {"error": "faster-whisper not installed"})
        data = self._body(15_000_000)
        if not data:
            return self._send(400, {"error": "empty or too long recording"})
        ctype = self.headers.get("Content-Type", "")
        suffix = (".ogg" if "ogg" in ctype else ".mp4" if "mp4" in ctype
                  else ".wav" if "wav" in ctype else ".webm")
        try:
            return self._send(200, {"text": stt.transcribe(data, suffix)})
        except Exception as e:                      # noqa: BLE001
            return self._send(500, {"error": f"transcription failed: {e}"})

    def _speak(self):
        """Text -> WAV with Kokoro (the same TTS as the voice-over)."""
        from . import tts
        if not tts_available():
            return self._send(501, {"error": "kokoro not installed"})
        try:
            body = json.loads(self._body(50_000) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        text = re.sub(r"\s+", " ", str(body.get("text", ""))).strip()[:1500]
        if not text:
            return self._send(400, {"error": "no text"})
        voice = os.environ.get("STUDIO_DASH_VOICE", "af_heart")
        with SPEAK_LOCK:
            try:
                wav = tts.say(text, self.root / ".voice", voice, 1.05)
            except Exception as e:                  # noqa: BLE001
                return self._send(500, {"error": f"speech failed: {e}"})
        return self._send(200, wav.read_bytes(), "audio/wav")


SPEAK_LOCK = threading.Lock()


def tts_available():
    import importlib.util
    return (importlib.util.find_spec("kokoro") is not None
            and importlib.util.find_spec("soundfile") is not None)


def voice_caps():
    from . import stt
    return {"stt": stt.available(), "tts": tts_available()}


def start(root="projects", port=8765, token=None, default=None,
          host=None, config="config.yaml"):
    """Start the dashboard in a background thread; returns the server."""
    handler = type("H", (Handler,), {"root": Path(root), "token": token,
                                     "default": default,
                                     "cwd": Path.cwd(),
                                     "config": config})
    host = host or ("0.0.0.0" if token else "127.0.0.1")
    try:
        srv = ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        print(f"[dashboard] could not start on port {port}: {e}")
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    suffix = f"?key={token}" if token else ""
    print(f"[dashboard] http://localhost:{port}/{suffix}")
    pod = os.environ.get("RUNPOD_POD_ID")
    if pod and token:
        print(f"[dashboard] RunPod: https://{pod}-{port}.proxy.runpod.net/{suffix}")
    elif not token:
        print(f"[dashboard] from your laptop: ssh -p <PORT> root@<IP> "
              f"-L {port}:localhost:{port}   then open http://localhost:{port}/")
    return srv


def main():
    ap = argparse.ArgumentParser(description="Studio dashboard")
    ap.add_argument("--root", default="projects")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--demo", action="store_true",
                    help="create and show a sample project")
    a = ap.parse_args()
    default = None
    if a.demo:
        from .demo_project import make_demo
        default = make_demo(a.root)
    srv = start(a.root, a.port, os.environ.get("STUDIO_DASH_TOKEN") or None,
                default, config=a.config)
    print("[dashboard] standalone: you can start runs from the browser")
    if srv is None:
        return
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

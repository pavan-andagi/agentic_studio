"""What a run actually costs in GPU time.

A sampler thread writes one line per interval to the project's gpu.csv
while the studio runs, and `report()` turns that plus events.jsonl into
busy/idle seconds, a per-stage breakdown and a cost per second of
finished video. The dashboard shows it live; tools/gpu_report.py prints
the same thing afterwards.

Sampling beats timing the code. A human gate shows up as what it is -
the GPU sitting at 0% while somebody decides - and that idle time is the
whole argument for making a run able to release its GPU and resume
later. It is worth seeing on its own rather than buried in wall clock.
"""
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

BUSY = 10            # % utilisation above which the GPU counts as working
FRESH = 30           # a sample newer than this means the sampler is alive

QUERY = ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
         "--format=csv,noheader,nounits"]


def available():
    try:
        subprocess.run(QUERY, capture_output=True, check=True, timeout=10)
        return True
    except Exception:                                        # noqa: BLE001
        return False


def _sample():
    """(utilisation %, MiB used) for GPU 0, or None."""
    try:
        out = subprocess.run(QUERY, capture_output=True, text=True,
                             check=True, timeout=10).stdout.strip()
    except Exception:                                        # noqa: BLE001
        return None
    first = out.splitlines()[0] if out else ""
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def start(project_dir, every=5):
    """Append a sample every `every` seconds for the life of the process.

    Daemon thread: it dies with the run, which is what we want - a gap in
    the file is a run that was not going."""
    path = Path(project_dir) / "gpu.csv"

    def loop():
        while True:
            s = _sample()
            if s:
                try:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(f"{time.time():.0f},{s[0]:.0f},{s[1]:.0f}\n")
                except OSError:
                    pass
            time.sleep(every)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


# ----------------------------------------------------------------- report
def _read_samples(path):
    """[(epoch, util)] - accepts our own format and nvidia-smi's own CSV,
    so a file made with `nvidia-smi -l 5 > gpu.csv` still reads."""
    out = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        stamp, util = parts[0], parts[1].rstrip(" %")
        try:
            t = float(stamp)                      # our format: epoch
        except ValueError:
            try:                                  # nvidia-smi: a timestamp
                t = datetime.strptime(stamp[:19],
                                      "%Y/%m/%d %H:%M:%S").timestamp()
            except ValueError:
                continue
        try:
            out.append((t, float(util)))
        except ValueError:
            continue
    return sorted(out)


def _events(project_dir):
    f = Path(project_dir) / "events.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("time"):
            out.append((float(e["time"]), e.get("stage") or "?"))
    return sorted(out)


def _spans(events, end):
    """[(stage, start, stop)] - one span per contiguous run of a stage."""
    spans = []
    for i, (t, stage) in enumerate(events):
        stop = events[i + 1][0] if i + 1 < len(events) else end
        if spans and spans[-1][0] == stage:
            spans[-1][2] = stop
        else:
            spans.append([stage, t, stop])
    return [(s, a, b) for s, a, b in spans if b > a]


def _busy(samples, a, b):
    """GPU-busy seconds in [a, b). Each sample stands for the interval
    that follows it, so a gate shows up as idle rather than as nothing."""
    secs = 0.0
    for i in range(len(samples) - 1):
        t0, u0 = samples[i]
        t1 = samples[i + 1][0]
        if t1 <= a or t0 >= b or t1 - t0 > 120:   # a gap: the run was off
            continue
        if u0 > BUSY:
            secs += min(t1, b) - max(t0, a)
    return secs


def _video_seconds(project_dir):
    d = Path(project_dir)
    for name in (f"{d.name}_final.mp4", f"{d.name}.mp4"):
        f = d / "final" / name
        if f.exists():
            try:
                return float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries",
                     "format=duration", "-of", "default=nw=1:nk=1", str(f)],
                    capture_output=True, text=True, check=True,
                    timeout=20).stdout.strip())
            except Exception:                                # noqa: BLE001
                return None
    return None


def report(project_dir, rate=None):
    """Everything the Cost tab shows. None when there is nothing to show."""
    d = Path(project_dir)
    samples = _read_samples(d / "gpu.csv")
    if len(samples) < 2:
        return None
    if rate is None:
        st = {}
        try:
            st = json.loads((d / "state.json").read_text())
        except Exception:                                    # noqa: BLE001
            pass
        rate = float(((st.get("gpu") or {}).get("hourly_rate")) or 0)

    t0, t1 = samples[0][0], samples[-1][0]
    # Gaps longer than two minutes are time the studio was not running at
    # all, so they belong in neither column.
    gaps = sum(samples[i + 1][0] - samples[i][0]
               for i in range(len(samples) - 1)
               if samples[i + 1][0] - samples[i][0] > 120)
    wall = max(0.0, (t1 - t0) - gaps)
    busy = _busy(samples, t0, t1)

    stages = {}
    for stage, a, b in _spans(_events(d), t1):
        secs = _busy(samples, a, b)
        if secs >= 1:
            stages[stage] = stages.get(stage, 0.0) + secs

    vid = _video_seconds(d)
    return {
        "samples": len(samples),
        "live": (time.time() - t1) < FRESH,
        "wall": round(wall), "busy": round(busy),
        "idle": round(max(0.0, wall - busy)),
        "busy_pct": round(busy / wall * 100) if wall else 0,
        "stages": [{"stage": s, "seconds": round(v)}
                   for s, v in sorted(stages.items(), key=lambda x: -x[1])],
        "video_seconds": round(vid, 1) if vid else None,
        "per_video_second": round(busy / vid, 1) if vid else None,
        "rate": rate,
        "cost": round(busy / 3600 * rate, 2) if rate else None,
    }

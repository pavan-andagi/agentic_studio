#!/usr/bin/env python3
"""What a finished video actually costs in GPU time.

Run a project with a sampler going beside it:

    nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used \\
      --format=csv,noheader -l 5 > /workspace/gpu.csv &
    python -m studio.main new <name> --brief '...'
    # when the run finishes:  kill %1

then:

    python tools/gpu_report.py /workspace/gpu.csv projects/<name>

Sampling beats instrumenting the code because a human gate shows up as
what it is - the GPU sitting at 0% while someone decides. That idle time
is the whole argument for ROADMAP.md Phase 2, so it is worth seeing
separately rather than buried in a wall-clock figure.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BUSY = 10          # % utilisation above which the GPU counts as working


def read_samples(path):
    """[(epoch, util%)] from nvidia-smi --format=csv,noheader."""
    out = []
    for line in Path(path).read_text().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            t = datetime.strptime(parts[0][:19], "%Y/%m/%d %H:%M:%S")
            util = float(parts[1].rstrip(" %"))
        except ValueError:
            continue
        out.append((t.timestamp(), util))
    return sorted(out)


def read_events(project):
    f = Path(project) / "events.jsonl"
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


def stage_spans(events, end):
    """[(stage, start, stop)] - one span per contiguous run of a stage."""
    spans = []
    for i, (t, stage) in enumerate(events):
        stop = events[i + 1][0] if i + 1 < len(events) else end
        if spans and spans[-1][0] == stage:
            spans[-1][2] = stop
        else:
            spans.append([stage, t, stop])
    return [(s, a, b) for s, a, b in spans if b > a]


def busy_between(samples, a, b):
    """GPU-busy seconds in [a, b), by trapezoid over the sample interval."""
    secs = 0.0
    for i in range(len(samples) - 1):
        t0, u0 = samples[i]
        t1, _ = samples[i + 1]
        if t1 <= a or t0 >= b:
            continue
        span = min(t1, b) - max(t0, a)
        if u0 > BUSY:
            secs += span
    return secs


def video_seconds(project):
    for name in (f"{Path(project).name}_final.mp4", f"{Path(project).name}.mp4"):
        f = Path(project) / "final" / name
        if f.exists():
            try:
                return float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries",
                     "format=duration", "-of",
                     "default=nw=1:nk=1", str(f)],
                    capture_output=True, text=True, check=True).stdout.strip())
            except Exception:                                # noqa: BLE001
                pass
    return None


def hms(s):
    s = int(s)
    return f"{s // 3600}h {s % 3600 // 60:02d}m {s % 60:02d}s"


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    csv, project = sys.argv[1], sys.argv[2]
    samples = read_samples(csv)
    if len(samples) < 2:
        sys.exit(f"no usable samples in {csv}")
    events = read_events(project)
    t0, t1 = samples[0][0], samples[-1][0]
    wall = t1 - t0
    busy = busy_between(samples, t0, t1)

    print(f"\n  project      {project}")
    print(f"  samples      {len(samples)} over {hms(wall)}")
    print(f"  GPU busy     {hms(busy)}  ({busy / wall * 100:.0f}% of wall)")
    print(f"  GPU idle     {hms(wall - busy)}  "
          f"- gates, LLM calls, ffmpeg, you")

    if events:
        per = {}
        for stage, a, b in stage_spans(events, t1):
            per[stage] = per.get(stage, 0.0) + busy_between(samples, a, b)
        print("\n  GPU seconds by stage")
        for stage, secs in sorted(per.items(), key=lambda x: -x[1]):
            if secs < 1:
                continue
            print(f"    {stage:<10} {secs:8.0f}s  "
                  f"{'#' * int(secs / max(per.values()) * 36)}")
        acc = sum(per.values())
        if acc and abs(acc - busy) / busy > 0.15:
            print(f"    (stages account for {acc:.0f}s of {busy:.0f}s; the "
                  f"rest ran outside any event window)")

    vid = video_seconds(project)
    if vid:
        print(f"\n  finished video   {vid:.1f}s")
        print(f"  GPU seconds per second of video   {busy / vid:.1f}")
        print(f"  at $1.00/GPU-hour this video cost "
              f"${busy / 3600:.2f} of GPU time")
        print(f"  (put your real hourly rate in place of $1.00)")
    else:
        print("\n  no final video found - cost per second unavailable")
    print()


if __name__ == "__main__":
    main()

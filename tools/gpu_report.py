#!/usr/bin/env python3
"""Print what a finished run cost in GPU time.

    python tools/gpu_report.py projects/<name>

The studio samples the GPU itself while it runs (config: gpu:), so there
is nothing to start by hand - this just prints what the dashboard's Cost
tab is already showing, for a run that has finished.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import gpumon                                    # noqa: E402


def hms(s):
    s = int(s)
    return f"{s // 3600}h {s % 3600 // 60:02d}m {s % 60:02d}s"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = Path(sys.argv[1])
    if not d.exists():
        sys.exit(f"{d} does not exist")
    rate = float(sys.argv[2]) if len(sys.argv) > 2 else None
    c = gpumon.report(d, rate)
    if not c:
        sys.exit(f"no usable GPU samples in {d / 'gpu.csv'} - was gpu.enabled "
                 f"on, and does nvidia-smi work on this machine?")

    print(f"\n  project     {d}")
    print(f"  samples     {c['samples']}   sampler "
          f"{'running' if c['live'] else 'not running'}")
    print(f"  run length  {hms(c['wall'])}")
    print(f"  GPU busy    {hms(c['busy'])}  ({c['busy_pct']}%)")
    print(f"  GPU idle    {hms(c['idle'])}  - gates, LLM calls, ffmpeg, you")
    if c["stages"]:
        mx = c["stages"][0]["seconds"] or 1
        print("\n  GPU seconds by stage")
        for x in c["stages"]:
            bar = "#" * int(x["seconds"] / mx * 36)
            print(f"    {x['stage']:<10}{x['seconds']:7.0f}s  {bar}")
    if c["per_video_second"] is not None:
        print(f"\n  finished video                    {c['video_seconds']}s")
        print(f"  GPU seconds per second of video   {c['per_video_second']}")
    if c["cost"] is not None:
        print(f"  GPU cost at ${c['rate']}/hour            ${c['cost']:.2f}")
    elif c["per_video_second"] is not None:
        print("  (set gpu.hourly_rate in config.yaml, or pass it as the "
              "second argument, for a cost)")
    print()


if __name__ == "__main__":
    main()

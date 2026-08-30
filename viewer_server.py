#!/usr/bin/env python3
"""Unified Sphere-Tracker viewer server (live or offline, grid-only).

Renders a 2-hemisphere "world" map showing a graticule + horizon line and
per-person markers by spherical direction + angular size. No video images are
shown -- this is a pure detection map.

Modes:
  * --video  GS011468.360 [--gyro-csv ...] : live: decodes + detects in a
    background thread and serves the latest detections as they arrive.
  * --frames DIR --jsonl tracks.jsonl      : offline: replays a pre-generated
    JSONL (from cli_sphere_tracker.py) over a scrubbable timeline.

With gyro enabled, markers use the gravity-leveled WORLD frame
(world_bearing_deg / world_elev_deg) and the horizon is the true gravity
horizon. Without gyro, markers use the image frame and the horizon is the
equirect equator.

Run:
  # live
  python viewer_server.py --video GS011468.360 --gyro-csv motion_data.csv \

  # offline
  python viewer_server.py --frames /path/to/equirect --jsonl tracks.jsonl \
      --port 8124
then open http://localhost:PORT
"""
import argparse
import os
import threading

import numpy as np

from webui.cli_args import add_common_args
from webui.httpd import run
from webui.live import run_live_wrapped
from webui.server import ViewerServer


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", default=None,
                     help="Live: GoPro MAX .360 video to detect on the fly")
    src.add_argument("--frames", default=None,
                     help="Offline: folder of equirect frames to replay")
    ap.add_argument("--jsonl", default="tracks.jsonl",
                    help="Offline: JSONL tracks to replay (with --frames)")
    ap.add_argument("--gyro-csv", default=None,
                    help="Gyro CSV (live video mode; auto-extracted if missing)")
    add_common_args(ap)
    ap.add_argument("--port", type=int, default=8123)
    args = ap.parse_args()

    server = ViewerServer(fps=args.fps, port=args.port,
                          live=bool(args.video))

    # nominal world-frame view centers, kept in sync with SpherePersonTracker's
    # _world_grid (linspace(-180,180,N,endpoint=False) at --pitch, plus --nadir).
    server.fov = args.fov
    server.views = [
        {"az_deg": float(np.rad2deg(az)),
         "el_deg": float(args.pitch)}
        for az in np.linspace(-np.pi, np.pi, args.views, endpoint=False)
    ]
    if getattr(args, "nadir", 0.0) and args.nadir > 0.0:
        server.views.append({"az_deg": 0.0, "el_deg": -args.nadir,
                             "nadir": True})

    if args.video:
        t = threading.Thread(target=run_live_wrapped, args=(args, server),
                             daemon=True)
        t.start()
    else:
        if not os.path.exists(args.jsonl):
            print(f"warn: no {args.jsonl} - showing empty map")
        else:
            server.tracks.load_jsonl(args.jsonl)
            server.tracks.frames()
            print(f"Loaded {server.tracks.count} records from {args.jsonl}")

    run(server)


if __name__ == "__main__":
    main()

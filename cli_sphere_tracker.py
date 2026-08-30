#!/usr/bin/env python3
"""
Sphere Tracker CLI
==================
Detect + track people on 360 equirectangular video, storing each person per
frame as a unit 3D direction + angular size (never a 2D bbox).

Source:
  * a folder of equirectangular JPEG/PNG frames (--frames), or
  * a GoPro MAX .360 video, decoded on-the-fly with the custom ffmpeg that has
    the `gopromax_opencl` filter (--video).

Gyro (optional, but recommended):
  Pass --gyro-csv motion_data.csv (and --gyro-calib gyro_calib.json). With
  gyro, perspective views are gravity-leveled and every record gains
  world_dir_x/y/z, world_bearing_deg and world_elev_deg. If --video is given,
  the gyro CSV is auto-extracted from the GPMF stream when --gyro-csv is
  omitted.

Examples:
  python cli_sphere_tracker.py --video GS011468.360 --gyro-csv motion_data.csv \
      -o tracks.jsonl

  python cli_sphere_tracker.py --frames /path/to/360 \
      --gyro-csv motion_data.csv -o tracks.jsonl

  python cli_sphere_tracker.py --no-gyro --video GS011468.360 -o tracks.jsonl
"""
import argparse
import os
import sys
import time

import json

from tracking.pipeline import (
    EquirectVideoReader, FolderFrameReader, probe_video_fps,
    resolve_gyro_csv, build_tracker, attach_gyro,
    save_view_overlay, draw_equirect_overlay,
)


def add_common_args(ap):
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--frames", default=None,
                     help="Folder of equirectangular frames")
    src.add_argument("--video", default=None,
                     help="GoPro MAX .360 or equirect mp4 video")

    ap.add_argument("--model", default="yolo11n-seg.pt",
                    help="YOLO model (default yolo11n-seg.pt)")
    ap.add_argument("--conf", type=float, default=0.40,
                    help="Detection confidence (default 0.40)")
    ap.add_argument("--fov", type=float, default=90.0,
                    help="Detection view FOV (default 90)")
    ap.add_argument("--views", type=int, default=8,
                    help="Number of views around the horizon/world ring (default 8)")
    ap.add_argument("--pitch", type=float, default=0.0,
                    help="Detection pitch band in degrees (default 0 = horizon)")
    ap.add_argument("--view-size", type=int, default=640,
                    help="Detection view pixel size (default 640)")
    ap.add_argument("--track-match", type=float, default=12.0,
                    help="Track match angular threshold in deg (default 12)")
    ap.add_argument("--max-missed", type=int, default=15,
                    help="Frames a track may be missed before dropping (default 15)")
    ap.add_argument("--recheck", type=int, default=30,
                    help="Full re-detection sweep interval in frames (default 30)")
    ap.add_argument("--max-person-deg", type=float, default=90.0,
                    help="Max person angular width in deg (default 90; raise "
                         "from 30 to also keep the operator who sits right "
                         "below the camera and subtends a huge angle)")
    ap.add_argument("--min-person-deg", type=float, default=1.0,
                    help="Min person angular size in deg (default 1.0)")
    ap.add_argument("--min-aspect", type=float, default=1.0,
                    help="Min box height/width aspect (default 1.0)")
    ap.add_argument("--lat-min", type=float, default=-90.0,
                    help="Min elevation/latitude band in deg (default -90)")
    ap.add_argument("--lat-max", type=float, default=80.0,
                    help="Max elevation/latitude band in deg (default 80)")
    ap.add_argument("--edge-margin", type=float, default=0.06,
                    help="View edge margin fraction (default 0.06)")
    ap.add_argument("--nadir", type=float, default=0.0,
                    help="Add a dedicated straight-down view at this many "
                         "degrees below the horizon (default 0 = off). Use "
                         "e.g. --nadir 85 to also track the operator directly "
                         "below the camera.")

    ap.add_argument("--no-center", action="store_true",
                    help="Disable centred-view per-track detection")

    ap.add_argument("--gyro-csv", default=None,
                    help="Gyro/accel CSV (default: auto-extract from --video)")
    ap.add_argument("--gyro-calib", default="gyro_calib.json",
                    help="Calibration JSON with a_map/s_map/g_map/q0 "
                         "(default gyro_calib.json)")
    ap.add_argument("--no-gyro", action="store_true",
                    help="Disable gyro entirely (camera-frame output only)")
    ap.add_argument("--fps", type=float, default=30.0,
                    help="Frame rate for frame-folder timestamps (default 30)")
    ap.add_argument("--equirect-width", type=int, default=2048,
                    help="Equirect width used for video decode (default 2048)")

    ap.add_argument("--max-frames", type=int, default=None,
                    help="Only process the first N frames")
    ap.add_argument("--start-frame", type=int, default=0,
                    help="Skip the first N frames (fast keyframe seek for video;"
                         " slice for a frame folder) to avoid the camera "
                         "operator being too close at the start. Frame indexing"
                         " stays absolute so world-frame/gyro output remains "
                         "aligned with real recording time.")
    ap.add_argument("--fps-decode", type=int, default=0,
                    help="If >0, decode every Nth video frame (0 = all)")

    ap.add_argument("--save-overlays", default=None,
                    help="Folder to save perspective-view overlay PNGs for review")
    ap.add_argument("--overlay-views", type=int, default=2,
                    help="How many views to visualize in the overlay tile")
    ap.add_argument("--overlay-framerate", type=int, default=0,
                    help="Save an overlay every Nth frame (0 = every frame)")
    ap.add_argument("--save-equirect-overlay", default=None,
                    help="Folder to save annotated equirect PNGs with detection dots")


def main():
    ap = argparse.ArgumentParser(
        description="Track people in 360 video; output per-person spherical "
                    "direction + angular size per frame (optionally gyro-leveled)."
    )
    add_common_args(ap)
    ap.add_argument("-o", "--out", default="tracks.jsonl",
                    help="Output JSONL path (default tracks.jsonl)")
    args = ap.parse_args()

    gyro_csv = resolve_gyro_csv(args.video, args.gyro_csv, args.no_gyro)
    tracker = build_tracker(args)
    gyro = attach_gyro(tracker, gyro_csv, args.gyro_calib)
    if not tracker.detector.load():
        sys.exit("Failed to load YOLO model")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    out_fp = open(args.out, "w")
    if args.save_overlays:
        os.makedirs(args.save_overlays, exist_ok=True)
    if args.save_equirect_overlay:
        os.makedirs(args.save_equirect_overlay, exist_ok=True)

    print(f"Tracker started. model={args.model} views={args.views} "
          f"fov={args.fov} gyro={'on' if gyro else 'off'}")
    t_start = time.time()
    total_frames = 0
    total_records = 0

    try:
        if args.video:
            reader = EquirectVideoReader(args.video, args.equirect_width,
                                         start_frame=args.start_frame)
            reader.open()
            decode_counter = 0
            while True:
                got = reader.read()
                if got is None:
                    break
                fi, ts, frame = got
                if args.fps_decode and fi % args.fps_decode != 0:
                    continue
                total_frames += 1
                records = _process(tracker, frame, fi, ts, args, out_fp)
                total_records += len(records)
                if total_frames % 20 == 0:
                    print(f"  frame {fi} ts={ts:.2f}s tracks={len(tracker.tracks)} "
                          f"({total_frames/(time.time()-t_start):.2f} fps)", flush=True)
                if args.max_frames and total_frames >= args.max_frames:
                    break
            reader.close()
        else:
            for fi, ts, frame in FolderFrameReader(args.frames, args.max_frames,
                                                   fps=args.fps,
                                                   start=args.start_frame):
                total_frames += 1
                records = _process(tracker, frame, fi, ts, args, out_fp)
                total_records += len(records)
                if total_frames % 20 == 0:
                    print(f"  frame {fi} ts={ts:.2f}s tracks={len(tracker.tracks)} "
                          f"({total_frames/(time.time()-t_start):.2f} fps)", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        out_fp.close()

    dt = time.time() - t_start
    print(f"\nDone. {total_frames} frames, {total_records} person-detections "
          f"in {dt:.1f}s ({total_frames/dt:.2f} fps overall)")
    print(f"Output written to {args.out}")


def _process(tracker, frame, fi, ts, args, out_fp):
    records = tracker.process_frame(
        frame, frame_index=fi, timestamp=ts,
        enable_centering=True if not args.no_center else False,
    )
    for rec in records:
        out_fp.write(json.dumps(rec) + "\n")
    if args.save_overlays and (
        args.overlay_framerate == 0 or fi % args.overlay_framerate == 0
    ):
        save_view_overlay(tracker, frame, fi, args.save_overlays,
                          args.overlay_views)
    if args.save_equirect_overlay and records:
        annotated = draw_equirect_overlay(frame, records)
        cv2_imwrite = __import__("cv2").imwrite
        cv2_imwrite(os.path.join(args.save_equirect_overlay, f"frame_{fi:06d}.png"),
                    annotated)
    return records


if __name__ == "__main__":
    main()
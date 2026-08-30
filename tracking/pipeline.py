#!/usr/bin/env python3
"""Shared plumbing for the sphere-tracker batch CLI and the live viewer:
video/frame reading, gyro resolution, tracker construction, overlay helpers.
"""
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import numpy as np
import cv2

from tracking.sphere_tracker import (
    SpherePersonTracker, PersonDetector, extract_perspective,
)
from tracking.gyro_orientation import GyroOrientation, load_calib


# --------------------------------------------------------------------------- #
# Video decoding (custom ffmpeg with gopromax_opencl)
# --------------------------------------------------------------------------- #

def probe_video_fps(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20).stdout.strip()
        n, d = out.split("/")
        return float(n) / float(d) if float(d) else 30.0
    except Exception:
        return 30.0


class EquirectVideoReader:
    """Streaming reader that yields equirect frames from a .360 via ffmpeg."""

    def __init__(self, path: str, equirect_width: int = 5376,
                 start_frame: int = 0):
        self.path = path
        self.equirect_width = equirect_width
        self.fps = probe_video_fps(path)
        self.frame_index = start_frame
        self.start_frame = start_frame
        self.width = equirect_width
        self.height = equirect_width // 2
        self._proc = None
        self._frame_size = 0
        self._start_time = None

    def open(self):
        H = self.height
        W = self.width
        fw = f"scale={W}:{H}" if self.equirect_width else "null"
        vf = (
            "[0:v:0]format=nv12,hwupload[front];"
            "[0:v:1]format=nv12,hwupload[rear];"
            "[front][rear]gopromax_opencl[out];"
            "[out]hwdownload,format=nv12[vo];"
            f"[vo]{fw}[vo2]"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-init_hw_device", "opencl:0",
        ]
        # fast keyframe seek to skip the opening (e.g. operator too close to the
        # lens). Keeps absolute frame_index/timestamp so the world-frame (gyro)
        # output stays aligned with real recording time.
        if self.start_frame > 0:
            cmd += ["-ss", f"{self.start_frame / self.fps:.6f}"]
        cmd += ["-i", self.path,
                "-filter_complex", vf,
                "-map", "[vo2]",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
                ]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        self._frame_size = W * H * 3
        self._start_time = time.time()
        return self

    def read(self, timeout_s: float = 10.0):
        """Return (frame_index, timestamp_s, bgr_frame) or None at end.

        Uses a pipe read timeout: if ffmpeg produces nothing for `timeout_s`,
        treats the stream as stalled and returns None (end) instead of hanging.
        """
        raw = _read_stdout_exact(self._proc, self._frame_size, timeout_s)
        if raw is None:
            return None
        frame = np.frombuffer(raw, np.uint8).reshape(self.height, self.width, 3)
        ts = self.frame_index / self.fps
        result = (self.frame_index, ts, frame)
        self.frame_index += 1
        return result

    def close(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except Exception:
                pass


def _read_stdout_exact(proc, size, timeout_s):
    """Read exactly `size` bytes from proc.stdout with a timeout, else None."""
    import select
    buf = bytearray()
    deadline = time.time() + timeout_s
    while len(buf) < size:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        r, _, _ = select.select([proc.stdout], [], [], remaining)
        if not r:
            return None
        chunk = proc.stdout.read(min(size - len(buf), 65536))
        if not chunk:
            if proc.poll() is not None:
                return None
            # pipe closed with no more data
            return None
        buf.extend(chunk)
    return bytes(buf)


class FolderFrameReader:
    """Read equirect frames from a folder of JPEG/PNG files (0-based index)."""

    def __init__(self, folder: str, max_frames: Optional[int] = None,
                 step: int = 1, fps: float = 30.0, start: int = 0):
        folder = Path(folder)
        self.fps = fps
        self.start = start
        exts = (".jpg", ".jpeg", ".png", ".bmp")
        self.paths = sorted(
            p for p in folder.iterdir() if p.suffix.lower() in exts
        )
        if max_frames:
            self.paths = self.paths[start:start + max_frames]
        else:
            self.paths = self.paths[start:]
        self.paths = self.paths[::step]
        self.width = 0
        self.height = 0

    def __iter__(self):
        for i, p in enumerate(self.paths):
            img = cv2.imread(str(p))
            if img is None:
                continue
            self.height, self.width = img.shape[:2]
            yield (self.start + i, (self.start + i) / self.fps, img)


# --------------------------------------------------------------------------- #
# Gyro setup
# --------------------------------------------------------------------------- #

def resolve_gyro_csv(video_path: Optional[str], gyro_csv: Optional[str],
                     no_gyro: bool = False, cache_dir: str = "gyro_cache"):
    """Return a CSV path to use, or None. Auto-extracts GPMF when needed."""
    if no_gyro:
        return None
    if gyro_csv:
        if Path(gyro_csv).exists():
            return gyro_csv
        print(f"  warn: gyro csv '{gyro_csv}' not found - gyro disabled")
        return None
    if video_path:
        stem = Path(video_path).stem
        cache = Path(cache_dir)
        cache.mkdir(exist_ok=True)
        target = cache / f"{stem}.csv"
        if target.exists():
            return str(target)
        print(f"  extracting gyro/accel from {stem} ...")
        from tracking.gyro_extract import extract_motion_csv
        if extract_motion_csv(video_path, str(target)):
            print(f"  gyro csv -> {target}")
            return str(target)
        print("  warn: GPMF extraction failed - gyro disabled")
        return None
    return None


def make_gyro(gyro_csv: Optional[str], calib_json: Optional[str]) -> Optional[GyroOrientation]:
    if not gyro_csv:
        return None
    calib = load_calib(calib_json) if calib_json else None
    if calib is None:
        print(f"  warn: gyro calib '{calib_json}' not found - gyro disabled")
        return None
    try:
        g = GyroOrientation(gyro_csv, calib)
        print(f"  gyro loaded: {gyro_csv} "
              f"(duration {g.duration:.1f}s, {len(g.gyro)} samples)")
        return g
    except Exception as e:
        print(f"  warn: gyro init failed ({e}) - gyro disabled")
        return None


# --------------------------------------------------------------------------- #
# Tracker construction from argparse-style namespace
# --------------------------------------------------------------------------- #

def build_tracker(args) -> SpherePersonTracker:
    detector = PersonDetector(model_path=args.model, confidence=args.conf)
    return SpherePersonTracker(
        detector=detector,
        detect_fov=args.fov,
        detect_view_size=args.view_size,
        num_views=args.views,
        pitch_deg=args.pitch,
        track_match_deg=args.track_match,
        max_missed=args.max_missed,
        recheck_interval=args.recheck,
        max_person_deg=args.max_person_deg,
        min_person_deg=args.min_person_deg,
        min_aspect=args.min_aspect,
        lat_min_deg=args.lat_min,
        lat_max_deg=args.lat_max,
        edge_margin_frac=args.edge_margin,
        nadir_deg=getattr(args, "nadir", 0.0),
        gyro=None,
    )


def attach_gyro(tracker: SpherePersonTracker, gyro_csv, calib_json):
    tracker.gyro = make_gyro(gyro_csv, calib_json)
    return tracker.gyro


# --------------------------------------------------------------------------- #
# Overlay helpers (perspective + equirect PNG review)
# --------------------------------------------------------------------------- #

def draw_view_overlay(view, boxes, prm, label=""):
    out = view.copy()
    for box, conf in boxes:
        x1, y1, x2, y2 = [int(v) for v in box]
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        cv2.circle(out, (cx, cy), 4, (0, 0, 255), -1)
        if conf is not None:
            cv2.putText(out, f"{conf:.2f}", (x1, max(y1 - 5, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    if label:
        cv2.putText(out, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 255, 255), 2)
    return out


def save_view_overlay(tracker, frame, fi, out_dir, overlay_views=2):
    tiles = []
    for prm in tracker._detect_prms[:overlay_views]:
        view = extract_perspective(frame, prm)
        boxes = tracker.detector.detect(view)
        annotated = draw_view_overlay(
            view, [(b, c) for b, c in boxes], prm,
            label=f"yaw={prm['yaw']:+.0f} pitch={prm['pitch']:+.0f}",
        )
        tiles.append(annotated)
    tile = np.hstack(tiles) if tiles else frame
    cv2.imwrite(os.path.join(out_dir, f"frame_{fi:06d}.png"), tile)


def _wrap_x(ex, w, margin=0):
    xs = [int(ex)]
    if int(ex) + margin >= w:
        xs.append(int(ex) - w)
    elif int(ex) - margin < 0:
        xs.append(int(ex) + w)
    return xs


def draw_equirect_overlay(equirect, records):
    """Draw a cross/dot at each recorded person's direction on an equirect frame."""
    import random
    out = equirect.copy()
    h, w = out.shape[:2]
    rng = random.Random(0)
    pal = {}
    for rec in records:
        pid = rec.get("person_id")
        if pid not in pal:
            pal[pid] = (rng.randint(60, 255), rng.randint(60, 255), rng.randint(60, 255))
    for rec in records:
        pid = rec.get("person_id")
        d = np.array([rec["dir_x"], rec["dir_y"], rec["dir_z"]])
        lon = math.atan2(d[0], d[2])
        lat = math.asin(np.clip(d[1], -1, 1))
        ex = (lon / math.pi + 1.0) / 2.0 * w
        ey = (-lat / (math.pi / 2.0) + 1.0) / 2.0 * h
        ah_deg = rec["angular_height_deg"]
        r = max(3, int(ah_deg / 180.0 * h * 0.5))
        color = pal[pid]
        for cx in _wrap_x(ex, w, margin=int(r)):
            cv2.circle(out, (cx, int(ey)), r, color, 2)
            cv2.line(out, (cx - r, int(ey)), (cx + r, int(ey)), color, 1)
            cv2.line(out, (cx, int(ey) - r), (cx, int(ey) + r), color, 1)
            cv2.putText(out, f"#{pid}", (cx + r + 4, int(ey) - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return out
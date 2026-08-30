#!/usr/bin/env python3
"""Gyro orientation service.

Reuses the quaternion fusion / calibration logic from
fisheye_mask_generator/sun_from_gyro.py and exposes one convenient object:

    GyroOrientation(csv_path, calib)

with
    up_image(t)              gravity up as a unit vector in the equirect image frame
    R_world_image(t)         (3x3) image-frame unit dir -> world dir
    to_world(v_image, t)     convert an image-frame 3D direction to world
    bearing_elev(v_image, t) -> (azimuth_deg, elevation_deg)  0 = camera front at t=0
    view_pose_from_world(az, elev, t) -> (d_image, up_image)  for level perspective views

Coordinate conventions:
  * image frame : X right, Y up,  Z front  (same as sphere_tracker / equirect lon/lat)
  * world frame : Y = gravity up, Z = camera's initial horizontal heading (t=0),
                  X = right.  azimuth = atan2(wx, wz), elevation = asin(wy).

Calibration JSON (a_map, s_map, g_map, q0) comes from sun_from_gyro's sun_state.json.
"""
import csv
import math
from pathlib import Path
from typing import List, Tuple, Optional, Dict

import numpy as np


# --------------------------------------------------------------------------- #
# Quaternion helpers (ported from sun_from_gyro.py)
# --------------------------------------------------------------------------- #

def quat_mul(q1, q2):
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ])


def quat_conj(q):
    return np.array([q[0], -q[1], -q[2], -q[3]])


def quat_normalize(q):
    n = np.linalg.norm(q)
    return q / n if n > 0 else np.array([1.0, 0.0, 0.0, 0.0])


def quat_rotate(q, v):
    w, x, y, z = q
    vx, vy, vz = v
    uvx, uvy, uvz = y * vz - z * vy, z * vx - x * vz, x * vy - y * vx
    return np.array([
        vx + 2 * w * uvx + 2 * (y * uvz - z * uvy),
        vy + 2 * w * uvy + 2 * (z * uvx - x * uvz),
        vz + 2 * w * uvz + 2 * (x * uvy - y * uvx),
    ])


def axis_angle_q(axis, angle):
    n = np.linalg.norm(axis)
    if n < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0])
    a = axis / n
    h = angle / 2.0
    return np.array([math.cos(h), *(a * math.sin(h))])


def vec_to_q(v_from, v_to):
    cross = np.cross(v_from, v_to)
    dot = float(np.dot(v_from, v_to))
    return quat_normalize(axis_angle_q(cross, math.atan2(np.linalg.norm(cross), dot)))


def quat_to_mat(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def dir_from_lonlat(lon: float, lat: float) -> np.ndarray:
    cl, sl = math.cos(lon), math.sin(lon)
    cb, sb = math.cos(lat), math.sin(lat)
    return np.array([cb * sl, sb, cb * cl], dtype=np.float64)


# --------------------------------------------------------------------------- #
# Sensor CSV + integration (ported from sun_from_gyro.py)
# --------------------------------------------------------------------------- #

def load_gyro_csv(path) -> Tuple[List, List]:
    gyro, accel = [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            t = float(row["Time(s)"])
            v = np.array([float(row["X"]), float(row["Y"]), float(row["Z"])])
            (gyro if row["Type"] == "GYRO" else accel).append((t, v))
    return gyro, accel


def accel_up_at(accel, t, window_s=0.6) -> Optional[np.ndarray]:
    lo, hi = t - window_s / 2, t + window_s / 2
    vs = np.array([a[1] for a in accel if lo <= a[0] <= hi])
    if len(vs) < 5:
        return None
    m = vs.mean(axis=0)
    n = np.linalg.norm(m)
    return m / n if n > 1e-6 else None


def smoothed_accel_ups(accel, a_map, half_window_s=1.0):
    ts = np.array([a[0] for a in accel])
    vs = np.array([a[1] for a in accel])
    csum = np.vstack([np.zeros(3), np.cumsum(vs, axis=0)])
    ups = np.empty((len(ts), 3))
    lo = np.searchsorted(ts, ts - half_window_s)
    hi = np.searchsorted(ts, ts + half_window_s)
    for i in range(len(ts)):
        m = csum[hi[i]] - csum[lo[i]]
        n = np.linalg.norm(m)
        ups[i] = (a_map @ m / n) if n > 1e-6 else np.array([0.0, 1.0, 0.0])
    return ts, ups


def integrate_gyro(gyro, g_map, q0, duration, accel_up, tau=3.0):
    sub = gyro
    dt = duration / len(gyro)
    qs = np.empty((len(sub), 4))
    ts = np.empty(len(sub))
    q = q0.copy()
    omegas = np.array([g_map @ v for _, v in sub])
    at_times, at_ups = accel_up
    k = dt / tau
    y_world = np.array([0.0, 1.0, 0.0])
    for i, w in enumerate(omegas):
        ts[i] = sub[i][0]
        omega = np.linalg.norm(w)
        if omega > 1e-9:
            q = quat_normalize(quat_mul(q, axis_angle_q(w, omega * dt)))
        if k > 0.0:
            j = min(np.searchsorted(at_times, ts[i]), len(at_ups) - 1)
            up_ref = at_ups[j]
            up_body = quat_rotate(quat_conj(q), y_world)
            ax = np.cross(up_body, up_ref)
            ang = math.acos(np.clip(np.dot(up_body, up_ref), -1, 1))
            if ang > 1e-9:
                corr = axis_angle_q(ax, k * ang)
                q = quat_normalize(quat_mul(q, quat_conj(corr)))
        qs[i] = q
    return ts, qs


def orientation_at(ts, qs, t):
    return qs[min(np.searchsorted(ts, t), len(qs) - 1)]


# --------------------------------------------------------------------------- #
# GyroOrientation service
# --------------------------------------------------------------------------- #

class GyroOrientation:
    """Per-time gravity-up and image<->world rotation from a gyro CSV + calib."""

    def __init__(self, gyro_csv: str, calib: Dict):
        self.csv_path = str(gyro_csv)
        self.a_map = np.asarray(calib["a_map"], dtype=float)
        self.s_map = np.asarray(calib["s_map"], dtype=float)
        self.g_map = np.asarray(calib["g_map"], dtype=float)
        q0_list = calib.get("q0") or calib.get("q0_list")
        self.q0 = np.asarray(q0_list, dtype=float)

        gyro, accel = load_gyro_csv(self.csv_path)
        if not gyro:
            raise ValueError(f"no gyro samples in {self.csv_path}")
        self.gyro = gyro
        self.accel = accel
        self.duration = float(gyro[-1][0]) or self.duration_fallback(gyro)
        accel_up = smoothed_accel_ups(accel, self.a_map)
        self.ts, self.qs = integrate_gyro(gyro, self.g_map, self.q0,
                                          self.duration, accel_up)
        # stitch->equirect basis change. Sun_from_gyro's s_map emits vectors in
        # the fisheye/stitch frame (X right, Y front, Z up); the equirect image
        # frame here is (X right, Y up, Z front). R_s2e: stitch XYZ -> equirect.
        R_s2e = np.array([[1.0, 0.0, 0.0],
                          [0.0, 0.0, 1.0],
                          [0.0, 1.0, 0.0]])
        self.P = R_s2e @ self.s_map

        # constant yaw-reference matrix D: align the camera front direction at
        # t=0 (projected horizontal) with world +Z. D fixes world Y.
        MEQ0 = self._meq(self.qs[0])
        world_front = MEQ0 @ np.array([0.0, 0.0, 1.0])
        h = world_front - world_front[1] * np.array([0.0, 1.0, 0.0])
        hn = np.linalg.norm(h)
        if hn > 1e-6:
            az_axis = h / hn
        else:
            az_axis = np.array([0.0, 0.0, 1.0])
        theta0 = math.atan2(az_axis[0], az_axis[2])
        cy, sy = math.cos(theta0), math.sin(theta0)
        self.D = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])

    @staticmethod
    def duration_fallback(gyro):
        return float(gyro[-1][0])

    def q(self, t):
        return orientation_at(self.ts, self.qs, t)

    def _meq(self, q) -> np.ndarray:
        """body quaternion q -> image-frame(equirect) rotation matrix.

        Such that MEQ @ [0,1,0] = gravity up in the equirect image frame.
        """
        return self.P @ quat_to_mat(q).T

    def M(self, t) -> np.ndarray:
        """image(eq) -> gauged-world (world Y = gravity up, arbitrary azimuth)."""
        if np.ndim(t) == 0:
            return self._meq(self.q(t))
        return np.stack([self._meq(orientation_at(self.ts, self.qs, tt))
                         for tt in t])

    def R_world_image(self, t) -> np.ndarray:
        """image(equirect)-frame unit dir -> world dir (Y up, Z = initial heading).

        A pure rotation (orthogonal), so its inverse/transpose maps world
        dirs back into the image frame.
        """
        return self.D @ self.M(t)

    def up_image(self, t) -> np.ndarray:
        """Gravity up expressed in the equirect image frame."""
        return self.M(t).T @ np.array([0.0, 1.0, 0.0])

    def to_world(self, v_image, t) -> np.ndarray:
        v = np.asarray(v_image, dtype=float)
        if v.ndim == 1:
            return self.R_world_image(t) @ (v / np.linalg.norm(v))
        R = self.R_world_image(t)
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return (v / n) @ R.T

    def bearing_elev(self, v_image, t) -> Tuple[float, float]:
        w = self.to_world(v_image, t)
        az = math.degrees(math.atan2(w[0], w[2]))
        el = math.degrees(math.asin(np.clip(w[1], -1.0, 1.0)))
        return az, el

    def world_to_image_dir(self, d_world, t) -> np.ndarray:
        return self.R_world_image(t).T @ (np.asarray(d_world) / np.linalg.norm(d_world))

    def view_pose_from_world(self, az_deg, elev_deg, t):
        """Given a view centred on world (az, elev) with gravity-aligned up,
        return (d_image, up_image) for building a level perspective view."""
        w = dir_from_lonlat(math.radians(az_deg), math.radians(elev_deg))
        d_image = self.world_to_image_dir(w, t)
        up_image = self.up_image(t)
        return d_image, up_image


def load_calib(path) -> Optional[Dict]:
    p = Path(path)
    if not p.exists():
        return None
    import json
    with open(p) as f:
        return json.load(f)
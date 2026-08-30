"""
Sphere Tracker
==============
Finds and tracks people on equirectangular 360° frames, storing each person's
position as a *unit 3D direction + angular size* on the sphere, never as a 2D
pixel bbox.

When a `GyroOrientation` is supplied:
  * every perspective view is extracted with its up axis aligned to gravity
    (horizon-level), so people are never tilted in the views;
  * detection, dedupe and tracking happen in a *world* frame (Y = gravity up,
    azimuth 0 = camera front at t=0), so tracks stay stable while the camera
    rolls / pitches / pans;
  * each output record keeps the camera/image-frame direction and *additionally*
    reports `world_dir_x/y/z`, `world_bearing_deg` (0 = initial heading) and
    `world_elev_deg` (vs gravity).

Without gyro, behaviour is identical to before (camera-frame only).

Coordinate conventions
----------------------
Equirectangular image : width W, height H.
  x pixel   -> longitude  in [-pi, pi]        (left -> right)
  y pixel   -> latitude   in [-pi/2, pi/2]    (top  -> bottom)

Unit direction vector (image frame):
  dir = [X, Y, Z] = [cos(lat)*sin(lon), sin(lat), cos(lat)*cos(lon)]   (+Z front)

World frame (when gyro on):
  Y = gravity up, Z = camera's initial horizontal heading, X = right.

Perspective view is a 3D rotation: dir_image = M @ ray_view, with
  M = Ry(yaw) @ Rx(pitch) @ Rz(roll).  Default roll = 0 == previous behaviour.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

import numpy as np
import cv2


# --------------------------------------------------------------------------- #
# Vector / spherical helpers
# --------------------------------------------------------------------------- #

def dir_from_lonlat(lon: float, lat: float) -> np.ndarray:
    cl, sl = math.cos(lon), math.sin(lon)
    cb, sb = math.cos(lat), math.sin(lat)
    return np.array([cb * sl, sb, cb * cl], dtype=np.float64)


def lonlat_from_dir(d: np.ndarray) -> Tuple[float, float]:
    d = d / np.linalg.norm(d)
    lon = math.atan2(d[0], d[2])
    lat = math.asin(np.clip(d[1], -1.0, 1.0))
    return lon, lat


def angular_distance(a: np.ndarray, b: np.ndarray) -> float:
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    c = float(np.clip(np.dot(a, b), -1.0, 1.0))
    return math.acos(c)


def lonlat_to_equirect_px(lon, lat, w, h):
    x = (lon / math.pi + 1.0) / 2.0 * w
    y = (-lat / (math.pi / 2.0) + 1.0) / 2.0 * h
    return x, y


# --------------------------------------------------------------------------- #
# Rotation helpers
# --------------------------------------------------------------------------- #

def rotation_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rotation_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rotation_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def euler_to_matrix(yaw_deg, pitch_deg, roll_deg):
    """M = Ry(yaw) @ Rx(pitch) @ Rz(roll); view ray -> image dir."""
    return rotation_y(math.radians(yaw_deg)) @ \
        rotation_x(math.radians(pitch_deg)) @ \
        rotation_z(math.radians(roll_deg))


def matrix_from_axes(d_forward, d_up):
    """Build view->image rotation whose +Z = d_forward, +Y = d_up (perp)."""
    z = np.asarray(d_forward, dtype=np.float64) / np.linalg.norm(d_forward)
    up = np.asarray(d_up, dtype=np.float64)
    up = up - np.dot(up, z) * z
    up = up / np.linalg.norm(up)
    right = np.cross(up, z)
    return np.c_[right, up, z]


# --------------------------------------------------------------------------- #
# Perspective view extraction + inverse mapping
# --------------------------------------------------------------------------- #

def perspective_view_params(yaw_deg, pitch_deg, fov_deg, out_w, out_h,
                            roll_deg=0.0):
    """Return dict of precomputed view geometry for the given orientation."""
    f = (out_w / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    return {
        "yaw": yaw_deg,
        "pitch": pitch_deg,
        "roll": roll_deg,
        "fov": fov_deg,
        "out_w": out_w,
        "out_h": out_h,
        "f": f,
        "M": euler_to_matrix(yaw_deg, pitch_deg, roll_deg),
    }


def perspective_view_params_from_axes(d_forward, d_up, fov_deg, out_w, out_h):
    """View geometry from an image-frame forward + up vector (gyro-leveled)."""
    fov_deg = float(fov_deg)
    f = (out_w / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    M = matrix_from_axes(d_forward, d_up)
    z = M[:, 2]
    yaw = math.degrees(math.atan2(z[0], z[2]))
    pitch = math.degrees(math.asin(np.clip(z[1], -1.0, 1.0)))
    return {
        "yaw": yaw,
        "pitch": pitch,
        "roll": 0.0,
        "fov": fov_deg,
        "out_w": out_w,
        "out_h": out_h,
        "f": f,
        "M": M,
    }


def extract_perspective(equirect: np.ndarray, prm: dict) -> np.ndarray:
    """Extract a perspective view image from an equirectangular frame."""
    eq_h, eq_w = equirect.shape[:2]
    out_w, out_h = prm["out_w"], prm["out_h"]
    f = prm["f"]
    M = prm["M"]

    x = np.arange(out_w) - out_w / 2.0
    y = out_h / 2.0 - np.arange(out_h)
    xx, yy = np.meshgrid(x, y)
    zz = np.full_like(xx, f, dtype=np.float32)

    norm = np.sqrt(xx ** 2 + yy ** 2 + zz ** 2)
    ray = np.stack([xx / norm, yy / norm, zz / norm], axis=0)          # (3,H,W)
    dirs = np.einsum("ab,bhw->ahw", M, ray)                            # (3,H,W)
    lon = np.arctan2(dirs[0], dirs[2])
    lat = np.arcsin(np.clip(dirs[1], -1.0, 1.0))

    e_x = (lon / math.pi + 1.0) / 2.0 * eq_w
    e_y = (-lat / (math.pi / 2.0) + 1.0) / 2.0 * eq_h

    return cv2.remap(
        equirect,
        e_x.astype(np.float32),
        e_y.astype(np.float32),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_WRAP,
    )


def view_px_to_dir(prm: dict, px, py) -> np.ndarray:
    """Map a perspective-view pixel to a unit direction on the sphere."""
    f = prm["f"]
    M = prm["M"]
    x = px - prm["out_w"] / 2.0
    y = prm["out_h"] / 2.0 - py
    v = M @ np.array([x, y, f], dtype=np.float64)
    return v / np.linalg.norm(v)


def ang_size_from_box(prm: dict, box) -> Tuple[float, float]:
    """Subtended angular width/height (deg) of a box on a perspective view."""
    x1, y1, x2, y2 = [float(v) for v in box]
    w = max(x2 - x1, 1.0)
    h = max(y2 - y1, 1.0)
    f = prm["f"]
    aw = math.degrees(2.0 * math.atan((w / 2.0) / f))
    ah = math.degrees(2.0 * math.atan((h / 2.0) / f))
    return aw, ah


# --------------------------------------------------------------------------- #
# Data types
# --------------------------------------------------------------------------- #

@dataclass
class PersonDetection:
    """A person detected in one frame, in spherical terms."""
    frame_index: int
    timestamp: float
    person_id: Optional[int]
    direction: np.ndarray          # unit 3D vector (camera/image frame)
    angular_width_deg: float
    angular_height_deg: float
    confidence: float
    view_yaw: float                # origin perspective view yaw (image frame)
    view_pitch: float
    view_box: Tuple[float, float, float, float]
    poly_lonlat: Optional[List] = None    # [[lon_deg, lat_deg], ...] image frame
    world_direction: Optional[np.ndarray] = None   # unit 3D (world frame)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "frame_index": self.frame_index,
            "timestamp": round(self.timestamp, 6),
            "person_id": self.person_id,
            "dir_x": round(float(self.direction[0]), 6),
            "dir_y": round(float(self.direction[1]), 6),
            "dir_z": round(float(self.direction[2]), 6),
            "lon_deg": round(math.degrees(math.atan2(self.direction[0], self.direction[2])), 4),
            "lat_deg": round(math.degrees(math.asin(np.clip(self.direction[1], -1, 1))), 4),
            "angular_width_deg": round(self.angular_width_deg, 4),
            "angular_height_deg": round(self.angular_height_deg, 4),
            "confidence": round(self.confidence, 4),
        }
        if self.poly_lonlat:
            d["poly_lonlat"] = [[round(float(a), 3), round(float(b), 3)]
                                for a, b in self.poly_lonlat]
        if self.world_direction is not None:
            w = self.world_direction / np.linalg.norm(self.world_direction)
            d["world_dir_x"] = round(float(w[0]), 6)
            d["world_dir_y"] = round(float(w[1]), 6)
            d["world_dir_z"] = round(float(w[2]), 6)
            d["world_bearing_deg"] = round(math.degrees(math.atan2(w[0], w[2])), 4)
            d["world_elev_deg"] = round(math.degrees(math.asin(np.clip(w[1], -1, 1))), 4)
        return d


@dataclass
class Track:
    """A person track across frames (direction in the association frame)."""
    track_id: int
    direction: np.ndarray
    last_frame: int
    last_angular_width: float = 0.0
    last_angular_height: float = 0.0
    last_confidence: float = 0.0
    last_view: Tuple[float, float] = (0.0, 0.0)
    missed_frames: int = 0
    age: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Detector wrapper
# --------------------------------------------------------------------------- #

class PersonDetector:
    """Thin wrapper around Ultralytics YOLO for person boxes on views."""

    def __init__(self, model_path: str = "yolo11n.pt",
                 confidence: float = 0.30, device: Optional[str] = None):
        self.model_path = model_path
        self.confidence = confidence
        self.device = device
        self.model = None

    def load(self) -> bool:
        try:
            from ultralytics import YOLO
            import torch
            self.model = YOLO(self.model_path)
            dev = self.device or ('cuda' if torch.cuda.is_available() else 'cpu')
            self.model.to(dev)
            return True
        except Exception as e:
            print(f"PersonDetector: failed to load {self.model_path}: {e}")
            return False

    def detect(self, view: np.ndarray):
        """Return [(box, conf, poly)] where poly is a (N,2) array of view-image
        pixel coords for the YOLO segmentation mask boundary (fallback: the
        bbox corners), or [] on failure."""
        if self.model is None:
            if not self.load():
                return []
        try:
            results = self.model(view, conf=self.confidence, classes=[0],
                                 verbose=False)
            out = []
            if len(results) == 0:
                return out
            r = results[0]
            if r.boxes is None:
                return out
            boxes = r.boxes.xyxy.cpu().numpy()
            confs = r.boxes.conf.cpu().numpy()
            masks = None
            if r.masks is not None and r.masks.xy is not None:
                masks = r.masks.xy            # list of (N,2) in view pixels
            for i, (box, conf) in enumerate(zip(boxes, confs)):
                if masks is not None and i < len(masks):
                    poly = np.asarray(masks[i], dtype=float)
                else:
                    x1, y1, x2, y2 = box
                    poly = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                                    dtype=float)
                out.append((np.asarray(box, dtype=float), float(conf), poly))
            return out
        except Exception as e:
            print(f"PersonDetector: inference error: {e}")
            return []


# --------------------------------------------------------------------------- #
# The tracker
# --------------------------------------------------------------------------- #

class SpherePersonTracker:
    """
    Detect + track people on a sequence of equirectangular frames.

    With `gyro` (a core.gyro_orientation.GyroOrientation), detection views are
    gravity-leveled and association + output happen in the world frame.
    """

    def __init__(self,
                 detector: Optional[PersonDetector] = None,
                 detect_fov: float = 90.0,
                 detect_view_size: int = 640,
                 num_views: int = 8,
                 pitch_deg: float = 0.0,
                 overlap_merge_deg: float = 3.0,
                 track_match_deg: float = 12.0,
                 max_missed: int = 15,
                 recheck_interval: int = 30,
                 center_fov: float = 60.0,
                 center_view_size: int = 640,
                 max_person_deg: float = 90.0,
                 min_person_deg: float = 1.0,
                 edge_margin_frac: float = 0.06,
                 lat_min_deg: float = -90.0,
                 lat_max_deg: float = 80.0,
                 min_aspect: float = 1.0,
                 nadir_deg: float = 0.0,
                 gyro=None):
        self.detector = detector or PersonDetector()
        self.detect_fov = detect_fov
        self.detect_view_size = detect_view_size
        self.num_views = num_views
        self.pitch_deg = pitch_deg
        self.overlap_merge_deg = overlap_merge_deg
        self.track_match_deg = track_match_deg
        self.max_missed = max_missed
        self.recheck_interval = recheck_interval
        self.center_fov = center_fov
        self.center_view_size = center_view_size
        self.max_person_deg = max_person_deg
        self.min_person_deg = min_person_deg
        self.edge_margin_frac = edge_margin_frac
        self.lat_min_deg = lat_min_deg
        self.lat_max_deg = lat_max_deg
        self.min_aspect = min_aspect
        self.nadir_deg = nadir_deg
        self.gyro = gyro

        self.yaw_angles = np.linspace(-180, 180, num_views, endpoint=False)
        self._detect_prms = [
            perspective_view_params(yaw, pitch_deg, detect_fov,
                                    detect_view_size, detect_view_size)
            for yaw in self.yaw_angles
        ]
        self._world_grid = [(float(az), float(pitch_deg)) for az in self.yaw_angles]
        # optional dedicated nadir view: straight down, catches the operator
        # who sits at extreme negative elevation below the camera.
        if self.nadir_deg > 0.0:
            self._world_grid.append((0.0, -self.nadir_deg))

        self.tracks: List[Track] = []
        self._next_track_id = 0
        self._equirect_shape: Optional[Tuple[int, int]] = None

    @property
    def world_mode(self) -> bool:
        return self.gyro is not None

    def _build_prms(self, t: float) -> List[dict]:
        if not self.world_mode:
            return self._detect_prms
        prms = []
        for az, el in self._world_grid:
            d_image, up_image = self.gyro.view_pose_from_world(az, el, t)
            prms.append(perspective_view_params_from_axes(
                d_image, up_image, self.detect_fov,
                self.detect_view_size, self.detect_view_size))
        return prms

    # ---- view-centric detection -------------------------------------------

    def _detect_in_views(self, equirect: np.ndarray, t: float,
                         prms: List[dict]) -> List[tuple]:
        raw: List[tuple] = []
        for prm in prms:
            view = extract_perspective(equirect, prm)
            boxes = self.detector.detect(view)
            vsz = prm["out_w"]
            for box, conf, poly in boxes:
                d = self._raw_to_tuple(box, conf, prm, t, poly)
                if d is not None and self._gate(d, vsz):
                    raw.append(d)
        return raw

    def _raw_to_tuple(self, box, conf, prm, t, poly=None) -> tuple:
        """(assoc_dir, image_dir, aw, ah, conf, yaw, pitch, box, poly_lonlat)."""
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        vdir_image = view_px_to_dir(prm, cx, cy)
        aw, ah = ang_size_from_box(prm, box)
        if self.world_mode:
            assoc = self.gyro.to_world(vdir_image, t)
        else:
            assoc = vdir_image
        poly_lonlat = None
        if poly is not None and len(poly) >= 3:
            pts = []
            for px, py in poly:
                d = view_px_to_dir(prm, px, py)
                pts.append([math.degrees(math.atan2(d[0], d[2])),
                            math.degrees(math.asin(np.clip(d[1], -1, 1)))])
            poly_lonlat = pts
        return (assoc, vdir_image, aw, ah, conf, prm["yaw"], prm["pitch"],
                tuple(box), poly_lonlat)

    def _gate(self, d: tuple, view_size: int) -> bool:
        assoc, _, aw, ah, conf, yaw, pitch, box, poly_lonlat = d
        if aw < self.min_person_deg or ah < self.min_person_deg:
            return False
        if aw > self.max_person_deg or ah > self.max_person_deg:
            return False
        # latitude band (world elevation when gyro is on, image lat otherwise)
        _, lat = lonlat_from_dir(assoc)
        lat_deg = math.degrees(lat)
        if not (self.lat_min_deg <= lat_deg <= self.lat_max_deg):
            return False
        x1, y1, x2, y2 = box
        vw = x2 - x1
        vh = y2 - y1
        if vw <= 0 or vh <= 0:
            return False
        if vh / vw < self.min_aspect:
            return False
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        m = self.edge_margin_frac
        if not (m <= cx / view_size <= 1 - m):
            return False
        if not (m <= cy / view_size <= 1 - m):
            return False
        # Reject detections whose box is truncated by a view edge: the mask is
        # incomplete and unprojects into a distorted shape (worst at the downward
        # nadir view near the pole). Box must be fully inside the view.
        lo = m * view_size
        hi = (1 - m) * view_size
        if not (lo <= x1 and x2 <= hi and lo <= y1 and y2 <= hi):
            return False
        return True

    @staticmethod
    def _dedupe(raw: List[tuple], merge_deg: float) -> List[tuple]:
        if not raw:
            return []
        merged = []
        for d in raw:
            placed = False
            for i, m in enumerate(merged):
                ang = angular_distance(d[0], m[0])
                if ang < math.radians(merge_deg):
                    a = (d[0] + m[0])
                    a = a / np.linalg.norm(a)
                    im = (d[1] + m[1])
                    im = im / np.linalg.norm(im)
                    # keep the polygon from the larger detection (fewer holes)
                    poly = d[8] if d[2] > m[2] else m[8]
                    merged[i] = (
                        a, im,
                        max(m[2], d[2]),
                        max(m[3], d[3]),
                        max(m[4], d[4]),
                        m[5], m[6], m[7], poly,
                    )
                    placed = True
                    break
            if not placed:
                merged.append(d)
        return merged

    # ---- assimilation / tracking ------------------------------------------

    def _associate(self, dets: List[tuple], frame_index: int, timestamp: float):
        if not self.tracks:
            for d in dets:
                self._spawn_track(d, frame_index, timestamp)
            return
        assigned_tracks = set()
        for d in dets:
            best = None
            best_ang = math.inf
            for tr in self.tracks:
                if tr.track_id in assigned_tracks:
                    continue
                ang = angular_distance(d[0], tr.direction)
                if ang < math.radians(self.track_match_deg) and ang < best_ang:
                    best_ang = ang
                    best = tr
            if best is not None:
                best.direction = d[0]
                best.last_frame = frame_index
                best.last_angular_width = d[2]
                best.last_angular_height = d[3]
                best.last_confidence = d[4]
                best.last_view = (d[5], d[6])
                best.missed_frames = 0
                best.age += 1
                assigned_tracks.add(best.track_id)
                best.history.append(_make_record(best.track_id, d, frame_index, timestamp,
                                                 self.world_mode))
            else:
                self._spawn_track(d, frame_index, timestamp)

        for tr in self.tracks:
            if tr.track_id not in assigned_tracks:
                tr.missed_frames += 1

        keep = []
        for tr in self.tracks:
            if tr.missed_frames <= self.max_missed:
                keep.append(tr)
        self.tracks = keep

    def _spawn_track(self, d: tuple, frame_index: int, timestamp: float):
        tr = Track(
            track_id=self._next_track_id,
            direction=d[0],
            last_frame=frame_index,
            last_angular_width=d[2],
            last_angular_height=d[3],
            last_confidence=d[4],
            last_view=(d[5], d[6]),
            age=1,
        )
        self._next_track_id += 1
        tr.history.append(_make_record(tr.track_id, d, frame_index, timestamp,
                                       self.world_mode))
        self.tracks.append(tr)

    # ---- public API --------------------------------------------------------

    def process_frame(self, equirect: np.ndarray, frame_index: int,
                      timestamp: float = 0.0,
                      enable_centering: bool = True) -> List[Dict[str, Any]]:
        if equirect is None:
            return []
        self._equirect_shape = equirect.shape[:2]

        if enable_centering and self.tracks:
            dets = self._tracked_detection(equirect, frame_index, timestamp)
        else:
            prms = self._build_prms(timestamp)
            raw = self._detect_in_views(equirect, timestamp, prms)
            dets = self._dedupe(raw, self.overlap_merge_deg)

        self._associate(dets, frame_index, timestamp)

        out = []
        for tr in self.tracks:
            if tr.last_frame == frame_index:
                for rec in tr.history:
                    if rec["frame_index"] == frame_index:
                        out.append(rec)
        return out

    def _track_view_prm(self, d_world, t, fov, size) -> dict:
        lon, lat = lonlat_from_dir(d_world)
        if self.world_mode:
            d_image, up_image = self.gyro.view_pose_from_world(
                math.degrees(lon), math.degrees(lat), t)
            return perspective_view_params_from_axes(d_image, up_image, fov, size, size)
        return perspective_view_params(math.degrees(lon), math.degrees(lat),
                                       fov, size, size)

    def _tracked_detection(self, equirect, frame_index, t):
        centred: List[tuple] = []
        for tr in list(self.tracks):
            prm = self._track_view_prm(tr.direction, t, self.center_fov,
                                       self.center_view_size)
            view = extract_perspective(equirect, prm)
            boxes = self.detector.detect(view)
            if not boxes:
                continue
            best = None
            best_d = math.inf
            for box, conf, poly in boxes:
                d = self._raw_to_tuple(box, conf, prm, t, poly)
                if d is None or not self._gate(d, self.center_view_size):
                    continue
                c2 = (box[0] + box[2]) / 2.0
                c3 = (box[1] + box[3]) / 2.0
                dist = math.hypot(c2 - self.center_view_size / 2.0,
                                  c3 - self.center_view_size / 2.0)
                if dist < best_d:
                    best_d = dist
                    best = d
            if best is not None:
                centred.append(best)

        if frame_index % self.recheck_interval == 0:
            prms = self._build_prms(t)
            raw = self._detect_in_views(equirect, t, prms)
            sweep = self._dedupe(raw, self.overlap_merge_deg)
            centred.extend(sweep)

        return self._dedupe(centred, self.overlap_merge_deg)

    def tracks_summary(self) -> List[Dict[str, Any]]:
        out = []
        for tr in self.tracks:
            out.append({
                "track_id": tr.track_id,
                "frames_seen": tr.age,
                "missed_frames": tr.missed_frames,
                "dir_x": float(tr.direction[0]),
                "dir_y": float(tr.direction[1]),
                "dir_z": float(tr.direction[2]),
            })
        return out


def _make_record(track_id, d, frame_index, timestamp, world_mode=False) -> Dict[str, Any]:
    assoc, image_dir, aw, ah, conf, yaw, pitch, box, poly_lonlat = d
    rec = PersonDetection(
        frame_index=frame_index,
        timestamp=timestamp,
        person_id=track_id,
        direction=image_dir,
        angular_width_deg=aw,
        angular_height_deg=ah,
        confidence=conf,
        view_yaw=yaw,
        view_pitch=pitch,
        view_box=box,
        poly_lonlat=poly_lonlat,
        world_direction=assoc if world_mode else None,
    ).to_dict()
    return rec
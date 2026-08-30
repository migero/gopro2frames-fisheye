"""Shared CLI arguments for the viewer (kept in sync with cli_sphere_tracker.py)."""


def add_common_args(ap):
    ap.add_argument("--model", default="yolo11n-seg.pt")
    ap.add_argument("--conf", type=float, default=0.40)
    ap.add_argument("--fov", type=float, default=90.0)
    ap.add_argument("--views", type=int, default=8)
    ap.add_argument("--pitch", type=float, default=0.0)
    ap.add_argument("--view-size", type=int, default=640)
    ap.add_argument("--track-match", type=float, default=12.0)
    ap.add_argument("--max-missed", type=int, default=15)
    ap.add_argument("--recheck", type=int, default=30)
    ap.add_argument("--max-person-deg", type=float, default=90.0)
    ap.add_argument("--min-person-deg", type=float, default=1.0)
    ap.add_argument("--min-aspect", type=float, default=1.0)
    ap.add_argument("--lat-min", type=float, default=-90.0)
    ap.add_argument("--lat-max", type=float, default=80.0)
    ap.add_argument("--edge-margin", type=float, default=0.06)
    ap.add_argument("--nadir", type=float, default=0.0,
                    help="Add a straight-down detection view (deg below "
                         "horizon) to also track the operator below the camera")
    ap.add_argument("--gyro-calib", default="gyro_calib.json")
    ap.add_argument("--no-gyro", action="store_true")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--equirect-width", type=int, default=2048)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--start-frame", type=int, default=0,
                    help="Skip the first N frames (fast keyframe seek for video;"
                         " slice for a frame folder) to avoid the camera "
                         "operator being too close at the start. Frame "
                         "indexing stays absolute so world-frame/gyro output "
                         "remains aligned with real recording time.")

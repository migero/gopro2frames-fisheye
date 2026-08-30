"""Server-side state shared with the HTTP routes and live loop."""

from .trackstore import TrackStore


class ViewerServer:
    """Holds the shared state used by both the live loop and the HTTP API."""

    def __init__(self, fps=30.0, port=8123, live=False):
        self.fps = fps
        self.port = port
        self.live = live
        self.tracks = TrackStore()
        self.dim = (2048, 1024)
        self.live_meta = {"running": False, "fps": 0.0, "frames": 0}
        self.frame_index = 0
        self.views = []          # [{az_deg, el_deg}] nominal world-frame view centers
        self.fov = 90.0          # square perspective fov (deg), matches tracker detect_fov
        self.has_gyro = False
        self.cur_pose = None     # world->image rotation for the latest frame (3x3), or None
        self.gyro = None         # gyro pose provider (set in live loop when enabled)
        self.ts_by_frame = {}    # frame_index -> timestamp (seconds) for seek pose lookup

    def pose_for_frame(self, fi):
        ts = self.ts_by_frame.get(fi)
        if ts is None or self.gyro is None:
            return None
        try:
            return self.gyro.R_world_image(ts).tolist()
        except Exception:
            return None

    def info(self):
        frames = self.tracks.frames()
        return {
            "live": self.live,
            "fps": self.fps,
            "width": self.dim[0],
            "height": self.dim[1],
            "tracks": self.tracks.count,
            "track_frames": frames,
            "min_frame": frames[0] if frames else 0,
            "max_frame": frames[-1] if frames else 0,
            "live_meta": self.live_meta,
            "has_gyro": self.has_gyro,
        }

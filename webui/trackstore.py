"""Thread-safe store for per-frame detection records."""

import json
import threading


class TrackStore:
    """Holds detections either loaded from a JSONL or appended live."""

    def __init__(self):
        self._lock = threading.Lock()
        self._index = {}   # frame_index -> [records]
        self._seq = {}
        self._order = []
        self._count = 0

    def load_jsonl(self, path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.add(r)

    def add(self, rec):
        fi = rec.get("frame_index")
        with self._lock:
            if fi not in self._index:
                self._index[fi] = []
                self._order.append(fi)
            self._index[fi].append(rec)
            self._seq.setdefault(fi, 0)
            self._seq[fi] += 1
            self._count += 1

    def for_frame(self, fi):
        with self._lock:
            return list(self._index.get(fi, []))

    def frames(self):
        with self._lock:
            return sorted(self._index.keys())

    def records_after(self, n):
        with self._lock:
            out = []
            for fi in sorted(self._index.keys()):
                if fi <= n:
                    continue
                out.extend(self._index[fi])
            return out

    @property
    def count(self):
        return self._count

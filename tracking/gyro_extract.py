#!/usr/bin/env python3
"""Extract gyro/accelerometer samples from a GoPro .360 file's GPMF metadata
into the same Time(s)/Type/X/Y/Z/Magnitude CSV used across this project.

Ported from 360_motion_from_sensors/visualize_motion_stream.py.
"""
import csv
import json
import struct
import subprocess
from pathlib import Path
from typing import List, Dict, Optional


class GPMFParser:
    def __init__(self, data: bytes):
        self.data = data
        self.gyro_samples: List[Dict] = []
        self.accel_samples: List[Dict] = []

    def parse(self):
        self._parse_gyro()
        self._parse_accel()

    def _find_scale_factor(self, search_end: int, fourcc: str = 'SCAL') -> List[float]:
        scale = [1.0, 1.0, 1.0]
        scal_pos = self.data.rfind(fourcc.encode(), max(0, search_end - 300), search_end)
        if scal_pos >= 0:
            try:
                type_char = chr(self.data[scal_pos + 4]) if scal_pos + 4 < len(self.data) else ''
                repeat_count = struct.unpack('>H', self.data[scal_pos + 6:scal_pos + 8])[0]
                if repeat_count >= 1:
                    if type_char == 'l':
                        for i in range(min(3, repeat_count)):
                            offset = scal_pos + 8 + i * 4
                            if offset + 4 <= len(self.data):
                                val = struct.unpack('>i', self.data[offset:offset + 4])[0]
                                if val != 0:
                                    scale[i] = float(val)
                        if repeat_count == 1 and scale[0] != 1.0:
                            scale[1] = scale[2] = scale[0]
                    elif type_char == 's':
                        for i in range(min(3, repeat_count)):
                            offset = scal_pos + 8 + i * 2
                            if offset + 2 <= len(self.data):
                                val = struct.unpack('>h', self.data[offset:offset + 2])[0]
                                if val != 0:
                                    scale[i] = float(val)
                        if repeat_count == 1 and scale[0] != 1.0:
                            scale[1] = scale[2] = scale[0]
            except Exception:
                pass
        return scale

    def _parse_gyro(self):
        search_offset = 0
        while True:
            pos = self.data.find(b'GYRO', search_offset)
            if pos < 0:
                break
            try:
                scale = self._find_scale_factor(pos)
                type_char = chr(self.data[pos + 4]) if pos + 4 < len(self.data) else ''
                repeat_count = struct.unpack('>H', self.data[pos + 6:pos + 8])[0]
                if type_char == 's':
                    for i in range(repeat_count):
                        offset = pos + 8 + i * 6
                        if offset + 6 <= len(self.data):
                            raw_y = struct.unpack('>h', self.data[offset:offset + 2])[0]
                            raw_neg_x = struct.unpack('>h', self.data[offset + 2:offset + 4])[0]
                            raw_z = struct.unpack('>h', self.data[offset + 4:offset + 6])[0]
                            self.gyro_samples.append({
                                'x': -raw_neg_x / scale[1],
                                'y': raw_y / scale[0],
                                'z': raw_z / scale[2],
                            })
            except Exception:
                pass
            search_offset = pos + 8

    def _parse_accel(self):
        search_offset = 0
        while True:
            pos = self.data.find(b'ACCL', search_offset)
            if pos < 0:
                break
            try:
                scale = self._find_scale_factor(pos)
                type_char = chr(self.data[pos + 4]) if pos + 4 < len(self.data) else ''
                repeat_count = struct.unpack('>H', self.data[pos + 6:pos + 8])[0]
                if type_char == 's':
                    for i in range(repeat_count):
                        offset = pos + 8 + i * 6
                        if offset + 6 <= len(self.data):
                            raw_y = struct.unpack('>h', self.data[offset:offset + 2])[0]
                            raw_x = struct.unpack('>h', self.data[offset + 2:offset + 4])[0]
                            raw_z = struct.unpack('>h', self.data[offset + 4:offset + 6])[0]
                            self.accel_samples.append({
                                'x': raw_x / scale[1],
                                'y': raw_y / scale[0],
                                'z': raw_z / scale[2],
                            })
            except Exception:
                pass
            search_offset = pos + 8


def find_gpmf_stream(video_path: str) -> Optional[int]:
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
               '-show_streams', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        for stream in data.get('streams', []):
            codec_tag = stream.get('codec_tag_string', '')
            handler_name = stream.get('tags', {}).get('handler_name', '')
            if codec_tag == 'gpmd' or 'GoPro MET' in handler_name:
                return stream['index']
        return None
    except Exception:
        return None


def extract_gpmf_data(video_path: str, output_path: str) -> bool:
    stream_index = find_gpmf_stream(video_path)
    if stream_index is None:
        print(f"  no GPMF metadata stream in {video_path}")
        return False
    try:
        cmd = ['ffmpeg', '-y', '-v', 'quiet',
               '-i', video_path,
               '-codec', 'copy',
               '-map', f'0:{stream_index}',
               '-f', 'rawvideo',
               output_path]
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def get_video_duration(video_path: str) -> Optional[float]:
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
               '-show_format', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(json.loads(result.stdout)['format']['duration'])
    except Exception:
        return None


def save_to_csv(gyro_samples, accel_samples, duration: float, output_path: str):
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Time(s)', 'Type', 'X', 'Y', 'Z', 'Magnitude'])
        for i, s in enumerate(gyro_samples):
            t = (i / len(gyro_samples)) * duration
            mag = (s['x'] ** 2 + s['y'] ** 2 + s['z'] ** 2) ** 0.5
            writer.writerow([f"{t:.3f}", 'GYRO', f"{s['x']:.3f}", f"{s['y']:.3f}",
                             f"{s['z']:.3f}", f"{mag:.3f}"])
        for i, s in enumerate(accel_samples):
            t = (i / len(accel_samples)) * duration
            mag = (s['x'] ** 2 + s['y'] ** 2 + s['z'] ** 2) ** 0.5
            writer.writerow([f"{t:.3f}", 'ACCL', f"{s['x']:.3f}", f"{s['y']:.3f}",
                             f"{s['z']:.3f}", f"{mag:.3f}"])


def extract_motion_csv(video_path: str, out_csv: str) -> bool:
    """Extract GPMF gyro/accel from a .360 video into out_csv. Returns False on failure."""
    video = Path(video_path)
    tmp_bin = Path(out_csv).with_suffix(".gpmd.bin")
    if not extract_gpmf_data(str(video), str(tmp_bin)):
        return False
    try:
        data = tmp_bin.read_bytes()
        parser = GPMFParser(data)
        parser.parse()
        duration = get_video_duration(str(video)) or parser.gyro_samples and 0.0
        duration = duration or 0.0
        if not parser.gyro_samples or not parser.accel_samples:
            return False
        save_to_csv(parser.gyro_samples, parser.accel_samples, duration, out_csv)
        return True
    finally:
        if tmp_bin.exists():
            tmp_bin.unlink()
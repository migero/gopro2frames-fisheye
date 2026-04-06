"""
GYRO sensor data processing for GoPro cameras.
Handles extraction and integration of GPMF GYRO samples.
"""

import json
import logging
import math
import os
import struct as _struct
import subprocess
import tempfile
import numpy as np


def parse_gpmf_gyro(video_file):
    """
    Extract raw GYRO samples from the GPMF stream.
    ORIO=ZXY: stored byte order is [Z_cam, X_cam, Y_cam].
    Returns list of (z_cam, x_cam, y_cam) in rad/s.
    """
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', video_file]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        gpmf_idx = None
        for s in json.loads(result.stdout).get('streams', []):
            if s.get('codec_tag_string') == 'gpmd':
                gpmf_idx = s['index']
                break
        if gpmf_idx is None:
            return []
        with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
            gpmf_path = tmp.name
        subprocess.run(['ffmpeg', '-y', '-v', 'quiet', '-i', video_file,
                        '-codec', 'copy', '-map', f'0:{gpmf_idx}',
                        '-f', 'rawvideo', gpmf_path], check=True)
        with open(gpmf_path, 'rb') as f:
            data = f.read()
        os.unlink(gpmf_path)

        samples = []
        offset = 0
        while True:
            pos = data.find(b'GYRO', offset)
            if pos < 0:
                break
            try:
                scale = 1.0
                scal_pos = data.rfind(b'SCAL', max(0, pos - 600), pos)
                if scal_pos >= 0:
                    st = chr(data[scal_pos + 4])
                    sr = _struct.unpack('>H', data[scal_pos + 6:scal_pos + 8])[0]
                    if st == 's' and sr >= 1:
                        v = _struct.unpack('>h', data[scal_pos + 8:scal_pos + 10])[0]
                        if v != 0:
                            scale = float(v)
                    elif st == 'l' and sr >= 1:
                        v = _struct.unpack('>i', data[scal_pos + 8:scal_pos + 12])[0]
                        if v != 0:
                            scale = float(v)
                t = chr(data[pos + 4])
                sz = data[pos + 5]
                repeat = _struct.unpack('>H', data[pos + 6:pos + 8])[0]
                if t == 's' and sz == 6:
                    for i in range(repeat):
                        base = pos + 8 + i * 6
                        if base + 6 <= len(data):
                            z_cam, x_cam, y_cam = _struct.unpack('>hhh', data[base:base + 6])
                            samples.append((z_cam / scale, x_cam / scale, y_cam / scale))
            except Exception:
                pass
            offset = pos + 8
        return samples
    except Exception as e:
        logging.warning(f'Could not parse GPMF GYRO: {e}')
        return []


def integrate_gyro_roll(gyro_samples, duration, fps, n_frames):
    """
    Integrate raw GYRO samples as a 3D rotation matrix (Rodrigues formula).
    Returns a dict {frame_number (1-based): roll_deg} where roll_deg is the
    accumulated rotation around the optical axis (Y_cam) since frame 1.

    ORIO=ZXY: (z_cam, x_cam, y_cam) — map to camera-frame axes:
        wx = x_cam  (camera right)
        wy = y_cam  (optical axis = forward)
        wz = z_cam  (camera up)
    """
    n = len(gyro_samples)
    if n == 0:
        return {}

    dt = duration / n          # time per gyro sample
    frame_dt = 1.0 / fps       # time per video frame

    R = np.eye(3)              # orientation starts at identity (frame 1 = reference)
    roll_per_frame = {}        # 1-based frame number → roll_deg

    next_frame_time = 0.0      # we snapshot R at these moments
    frame_num = 1

    for i, (z_cam, x_cam, y_cam) in enumerate(gyro_samples):
        t = i * dt

        # Snapshot before we step past the frame boundary
        if t >= next_frame_time and frame_num <= n_frames:
            # Extract roll around optical axis (Y_cam = column 1 of world axes)
            # R[:,1] = where Y_cam has moved; but we want roll around the *current*
            # optical axis, which is the angle between R's X column and the original X.
            # roll = atan2(-R[2,0], R[0,0])  — rotation around Y by Euler decomposition
            roll_rad = math.atan2(-R[2, 0], R[0, 0])
            roll_per_frame[frame_num] = math.degrees(roll_rad)
            next_frame_time += frame_dt
            frame_num += 1
            if frame_num > n_frames:
                break

        # Integrate: wx=x_cam, wy=y_cam(optical), wz=z_cam
        wx, wy, wz = x_cam, y_cam, z_cam
        omega = np.array([wx, wy, wz])
        theta = np.linalg.norm(omega) * dt
        if theta > 1e-10:
            k = omega / np.linalg.norm(omega)
            K = np.array([[ 0,   -k[2],  k[1]],
                          [ k[2], 0,    -k[0]],
                          [-k[1], k[0],  0   ]])
            R_step = np.eye(3) + math.sin(theta) * K + (1 - math.cos(theta)) * (K @ K)
            R = R @ R_step

    # Fill any remaining frames with last known roll
    last_roll = roll_per_frame.get(max(roll_per_frame, default=1), 0.0)
    for fn in range(frame_num, n_frames + 1):
        roll_per_frame[fn] = last_roll

    return roll_per_frame

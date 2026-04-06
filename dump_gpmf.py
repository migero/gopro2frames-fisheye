#!/usr/bin/env python3
"""
Dump raw GPMF sensor data from a GoPro .360 file.

Usage:
    python dump_gpmf.py /path/to/GS011423.360
    python dump_gpmf.py /path/to/GS011423.360 --seconds 10
    python dump_gpmf.py /path/to/GS011423.360 --seconds 10 --fps 4

Without --fps: one CSV row per raw sensor sample.
With --fps N:  one CSV row per video frame — sensor samples within each frame
               window are averaged together (same logic gfm.py uses for extraction).

Output files:
    gpmf_raw.bin        - raw GPMF binary stream (for hex inspection)
    gpmf_accl.csv       - accelerometer  (m/s²)
    gpmf_gyro.csv       - gyroscope      (rad/s)
    gpmf_tags.txt       - every GPMF tag found
"""

import argparse
import struct
import subprocess
import tempfile
import os
import csv
import json
import math


def find_gpmf_stream(video_path):
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', video_path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    for s in json.loads(result.stdout).get('streams', []):
        if s.get('codec_tag_string') == 'gpmd':
            return s['index']
    return None


def extract_gpmf_bytes(video_path, stream_index, seconds=None):
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
        path = tmp.name
    cmd = ['ffmpeg', '-y', '-v', 'quiet', '-i', video_path,
           '-codec', 'copy', '-map', f'0:{stream_index}']
    if seconds is not None:
        cmd += ['-t', str(seconds)]
    cmd += ['-f', 'rawvideo', path]
    subprocess.run(cmd, check=True)
    with open(path, 'rb') as f:
        data = f.read()
    os.unlink(path)
    return data


def find_scale(data, before_pos):
    """Find the SCAL tag closest before before_pos, return list of floats."""
    pos = data.rfind(b'SCAL', max(0, before_pos - 600), before_pos)
    if pos < 0:
        return [1.0]
    t = chr(data[pos + 4])
    repeat = struct.unpack('>H', data[pos + 6:pos + 8])[0]
    vals = []
    if t == 's':
        for i in range(repeat):
            v = struct.unpack('>h', data[pos + 8 + i*2:pos + 10 + i*2])[0]
            vals.append(float(v) if v != 0 else 1.0)
    elif t == 'l':
        for i in range(repeat):
            v = struct.unpack('>i', data[pos + 8 + i*4:pos + 12 + i*4])[0]
            vals.append(float(v) if v != 0 else 1.0)
    return vals if vals else [1.0]


def parse_sensor(data, fourcc, struct_size, fmt):
    """Parse all blocks of a 3-component signed-short sensor (ACCL or GYRO)."""
    samples = []
    offset = 0
    while True:
        pos = data.find(fourcc.encode(), offset)
        if pos < 0:
            break
        try:
            t = chr(data[pos + 4])
            sz = data[pos + 5]
            repeat = struct.unpack('>H', data[pos + 6:pos + 8])[0]
            if t == 's' and sz == struct_size:
                scale = find_scale(data, pos)
                s0 = scale[0]
                s1 = scale[1] if len(scale) > 1 else s0
                s2 = scale[2] if len(scale) > 2 else s0
                for i in range(repeat):
                    base = pos + 8 + i * struct_size
                    if base + struct_size <= len(data):
                        c0, c1, c2 = struct.unpack(fmt, data[base:base + struct_size])
                        samples.append((c0 / s0, c1 / s1, c2 / s2))
        except Exception:
            pass
        offset = pos + 8
    return samples


def dump_all_tags(data):
    """Walk every GPMF KLV and return list of dicts."""
    tags = []
    offset = 0
    while offset + 8 <= len(data):
        try:
            fourcc = data[offset:offset + 4]
            if not all(0x20 <= b < 0x7f for b in fourcc):
                offset += 1
                continue
            t = chr(data[offset + 4])
            sz = data[offset + 5]
            repeat = struct.unpack('>H', data[offset + 6:offset + 8])[0]
            payload_len = sz * repeat
            # pad to 4-byte boundary
            padded = payload_len + (4 - payload_len % 4) % 4
            raw_preview = data[offset + 8:offset + 8 + min(16, payload_len)].hex()
            text_preview = ''
            if t == 'c':
                try:
                    text_preview = data[offset + 8:offset + 8 + payload_len].decode('ascii', errors='replace')
                except Exception:
                    pass
            tags.append({
                'fourcc': fourcc.decode('ascii', errors='replace'),
                'type': t,
                'size': sz,
                'repeat': repeat,
                'payload_bytes': payload_len,
                'raw_hex': raw_preview,
                'text': text_preview,
                'offset': offset,
            })
            offset += 8 + padded
        except Exception:
            offset += 1
    return tags


def average_window(samples, total_duration, frame_idx, fps):
    """
    Return the average of all samples whose time falls inside the window
    [frame_start, frame_end) for the given frame index at the given fps.
    Falls back to nearest sample if the window is empty.
    samples : list of tuples (all same length)
    """
    n = len(samples)
    if n == 0:
        return None
    sample_rate = n / total_duration          # samples per second
    frame_start = frame_idx / fps
    frame_end   = (frame_idx + 1) / fps

    lo = int(math.floor(frame_start * sample_rate))
    hi = int(math.ceil(frame_end   * sample_rate))
    lo = max(0, min(lo, n - 1))
    hi = max(lo + 1, min(hi, n))

    window = samples[lo:hi]
    if not window:
        window = [samples[max(0, min(lo, n - 1))]]

    ncols = len(window[0])
    return tuple(sum(row[c] for row in window) / len(window) for c in range(ncols))


def write_csv_raw(path, header, samples):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        for i, row in enumerate(samples):
            w.writerow([i] + [f'{v:.5f}' for v in row])


def write_csv_fps(path, header, samples, total_duration, fps):
    """One row per frame — averaged over the frame's sample window."""
    n_frames = int(math.floor(total_duration * fps))
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['frame_index', 'time_s'] + header)
        for fi in range(n_frames):
            t = fi / fps
            avg = average_window(samples, total_duration, fi, fps)
            if avg is None:
                continue
            w.writerow([fi, f'{t:.4f}'] + [f'{v:.5f}' for v in avg])
    return n_frames


def main():
    ap = argparse.ArgumentParser(description='Dump GPMF sensor data from a GoPro .360 file')
    ap.add_argument('video', help='Path to .360 file')
    ap.add_argument('--seconds', type=float, default=None,
                    help='Only extract the first N seconds (default: all)')
    ap.add_argument('--fps', type=float, default=None,
                    help='Resample to this frame rate: one CSV row per frame, '
                         'sensor samples averaged over each frame window. '
                         'Without this flag every raw sample is written.')
    ap.add_argument('--out-dir', default='.', help='Output directory (default: current dir)')
    args = ap.parse_args()

    print(f'Finding GPMF stream in {args.video} ...')
    stream = find_gpmf_stream(args.video)
    if stream is None:
        print('ERROR: no gpmd stream found'); return

    print(f'  → stream index {stream}')

    # Determine duration
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', args.video],
        capture_output=True, text=True, check=True)
    video_duration = None
    for s in json.loads(result.stdout).get('streams', []):
        if s.get('codec_type') == 'video':
            video_duration = float(s.get('duration', 0))
            break
    extract_seconds = args.seconds if args.seconds else video_duration
    print(f'  → video duration: {video_duration:.2f}s  extracting: {extract_seconds:.2f}s')

    print(f'Extracting GPMF binary data ...')
    data = extract_gpmf_bytes(args.video, stream, args.seconds)
    print(f'  → {len(data):,} bytes')

    os.makedirs(args.out_dir, exist_ok=True)

    # Raw binary
    raw_path = os.path.join(args.out_dir, 'gpmf_raw.bin')
    with open(raw_path, 'wb') as f:
        f.write(data)
    print(f'Written: {raw_path}')

    # Tags
    tags = dump_all_tags(data)
    tags_path = os.path.join(args.out_dir, 'gpmf_tags.txt')
    with open(tags_path, 'w') as f:
        f.write(f'{"FOURCC":<8} {"TYPE":<5} {"SIZE":<6} {"REPEAT":<8} {"BYTES":<8} {"OFFSET":<10} {"TEXT/HEX_PREVIEW"}\n')
        f.write('-' * 90 + '\n')
        for tag in tags:
            preview = tag['text'] if tag['text'] else tag['raw_hex']
            f.write(f'{tag["fourcc"]:<8} {tag["type"]:<5} {tag["size"]:<6} {tag["repeat"]:<8} '
                    f'{tag["payload_bytes"]:<8} {tag["offset"]:<10} {preview[:60]}\n')
    print(f'Written: {tags_path}  ({len(tags)} tags found)')

    # ACCL  (ORIO=ZXY: comp0=Z_cam/up, comp1=X_cam/right, comp2=Y_cam/forward)
    accl = parse_sensor(data, 'ACCL', 6, '>hhh')
    accl_header = ['comp0_Z_cam_m_s2', 'comp1_X_cam_m_s2', 'comp2_Y_cam_m_s2']
    accl_path = os.path.join(args.out_dir, 'gpmf_accl.csv')
    if args.fps:
        n = write_csv_fps(accl_path, accl_header, accl, extract_seconds, args.fps)
        print(f'Written: {accl_path}  ({len(accl)} raw samples → {n} frames @ {args.fps} fps)')
    else:
        write_csv_raw(accl_path, ['sample_index'] + accl_header, accl)
        rate = len(accl) / extract_seconds
        print(f'Written: {accl_path}  ({len(accl)} samples, ~{rate:.0f} Hz)')

    # GYRO
    gyro = parse_sensor(data, 'GYRO', 6, '>hhh')
    gyro_header = ['comp0_rad_s', 'comp1_rad_s', 'comp2_rad_s']
    gyro_path = os.path.join(args.out_dir, 'gpmf_gyro.csv')
    if args.fps:
        n = write_csv_fps(gyro_path, gyro_header, gyro, extract_seconds, args.fps)
        print(f'Written: {gyro_path}  ({len(gyro)} raw samples → {n} frames @ {args.fps} fps)')
    else:
        write_csv_raw(gyro_path, ['sample_index'] + gyro_header, gyro)
        rate = len(gyro) / extract_seconds
        print(f'Written: {gyro_path}  ({len(gyro)} samples, ~{rate:.0f} Hz)')

    print('\nDone.')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Calculate roll angle from GoPro .360 video accelerometer data.

This script extracts GPMF metadata from a GoPro .360 file, parses accelerometer
data, and calculates the average roll angle needed to level the horizon.

Usage:
    python calculate_roll_angle.py /path/to/video.360
    
The output is a single number (in degrees) that can be used with:
    python gfm.py --roll-angle <angle> video.360
"""

import argparse
import subprocess
import struct
import json
import os
import sys
import tempfile
import math
from typing import List, Dict, Optional


class GPMFAccelParser:
    """Parser for GoPro Metadata Format (GPMF) accelerometer data."""
    
    def __init__(self, data: bytes):
        self.data = data
        self.accel_samples = []
        
    def parse(self):
        """Parse GPMF data to extract accelerometer samples."""
        self._parse_accel()
        
    def _find_scale_factor(self, search_end: int, fourcc: str = 'SCAL') -> List[float]:
        """Find scale factor before a sensor data block."""
        scale = [1.0, 1.0, 1.0]
        scal_pos = self.data.rfind(fourcc.encode(), max(0, search_end - 300), search_end)
        
        if scal_pos >= 0:
            try:
                type_char = chr(self.data[scal_pos + 4]) if scal_pos + 4 < len(self.data) else ''
                struct_size = self.data[scal_pos + 5] if scal_pos + 5 < len(self.data) else 0
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
            except Exception as e:
                print(f"Warning: Could not parse scale factor: {e}", file=sys.stderr)
                
        return scale
    
    def _parse_accel(self):
        """Parse accelerometer data (ACCL fourcc)."""
        search_offset = 0
        
        while True:
            accl_pos = self.data.find(b'ACCL', search_offset)
            if accl_pos < 0:
                break
                
            try:
                accel_scale = self._find_scale_factor(accl_pos)
                type_char = chr(self.data[accl_pos + 4]) if accl_pos + 4 < len(self.data) else ''
                struct_size = self.data[accl_pos + 5] if accl_pos + 5 < len(self.data) else 0
                repeat_count = struct.unpack('>H', self.data[accl_pos + 6:accl_pos + 8])[0]
                
                if type_char == 's' and struct_size == 6:
                    data_start = accl_pos + 8
                    
                    for i in range(repeat_count):
                        offset = data_start + i * 6
                        if offset + 6 <= len(self.data):
                            raw_y = struct.unpack('>h', self.data[offset:offset + 2])[0]
                            raw_x = struct.unpack('>h', self.data[offset + 2:offset + 4])[0]
                            raw_z = struct.unpack('>h', self.data[offset + 4:offset + 6])[0]
                            
                            self.accel_samples.append({
                                'x': raw_x / accel_scale[1],
                                'y': raw_y / accel_scale[0],
                                'z': raw_z / accel_scale[2]
                            })
            except Exception as e:
                print(f"Warning: Error parsing ACCL at position {accl_pos}: {e}", file=sys.stderr)
                
            search_offset = accl_pos + 8


def find_gpmf_stream(video_path: str) -> Optional[int]:
    """Find the GPMF metadata stream index in the video."""
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
    except Exception as e:
        print(f"Error finding GPMF stream: {e}", file=sys.stderr)
        return None


def extract_gpmf_data(video_path: str, output_path: str) -> bool:
    """Extract GPMF metadata binary data from video."""
    stream_index = find_gpmf_stream(video_path)
    
    if stream_index is None:
        print(f"Error: No GPMF metadata stream found in {video_path}", file=sys.stderr)
        print("Make sure you're using the original .360 file, not a processed video.", file=sys.stderr)
        return False
    
    try:
        cmd = [
            'ffmpeg', '-y', '-v', 'quiet',
            '-i', video_path,
            '-codec', 'copy',
            '-map', f'0:{stream_index}',
            '-f', 'rawvideo',
            output_path
        ]
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error extracting GPMF data: {e}", file=sys.stderr)
        return False


def calculate_roll_angle(accel_samples: List[Dict]) -> float:
    """
    Calculate roll angle from accelerometer samples.
    
    The accelerometer measures gravity + motion. When the camera is steady
    or moving at constant velocity, the accelerometer mainly measures gravity,
    which points downward in the world frame.
    
    In camera coordinates:
    - X = right
    - Y = forward (optical axis)
    - Z = up
    
    When the camera is level, gravity should point along -Z (down).
    When rolled, gravity has components in X and Z.
    
    Roll angle = atan2(accel_x, -accel_z)
    
    We'll filter out high-motion samples and average over steady periods.
    """
    if not accel_samples:
        return 0.0
    
    # Calculate magnitude of each sample
    # Gravity is ~9.8 m/s², samples close to this are likely steady state
    gravity = 9.8
    steady_samples = []
    
    for sample in accel_samples:
        mag = math.sqrt(sample['x']**2 + sample['y']**2 + sample['z']**2)
        # Keep samples within 20% of gravity magnitude (filter out high acceleration)
        if 0.8 * gravity < mag < 1.2 * gravity:
            steady_samples.append(sample)
    
    if not steady_samples:
        print("Warning: No steady samples found, using all samples", file=sys.stderr)
        steady_samples = accel_samples
    
    # Average the steady samples
    avg_x = sum(s['x'] for s in steady_samples) / len(steady_samples)
    avg_y = sum(s['y'] for s in steady_samples) / len(steady_samples)
    avg_z = sum(s['z'] for s in steady_samples) / len(steady_samples)
    
    # Calculate roll from X and Z components
    # Roll is rotation around Y axis (forward)
    # When rolled right (+roll), gravity vector shifts toward +X
    roll_rad = math.atan2(avg_x, -avg_z)
    roll_deg = math.degrees(roll_rad)
    
    return roll_deg


def main():
    parser = argparse.ArgumentParser(
        description="Calculate roll angle from GoPro .360 accelerometer data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  %(prog)s /path/to/GS011406.360
  
Output:
  Roll angle in degrees (positive = camera rolled clockwise)
  
Use with gfm.py:
  python gfm.py --roll-angle $(python %(prog)s video.360) video.360
        """
    )
    
    parser.add_argument('video', help='Path to GoPro .360 file')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show detailed information')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.video):
        print(f"Error: File not found: {args.video}", file=sys.stderr)
        sys.exit(1)
    
    # Extract GPMF data
    if args.verbose:
        print(f"Extracting GPMF metadata from {args.video}...", file=sys.stderr)
    
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp_gpmf:
        gpmf_path = tmp_gpmf.name
    
    try:
        if not extract_gpmf_data(args.video, gpmf_path):
            sys.exit(1)
        
        # Parse accelerometer data
        if args.verbose:
            print("Parsing accelerometer data...", file=sys.stderr)
        
        with open(gpmf_path, 'rb') as f:
            gpmf_data = f.read()
        
        parser = GPMFAccelParser(gpmf_data)
        parser.parse()
        
        if args.verbose:
            print(f"Found {len(parser.accel_samples)} accelerometer samples", file=sys.stderr)
        
        if not parser.accel_samples:
            print("Error: No accelerometer data found in video", file=sys.stderr)
            sys.exit(1)
        
        # Calculate roll angle
        roll_angle = calculate_roll_angle(parser.accel_samples)
        
        if args.verbose:
            print(f"\nCalculated roll angle: {roll_angle:.2f}°", file=sys.stderr)
            print(f"\nUsage:", file=sys.stderr)
            print(f"  python gfm.py --roll-angle {roll_angle:.2f} {args.video}", file=sys.stderr)
            print(f"\nOr use command substitution:", file=sys.stderr)
            print(f"  python gfm.py --roll-angle $(python {sys.argv[0]} {args.video}) {args.video}", file=sys.stderr)
        else:
            # Just output the number for easy command substitution
            print(f"{roll_angle:.2f}")
        
    finally:
        if os.path.exists(gpmf_path):
            os.unlink(gpmf_path)


if __name__ == '__main__':
    main()

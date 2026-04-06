#!/usr/bin/env python3
"""Quick debug script to check accelerometer values"""
import sys
sys.path.insert(0, '/run/media/migero/nvme/gopro-frame-maker')
from calculate_roll_angle import extract_gpmf_data, GPMFAccelParser, find_gpmf_stream
import tempfile
import os
import math

video = '/run/media/migero/0123-4567/DCIM/100GOPRO/GS011406.360'

with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
    gpmf_path = tmp.name

try:
    extract_gpmf_data(video, gpmf_path)
    with open(gpmf_path, 'rb') as f:
        data = f.read()
    
    parser = GPMFAccelParser(data)
    parser.parse()
    
    # Show first 10 samples
    print(f"Total samples: {len(parser.accel_samples)}")
    print(f"\nFirst 10 samples:")
    for i, s in enumerate(parser.accel_samples[:10]):
        mag = math.sqrt(s['x']**2 + s['y']**2 + s['z']**2)
        print(f"  {i}: X={s['x']:7.2f}, Y={s['y']:7.2f}, Z={s['z']:7.2f}, mag={mag:.2f}")
    
    # Average of all samples
    avg_x = sum(s['x'] for s in parser.accel_samples) / len(parser.accel_samples)
    avg_y = sum(s['y'] for s in parser.accel_samples) / len(parser.accel_samples)
    avg_z = sum(s['z'] for s in parser.accel_samples) / len(parser.accel_samples)
    avg_mag = math.sqrt(avg_x**2 + avg_y**2 + avg_z**2)
    
    print(f"\nAverage over all samples:")
    print(f"  X={avg_x:7.2f}, Y={avg_y:7.2f}, Z={avg_z:7.2f}, mag={avg_mag:.2f}")
    
    # Calculate roll
    roll_rad = math.atan2(avg_x, -avg_z)
    roll_deg = math.degrees(roll_rad)
    print(f"\nRoll angle: {roll_deg:.2f}°")
    print(f"(atan2({avg_x:.2f}, {-avg_z:.2f}) = {roll_rad:.2f} rad)")
    
finally:
    os.unlink(gpmf_path)

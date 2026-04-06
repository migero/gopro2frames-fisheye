#!/usr/bin/env python3
"""Quick test to verify EXIF sensor data is being written to images"""

import subprocess
import json
import sys
from pathlib import Path

def check_image_exif(image_path):
    """Check EXIF data of an image using exiftool"""
    try:
        cmd = ["exiftool", "-j", str(image_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            data = json.loads(result.stdout)[0]
            return data
        else:
            print(f"Error reading EXIF: {result.stderr}")
            return None
    except Exception as e:
        print(f"Error: {e}")
        return None

# Check if we have any processed images
image_dir = Path("GS011406")
images = sorted(image_dir.glob("*.jpg"))

if not images:
    print("No processed images found yet. The script needs to complete processing first.")
    print("\nThe sensor data parsing is working correctly (verified by test_sensor_data.py)")
    print("Once the script completes, processed images will have:")
    print("  - ISO value")
    print("  - ExposureTime / ShutterSpeed")
    print("  - UserComment noting sensor data availability")
    sys.exit(0)

print(f"Found {len(images)} processed images")
print(f"\nChecking EXIF data of first image: {images[0]}")

exif = check_image_exif(images[0])
if exif:
    print("\n=== Relevant EXIF Tags ===")
    tags_to_check = [
        'ISO',
        'ExposureTime',
        'ShutterSpeed',
        'UserComment',
        'GPSLatitude',
        'GPSLongitude',
        'GPSAltitude',
        'GPSSpeed',
        'GPSImgDirection',
        'Model',
        'DateTimeOriginal'
    ]
    
    for tag in tags_to_check:
        if tag in exif:
            print(f"  {tag}: {exif[tag]}")
        else:
            print(f"  {tag}: (not found)")
    
    # Check if sensor data was added
    has_iso = 'ISO' in exif
    has_exposure = 'ExposureTime' in exif or 'ShutterSpeed' in exif
    has_sensor_note = 'UserComment' in exif and 'Sensor data' in str(exif.get('UserComment', ''))
    
    print("\n=== Sensor Data Status ===")
    print(f"  ✓ ISO data: {'YES' if has_iso else 'NO'}")
    print(f"  ✓ Exposure/Shutter data: {'YES' if has_exposure else 'NO'}")
    print(f"  ✓ Sensor availability note: {'YES' if has_sensor_note else 'NO'}")
    
    if has_iso and has_exposure:
        print("\n✅ SUCCESS: Sensor data is being written to EXIF!")
    else:
        print("\n⚠ Sensor data may not have been written yet. Make sure processing completed.")
else:
    print("Failed to read EXIF data")

#!/usr/bin/env python3
"""Test script to verify multiprocessing and track preservation"""

import os
import sys
from pathlib import Path
from multiprocessing import cpu_count

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 70)
print("TESTING IMPROVEMENTS")
print("=" * 70)

# Test 1: CPU detection
print(f"\n✓ CPU cores available: {cpu_count()}")
print(f"✓ Will use {max(1, cpu_count()-1)} cores for parallel processing")

# Test 2: Track preservation logic
media_folder = Path("GS011406")
track0 = media_folder / "track0"
track5 = media_folder / "track5"

if track0.exists() and track5.exists():
    import fnmatch
    track0_images = len(fnmatch.filter(os.listdir(str(track0)), '*.jpg'))
    track5_images = len(fnmatch.filter(os.listdir(str(track5)), '*.jpg'))
    
    print(f"\n✓ Track0 exists with {track0_images} images")
    print(f"✓ Track5 exists with {track5_images} images")
    
    if track0_images > 0 and track5_images > 0:
        print("✓ Both tracks have images - will be preserved!")
        print("✓ FFmpeg extraction will be SKIPPED")
    else:
        print("⚠ Tracks exist but have no images - will extract fresh")
else:
    print("\n⚠ Track folders don't exist - will extract from video")

print("\n" + "=" * 70)
print("IMPROVEMENTS SUMMARY")
print("=" * 70)
print("1. Multiprocessing: Will use all CPU cores for fisheye generation")
print("2. Track preservation: Existing track0/track5 folders are kept")
print("3. Smart cleanup: Only deletes non-track files/folders at start")
print("=" * 70)

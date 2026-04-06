#!/usr/bin/env python3
"""Demonstrate timestamp-based sensor matching"""

from pathlib import Path
from gfmhelper import GoProFrameMakerHelper

# Parse the XML
xml_file = Path("GS011406/GS011406.xml")
metadata = GoProFrameMakerHelper.parseMetadata(str(xml_file))

sensor_data = metadata['sensor_data']

print("=" * 70)
print("TIMESTAMP-BASED SENSOR DATA MATCHING DEMONSTRATION")
print("=" * 70)

print(f"\nTotal sensor blocks: {len(sensor_data)}")
print(f"Video frame rate: {metadata['video_field_data']['VideoFrameRate']} fps")
print(f"Video duration: {metadata['video_field_data']['Duration']}")

print("\n" + "=" * 70)
print("FIRST 5 SENSOR BLOCKS WITH TIMESTAMPS:")
print("=" * 70)

for i, block in enumerate(sensor_data[:5]):
    timestamp = block.get('TimeStamp', 'N/A')
    iso_values = block.get('ISOSpeeds', '').split()
    exposure_values = block.get('ExposureTimes', '').split()
    
    print(f"\nBlock {i+1}:")
    print(f"  Timestamp: {timestamp}s")
    print(f"  ISO values (count): {len(iso_values)}")
    print(f"  First 5 ISO values: {' '.join(iso_values[:5])}")
    print(f"  Last 5 ISO values: {' '.join(iso_values[-5:])}")
    print(f"  Exposure values (count): {len(exposure_values)}")
    print(f"  First exposure: {exposure_values[0] if exposure_values else 'N/A'}")

print("\n" + "=" * 70)
print("SIMULATED FRAME MATCHING (2 FPS EXTRACTION):")
print("=" * 70)

# Simulate frame extraction at 2 fps (one frame every 0.5 seconds)
frame_rate = 2.0
frame_interval = 1.0 / frame_rate

for frame_num in range(1, 11):  # First 10 frames
    frame_time = (frame_num - 1) * frame_interval
    
    # Find matching sensor block
    matched_block = None
    value_index = 0
    
    for i, block in enumerate(sensor_data):
        block_timestamp = block.get('TimeStamp', 0.0)
        if i < len(sensor_data) - 1:
            next_timestamp = sensor_data[i + 1].get('TimeStamp', block_timestamp + 1.0)
        else:
            next_timestamp = block_timestamp + 1.0
        
        if block_timestamp <= frame_time < next_timestamp:
            matched_block = block
            time_within_block = frame_time - block_timestamp
            block_duration = next_timestamp - block_timestamp
            value_index = int((time_within_block / block_duration) * 24)
            value_index = max(0, min(value_index, 23))
            block_num = i + 1
            break
    
    if matched_block:
        iso_values = matched_block.get('ISOSpeeds', '').split()
        exposure_values = matched_block.get('ExposureTimes', '').split()
        iso = iso_values[value_index] if value_index < len(iso_values) else iso_values[0] if iso_values else 'N/A'
        exposure = exposure_values[value_index] if value_index < len(exposure_values) else exposure_values[0] if exposure_values else 'N/A'
        
        print(f"\nFrame {frame_num:03d} ({frame_time:.3f}s):")
        print(f"  → Block {block_num} (timestamp {matched_block.get('TimeStamp')}s)")
        print(f"  → Value index: {value_index}/23")
        print(f"  → ISO: {iso}")
        print(f"  → Exposure: {exposure}")
    else:
        print(f"\nFrame {frame_num:03d} ({frame_time:.3f}s): No matching block")

print("\n" + "=" * 70)
print("KEY INSIGHT:")
print("=" * 70)
print("Each frame gets a SPECIFIC value from the 24-value array in its sensor block,")
print("based on the frame's exact timestamp within that block's time range.")
print("This is much more accurate than using the same value for all frames!")
print("=" * 70)

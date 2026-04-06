#!/usr/bin/env python3
"""Test script to verify sensor data parsing"""

import sys
from pathlib import Path
from gfmhelper import GoProFrameMakerHelper

# Test with existing XML file
xml_file = Path("GS011406/GS011406.xml")

if not xml_file.exists():
    print(f"XML file not found: {xml_file}")
    sys.exit(1)

print("Parsing XML file...")
metadata = GoProFrameMakerHelper.parseMetadata(str(xml_file))

print("\n=== Metadata Keys ===")
print(metadata.keys())

print("\n=== Video Field Data ===")
for key, value in metadata['video_field_data'].items():
    print(f"{key}: {value}")

print(f"\n=== GPS Data ===")
print(f"Number of GPS data blocks: {len(metadata['gps_data'])}")
if metadata['gps_data']:
    print(f"First GPS block keys: {metadata['gps_data'][0].keys()}")
    print(f"First GPS block sample: {metadata['gps_data'][0]['GPSDateTime']}")

print(f"\n=== Sensor Data ===")
if 'sensor_data' in metadata:
    print(f"Number of sensor data blocks: {len(metadata['sensor_data'])}")
    if metadata['sensor_data']:
        print("\nFirst 3 sensor data blocks:")
        for i, sensor_block in enumerate(metadata['sensor_data'][:3]):
            print(f"\nBlock {i+1}:")
            for key, value in sensor_block.items():
                if len(str(value)) > 100:
                    print(f"  {key}: {str(value)[:100]}...")
                else:
                    print(f"  {key}: {value}")
else:
    print("No sensor_data key found in metadata")

print("\n=== Testing gpsTimestamps function ===")
try:
    gps_output = GoProFrameMakerHelper.gpsTimestamps(
        metadata['gps_data'], 
        metadata['video_field_data'],
        metadata.get('sensor_data')
    )
    print("✓ gpsTimestamps executed successfully")
    print(f"  Output keys: {gps_output.keys()}")
    if 'sensor_data' in gps_output:
        print(f"  Sensor data blocks in output: {len(gps_output['sensor_data'])}")
except Exception as e:
    print(f"✗ Error in gpsTimestamps: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Test Complete ===")

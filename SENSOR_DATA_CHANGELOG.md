# Sensor Data EXIF Enhancement

## Summary
Added support for extracting and embedding accelerometer, gyroscope, ISO, and shutter speed data from GoPro 360 videos into the EXIF metadata of extracted frames.

## Changes Made

### 1. **gfmmain.py**
- Added sensor data field parsing in `__parseMetadata()`:
  - Added `sensorFields` list to track: `ExposureTimes`, `ISOSpeeds`, `Accelerometer`, `Gyroscope`
  - Parse sensor data from XML metadata (Track4)
  - Return sensor data alongside GPS and video field data

- Enhanced `__updateImagesMetadata()` to write sensor data to EXIF:
  - Match sensor data to frames based on frame index
  - Write ISO value to `-ISO` tag
  - Write shutter speed to `-ExposureTime` and `-ShutterSpeed` tags
  - Add note about accelerometer/gyroscope presence in `-UserComment` tag

### 2. **gfmhelper.py**
- Updated `parseMetadata()` to parse sensor data from XML
  - Same sensor field parsing as in gfmmain.py
  - Return sensor data in metadata dictionary

- Updated `gpsTimestamps()` function signature:
  - Added optional `sensorData` parameter (default: None)
  - Return sensor data in output dictionary

- Fixed Duration parsing to handle both time formats:
  - HH:MM:SS format (from XML)
  - Seconds format (from other sources)

## Data Format

### Sensor Data Structure
```python
{
    'ExposureTimes': '1/192 1/192 1/192 ...',  # Space-separated shutter speeds
    'ISOSpeeds': '2118 2118 2112 ...',         # Space-separated ISO values
    'Accelerometer': 'present',                 # Binary data indicator
    'Gyroscope': 'present'                      # Binary data indicator
}
```

### EXIF Tags Added to Images
- **ISO**: Camera ISO sensitivity (from first value in ISOSpeeds list)
- **ExposureTime**: Shutter speed in fractional format (e.g., "1/192")
- **ShutterSpeed**: Same as ExposureTime
- **UserComment**: Note about available sensor data ("Sensor data available: Accelerometer, Gyroscope")

## Testing

Run the test script to verify sensor data parsing:
```bash
source env/bin/activate
python test_sensor_data.py
```

Run the main script to process a video:
```bash
source env/bin/activate
python gfm.py -w 2800 /path/to/video.360
```

## Notes

1. **Sensor Data Matching**: Sensor data blocks are matched to frames by index. Since sensor data is sampled less frequently than frames (~1 second intervals in the metadata), the same sensor values may be applied to multiple consecutive frames.

2. **Binary Data**: Accelerometer and gyroscope data in the XML is stored as binary data. The current implementation notes their presence but doesn't extract the raw values. To extract actual accelerometer/gyroscope readings, you would need to use exiftool with the `-b` option to extract binary data, then parse the binary format.

3. **ISO and Shutter Speed**: These are lists of values (24 values per second in the test video). The first value from each block is used for frames within that time period.

## Future Enhancements

Potential improvements:
- Parse binary accelerometer/gyroscope data to extract actual XYZ values
- Better interpolation of sensor data between samples
- Add temperature data (also available in Track4:CameraTemperature)
- Add magnetometer data
- Add white balance RGB values
- Create custom EXIF tags for raw accelerometer/gyroscope arrays

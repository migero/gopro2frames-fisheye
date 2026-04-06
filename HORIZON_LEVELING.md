# Horizon Leveling for Fisheye Images

## Overview

The GoPro Frame Maker now supports horizon leveling for fisheye images using the `--roll-angle` parameter. This rotates the fisheye image around its optical axis to correct for camera tilt.

## Usage

### Manual Roll Angle

If you know the camera was tilted (e.g., mounted at an angle), you can specify the roll angle manually:

```bash
python gfm.py -w 2800 -r 6 --roll-angle 15.0 /path/to/video.360
```

- Positive angles = clockwise rotation when looking through the lens
- Negative angles = counter-clockwise rotation
- Typical range: -45° to +45°

### Examples

**Camera tilted 10° to the right:**
```bash
python gfm.py -w 2800 --roll-angle 10.0 video.360
```

**Camera tilted 15° to the left:**
```bash
python gfm.py -w 2800 --roll-angle -15.0 video.360
```

## How Roll Angle Works

The roll angle rotates the fisheye coordinate system before projection:

1. **Without roll correction** (roll=0°): The fisheye is rendered exactly as the camera saw it
2. **With roll correction** (roll≠0°): The fisheye is rotated so "up" aligns with world vertical

The rotation is applied during lookup table generation for optimal quality (no interpolation artifacts).

## Determining the Correct Roll Angle

### Method 1: Visual Inspection

1. Generate fisheyes without correction: `--roll-angle 0`
2. Look at the horizon in the image
3. Estimate the tilt angle
4. Re-run with correction: `--roll-angle <estimated_angle>`

### Method 2: From Accelerometer Data (Future Enhancement)

The accelerometer measures the gravity vector, which points "down" in world coordinates. By analyzing accelerometer data from the video metadata, we can calculate the exact roll angle needed.

**Current Status:** The XML metadata shows accelerometer data is present but as "Binary data". To properly parse it:

1. Extract GPMF binary stream from the .360 file (similar to `extract_motion_vectors.py`)
2. Parse accelerometer samples
3. Calculate average gravity direction
4. Compute roll angle from gravity vector

**Implementation Plan:**
```python
# Pseudo-code for future enhancement
def calculate_roll_from_accelerometer(video_file):
    # Extract GPMF data
    gpmf_data = extract_gpmf(video_file)
    
    # Parse accelerometer (measures gravity when stationary)
    accel_samples = parse_accelerometer(gpmf_data)
    
    # Average over steady portions
    avg_x = mean([s['x'] for s in accel_samples])
    avg_y = mean([s['y'] for s in accel_samples])
    
    # Calculate roll from horizontal components
    # roll = atan2(avg_y, avg_x)
    roll_rad = atan2(avg_y, avg_x)
    roll_deg = degrees(roll_rad)
    
    return roll_deg
```

### Method 3: From Reference Frame

If you have a frame with a visible horizon:
1. Load the fisheye image
2. Measure the angle of the horizon line
3. Use that as your roll angle

## Performance Notes

- Lookup tables are cached with roll angle in the filename
- Different roll angles create different cache files
- Cache format: `fisheye_lut_<template>_<size>_<aa>_roll<angle>.npz`
- Example: `fisheye_lut_0_2800_2_roll15.npz` for 15° correction

## Technical Details

The roll rotation is applied in the fisheye coordinate system:

```python
# For each pixel (x_c, y_c) in centered coordinates [-1, 1]:
roll_rad = radians(roll_angle)
x_rotated =  x_c * cos(roll_rad) + y_c * sin(roll_rad)
y_rotated = -x_c * sin(roll_rad) + y_c * cos(roll_rad)
```

This rotates the sampling grid, so the final fisheye image appears rotated by `roll_angle` degrees around its center.

## Coordinate System

- **GoPro Max camera coordinates:**
  - +X = Right (when holding camera normally)
  - +Y = Forward (lens pointing direction)
  - +Z = Up
  
- **Roll angle:**
  - 0° = Camera level (Z-axis vertical)
  - +θ = Camera rolled clockwise around Y-axis (optical axis)
  - -θ = Camera rolled counter-clockwise

- **Accelerometer in steady state:**
  - Measures gravity vector (points down in world frame)
  - When camera is level: accel = [0, 0, -9.8] m/s²
  - When rolled: gravity has X,Y components proportional to tilt

## See Also

- [extract_motion_vectors.py](../projects/gopro360-converter/360_motion_from_sensors/extract_motion_vectors.py) - Example of parsing GPMF accelerometer/gyro data
- [max2sphere.py](max2sphere/max2sphere.py) - Fisheye projection with roll correction

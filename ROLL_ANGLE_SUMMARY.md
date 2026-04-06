# Roll Angle Calculation - Summary

## What We Added

### 1. Horizon Leveling Support in max2sphere.py
- Added `roll_angle` parameter to `build_lookup_table()` and `process_frame()`
- Rotation is applied to fisheye coordinates before projection
- Lookup tables are cached with roll angle in filename

### 2. Command-Line Support in gfm.py
- New parameter: `--roll-angle <degrees>`
- Example: `python gfm.py -w 2800 --roll-angle 15.0 video.360`

### 3. Automatic Roll Calculation (calculate_roll_angle.py)
- Extracts GPMF accelerometer data from .360 files
- Calculates average roll angle from gravity vector
- **Limitation**: Only works well for steady/stationary videos

## Testing Results

Tested with GS011406.360:
- Found 155,764 accelerometer samples
- Average values: X=-5.58, Y=-3.11, Z=4.46 m/s²
- Calculated roll: -128.6°

**Issue**: The magnitude (7.79 m/s²) is less than gravity (9.8 m/s²), indicating the camera was in motion (skiing, biking, etc.). This makes accelerometer-only roll calculation unreliable.

## Recommendations

### For Steady Videos (tripod, static mount):
```bash
# Calculate roll automatically
ROLL=$(python calculate_roll_angle.py video.360)
python gfm.py -w 2800 --roll-angle $ROLL video.360
```

### For Action Videos (skiing, biking, moving):
1. **Manual Method** (Recommended):
   - Extract a sample frame: `python gfm.py -w 2800 --roll-angle 0 video.360`
   - Look at the horizon in one of the fisheye images
   - Estimate the tilt angle visually
   - Re-run with correction: `python gfm.py -w 2800 --roll-angle <angle> video.360`

2. **Future Enhancement**: Implement gyroscope integration
   - Use gyroscope data to track orientation changes over time
   - Combine with accelerometer using sensor fusion (Kalman filter)
   - Generate per-frame roll angles for dynamic leveling

## Examples

### Camera Tilted 15° Clockwise:
```bash
python gfm.py -w 2800 -r 6 --roll-angle 15.0 /path/to/video.360
```

### Camera Tilted 10° Counter-Clockwise:
```bash
python gfm.py -w 2800 -r 6 --roll-angle -10.0 /path/to/video.360
```

### Test Different Angles:
```bash
# Try a few angles to see which levels the horizon best
for angle in -15 -10 -5 0 5 10 15; do
  echo "Testing roll angle: ${angle}°"
  python gfm.py -w 1400 -r 1 --roll-angle $angle video.360
done
```

## How It Works

The roll angle rotates the fisheye sampling grid:

```
1. Fisheye pixel (x, y) → normalized (-1, 1) coordinates
2. Apply rotation: x' = x*cos(θ) + y*sin(θ)
                   y' = -x*sin(θ) + y*cos(θ)
3. Project rotated coordinates to sphere
4. Sample from source frames
```

This is much better than rotating the final image because:
- No interpolation artifacts
- Full fisheye circle is preserved
- Can be cached in lookup table

## Files Modified

1. **max2sphere/max2sphere.py**
   - Added `roll_angle` parameter
   - Applied rotation in lookup table generation
   - Updated cache filename to include roll angle

2. **gfm.py**
   - Added `--roll-angle` argument
   - Passed to processing pipeline

3. **gfmmain.py**
   - Updated `_process_fisheye_frame()` to accept roll_angle
   - Modified `__breakIntoFrames360()` to pass roll_angle
   - Added placeholder `calculate_roll_angle_from_xml()`

4. **New Files**
   - `calculate_roll_angle.py` - Extract roll from accelerometer
   - `HORIZON_LEVELING.md` - User documentation
   - `debug_accel.py` - Debug script for checking sensor data

## Next Steps (Future Enhancements)

1. **Gyroscope Integration**
   - Parse gyro data from GPMF
   - Integrate angular velocity to get orientation
   - Apply sensor fusion for accurate roll tracking

2. **Per-Frame Roll Correction**
   - Calculate roll for each frame individually
   - Handle dynamic orientation changes
   - Useful for action sports videos

3. **Automatic Horizon Detection**
   - Use computer vision to detect horizon line
   - Calculate roll from image analysis
   - Fallback when IMU data is unreliable

## Usage Summary

**Current command (without roll correction):**
```bash
python gfm.py -w 2800 -r 6 /run/media/migero/0123-4567/DCIM/100GOPRO/GS011406.360
```

**With manual roll correction:**
```bash
python gfm.py -w 2800 -r 6 --roll-angle 15.0 /run/media/migero/0123-4567/DCIM/100GOPRO/GS011406.360
```

**Performance:**
- Separate lookup table cached for each roll angle
- Example: `fisheye_lut_0_2800_2_roll15.npz`
- First run: Builds lookup table (~38 seconds)
- Subsequent runs: Loads from cache (instant)

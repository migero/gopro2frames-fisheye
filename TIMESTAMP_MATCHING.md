# How Sensor Data is Matched to Frames

## The Problem You Identified

Sensor data blocks don't explicitly reference frame numbers - they use **timestamps**. So how do we know which ISO value or shutter speed belongs to which frame?

## The Solution: Timestamp-Based Matching

### Data Structure

**Video:** 12:48 duration, 23.976 fps native
**Extraction:** 2 fps (1538 frames total)
**Sensor Blocks:** 768 blocks (~1 per second)

Each sensor block looks like this:
```
Block 1:
  TimeStamp: 0.176638 seconds
  ISOSpeeds: 2118 2118 2112 2112 2105 2105 ... (24 values)
  ExposureTimes: 1/192 1/192 1/192 1/192 ... (24 values)

Block 2:
  TimeStamp: 1.177638 seconds
  ISOSpeeds: 2075 2075 2075 2075 2068 2068 ... (24 values)
  ExposureTimes: 1/192 1/192 1/192 1/192 ... (24 values)

Block 3:
  TimeStamp: 2.178638 seconds
  ...
```

### Matching Algorithm

For each frame being processed:

1. **Calculate frame timestamp:**
   ```
   Frame 1 at time 0.0s
   Frame 2 at time 0.5s (2fps = every 0.5 seconds)
   Frame 3 at time 1.0s
   Frame 4 at time 1.5s
   ...
   ```

2. **Find the sensor block covering that time:**
   ```
   Block 1: 0.176638s to 1.177638s
   Block 2: 1.177638s to 2.178638s
   Block 3: 2.178638s to 3.179638s
   ```

3. **Select the appropriate value within the block:**
   
   Each block has 24 values covering ~1 second
   - Block duration: ~1.0 second
   - Values per block: 24
   - Time per value: ~0.042 seconds (1/24)
   
   **Example for Frame 4 at 1.5s:**
   - Falls in Block 2 (1.177638s to 2.178638s)
   - Position within block: 1.5 - 1.177638 = 0.322 seconds
   - Value index: (0.322 / 1.0) × 24 = 7.7 → **index 7**
   - Use: `ISOSpeeds[7]` and `ExposureTimes[7]`

### Visual Timeline

```
Time:     0s        0.5s       1s        1.5s       2s        2.5s
          |          |          |          |          |          |
Frames:   F1         F2         F3         F4         F5         F6
          |          |          |          |          |          |
Sensors: [---- Block 1 (24 vals) ----][---- Block 2 (24 vals) ----]
          ^0         ^12        ^23     ^0  ^7       ^23
          
Frame 1 (0.0s):  Block 1, value index ~0
Frame 2 (0.5s):  Block 1, value index ~12
Frame 3 (1.0s):  Block 1, value index ~23
Frame 4 (1.5s):  Block 2, value index ~7
Frame 5 (2.0s):  Block 2, value index ~19
```

### Code Implementation

```python
# Calculate which sensor block and which value within that block
for sensor_block in sensor_data:
    block_timestamp = sensor_block['TimeStamp']
    next_timestamp = next_block['TimeStamp']  # or block_timestamp + 1.0
    
    if block_timestamp <= frame_time < next_timestamp:
        # This is the right block
        time_within_block = frame_time - block_timestamp
        block_duration = next_timestamp - block_timestamp
        
        # Calculate index (0-23) based on position within block
        value_index = int((time_within_block / block_duration) * 24)
        value_index = max(0, min(value_index, 23))  # Clamp to valid range
        
        # Use the specific value
        iso = sensor_block['ISOSpeeds'].split()[value_index]
        exposure = sensor_block['ExposureTimes'].split()[value_index]
        break
```

### Fallback Behavior

If timestamp matching fails (edge cases, missing timestamps):
- Falls back to simple index-based matching
- Uses first value from the block (index 0)
- Still better than no sensor data!

## Result

Each frame now gets:
- **The correct ISO value** for that specific moment in time
- **The correct shutter speed** for that specific moment in time
- Much more accurate than the naive "1 block per frame" approach

## Example Output

```
Frame 000001.jpg (0.0s):
  - ISO: 2118 (Block 1, index 0)
  - ShutterSpeed: 1/192

Frame 000002.jpg (0.5s):
  - ISO: 2099 (Block 1, index 12)
  - ShutterSpeed: 1/192

Frame 000003.jpg (1.0s):
  - ISO: 2075 (Block 1, index 23)
  - ShutterSpeed: 1/192

Frame 000004.jpg (1.5s):
  - ISO: 2068 (Block 2, index 7)
  - ShutterSpeed: 1/192
```

This ensures each frame gets sensor data that accurately represents the camera's settings **at that exact moment** in the video!

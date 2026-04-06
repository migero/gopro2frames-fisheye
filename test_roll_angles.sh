#!/bin/bash
# Test horizon leveling with different roll angles

VIDEO="/run/media/migero/0123-4567/DCIM/100GOPRO/GS011406.360"
OUTPUT_DIR="/run/media/migero/nvme/gopro-frame-maker/roll_angle_test"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Test a few different roll angles
# Extract just one frame at 1 fps to speed up testing
echo "Testing different roll angles..."
echo "Extracting 1 frame with different roll corrections:"
echo ""

for ANGLE in 0 10 -10 15 -15; do
    echo "Testing roll angle: ${ANGLE}°"
    
    # Create a subdirectory for this test
    TEST_DIR="${OUTPUT_DIR}/roll_${ANGLE}"
    mkdir -p "$TEST_DIR"
    
    # Run with this roll angle (extract just 1 second worth of frames)
    # Redirect to the test directory
    cd "$TEST_DIR"
    
    python /run/media/migero/nvme/gopro-frame-maker/gfm.py \
        -w 1400 \
        -r 0.5 \
        --roll-angle $ANGLE \
        "$VIDEO" <<< "y" 2>&1 | grep -E "(Building|Rendering|roll)"
    
    echo "  → Fisheye images saved to: $TEST_DIR"
    echo ""
done

echo "Test complete!"
echo "Compare the fisheye images in subdirectories of: $OUTPUT_DIR"
echo ""
echo "To view:"
echo "  ls -R $OUTPUT_DIR/*/lens*.jpg"

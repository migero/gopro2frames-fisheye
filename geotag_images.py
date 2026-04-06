#!/usr/bin/env python3
"""
Standalone script to geotag fisheye images from GoPro Max videos.
Handles both front (NNNNNN.jpg) and back (lens1_NNNNNN.jpg) fisheye images.
"""

import os
import sys
import re
import argparse
import subprocess
import datetime
import shutil
import platform
from pathlib import Path


def _print_progress(current, total, prefix='', bar_width=45):
    """Print an in-place ASCII progress bar."""
    filled = int(bar_width * current / max(total, 1))
    bar = '█' * filled + '░' * (bar_width - filled)
    pct = current / max(total, 1) * 100
    print(f'\r{prefix} |{bar}| {current}/{total} ({pct:.1f}%)', end='', flush=True)
    if current >= total:
        print()


def find_exiftool():
    """Find exiftool executable"""
    # Try to find exiftool in PATH first (for conda environments on Windows)
    exiftool = shutil.which("exiftool")
    if exiftool:
        return exiftool
    
    # Fall back to platform-specific defaults
    if platform.system() == "Windows":
        return "exiftool.exe"
    else:
        return "exiftool"


def get_frame_number(filename):
    """Extract frame number from filename"""
    basename = os.path.basename(filename)

    # Support both old and new naming conventions:
    # lens0_000001.jpg, lens1_000001.jpg, front_000001.jpg, back_000001.jpg, 000001.jpg
    patterns = [
        (r'lens0_(\d+)\.jpg', 'front'),
        (r'front_(\d+)\.jpg', 'front'),
        (r'lens1_(\d+)\.jpg', 'back'),
        (r'back_(\d+)\.jpg', 'back'),
        (r'(\d+)\.jpg', 'front'),
    ]

    for pattern, img_type in patterns:
        match = re.match(pattern, basename, re.IGNORECASE)
        if match:
            return int(match.group(1)), img_type

    return None, None


def calculate_timestamp(frame_number, fps, start_time):
    """Calculate timestamp for a given frame number"""
    # Frame numbers are 1-based
    seconds_offset = (frame_number - 1) / fps
    frame_time = start_time + datetime.timedelta(seconds=seconds_offset)
    return frame_time


def format_timestamp_for_exif(dt):
    """Format datetime for EXIF tags"""
    # DateTimeOriginal format: "2020:04:13 15:37:22"
    date_time = dt.strftime("%Y:%m:%d %H:%M:%S")
    # SubSecTimeOriginal: milliseconds "444"
    subsec = dt.strftime("%f")[:3]
    # SubSecDateTimeOriginal: "2020:04:13T15:37:22.444Z"
    subsec_dt = dt.strftime("%Y:%m:%dT%H:%M:%S") + f".{subsec}Z"
    
    return date_time, subsec, subsec_dt


def find_gpx_file(folder):
    """Find the GPX file in the folder"""
    folder_path = Path(folder)
    gpx_files = list(folder_path.glob("*_video.gpx"))
    
    if not gpx_files:
        print(f"Error: No *_video.gpx file found in {folder}")
        return None
    
    if len(gpx_files) > 1:
        print(f"Warning: Multiple GPX files found, using: {gpx_files[0]}")
    
    return str(gpx_files[0])


def find_exiftool_config():
    """Find the .ExifTool_config file"""
    # Check in script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, ".ExifTool_config")
    
    if os.path.exists(config_path):
        return config_path
    
    # Check in current directory
    if os.path.exists(".ExifTool_config"):
        return os.path.abspath(".ExifTool_config")
    
    return None


def get_video_start_time(folder):
    """Try to extract video start time from XML metadata"""
    folder_path = Path(folder)
    xml_files = list(folder_path.glob("*.xml"))
    
    if not xml_files:
        print("Warning: No XML metadata file found. Using current time as start.")
        return datetime.datetime.now()
    
    try:
        with open(xml_files[0], 'r', encoding='utf-8') as f:
            xml_content = f.read()
        
        # Try to find CreateDate or GPSDateTime
        import re
        pattern = r'<Track4:GPSDateTime>([^<]+)</Track4:GPSDateTime>'
        matches = re.findall(pattern, xml_content)
        
        if matches:
            time_str = matches[0].strip()
            try:
                return datetime.datetime.strptime(time_str, "%Y:%m:%d %H:%M:%S.%f")
            except:
                return datetime.datetime.strptime(time_str, "%Y:%m:%d %H:%M:%S")
    except Exception as e:
        print(f"Warning: Could not parse XML metadata: {e}")
    
    return datetime.datetime.now()


def geotag_images(folder_path, fps, start_time_override=None):
    """Geotag all images in the folder"""
    
    # Find GPX file
    gpx_file = find_gpx_file(folder_path)
    if not gpx_file:
        return False
    
    print(f"Using GPX file: {gpx_file}")
    
    # Get video start time
    if start_time_override:
        start_time = start_time_override
    else:
        start_time = get_video_start_time(folder_path)
    
    print(f"Video start time: {start_time}")
    print(f"Frame rate: {fps} fps")
    
    # Find all image files
    folder = Path(folder_path)
    all_images = []
    
    front_folder = folder / 'front'
    back_folder = folder / 'back'
    
    if front_folder.exists() or back_folder.exists():
        # New layout: front/ and back/ subfolders
        if front_folder.exists():
            for img in sorted(front_folder.glob("*.jpg")):
                frame_num, img_type = get_frame_number(img.name)
                if frame_num is not None and img_type == 'front':
                    all_images.append((str(img), frame_num, 'front'))
        if back_folder.exists():
            for img in sorted(back_folder.glob("*.jpg")):
                frame_num, img_type = get_frame_number(img.name)
                if frame_num is not None and img_type == 'back':
                    all_images.append((str(img), frame_num, 'back'))
    else:
        # Legacy layout: both in same folder
        for img in sorted(folder.glob("*.jpg")):
            frame_num, img_type = get_frame_number(img.name)
            if frame_num is not None and img_type in ('front', 'back'):
                all_images.append((str(img), frame_num, img_type))
    
    if not all_images:
        print(f"Error: No fisheye images found in {folder_path}")
        return False
    
    all_images.sort(key=lambda x: x[1])  # Sort by frame number
    
    print(f"\nFound {len(all_images)} images to geotag")
    print(f"  Frame range: {all_images[0][1]} to {all_images[-1][1]}")
    
    # Find exiftool executable and config
    exiftool_exe = find_exiftool()
    config_path = find_exiftool_config()
    
    print(f"Using exiftool: {exiftool_exe}")
    if config_path:
        print(f"Using config: {config_path}")
    
    # Step 1: Add timestamps to all images in chunks for visible progress
    print(f"\nStep 1: Adding timestamps to {len(all_images)} images...")
    
    import tempfile
    CHUNK_SIZE = 100
    total = len(all_images)
    errors = 0

    for chunk_start in range(0, total, CHUNK_SIZE):
        chunk = all_images[chunk_start:chunk_start + CHUNK_SIZE]
        chunk_end = chunk_start + len(chunk)

        # Build an argfile for this chunk using -execute between images
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as argfile:
                argfile_path = argfile.name
                for img_path, frame_num, img_type in chunk:
                    frame_time = calculate_timestamp(frame_num, fps, start_time)
                    date_time, subsec, subsec_dt = format_timestamp_for_exif(frame_time)
                    argfile.write(f"-DateTimeOriginal={date_time}Z\n")
                    argfile.write(f"-SubSecTimeOriginal={subsec}\n")
                    argfile.write(f"-SubSecDateTimeOriginal={subsec_dt}\n")
                    argfile.write(f"-overwrite_original\n")
                    argfile.write(f"{img_path}\n")
                    argfile.write(f"-execute\n")

            cmd = [exiftool_exe]
            if config_path:
                cmd.extend(["-config", config_path])
            cmd.extend(["-@", argfile_path])

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                errors += 1
                if result.stderr:
                    print(f"  Warning (chunk {chunk_start}-{chunk_end}): {result.stderr[:200]}")
        finally:
            try:
                os.unlink(argfile_path)
            except:
                pass

        _print_progress(chunk_end, total, prefix='  Timestamps')

    print(f"✓ Added timestamps to {total} images" + (f" ({errors} chunk errors)" if errors else ""))
    
    # Step 2: Geotag all images using GPX file
    print("\nStep 2: Geotagging images with GPS data from GPX file...")
    
    front_folder = Path(folder_path) / 'front'
    back_folder = Path(folder_path) / 'back'
    
    if front_folder.exists() or back_folder.exists():
        # New layout: geotag each subfolder separately
        targets = []
        if front_folder.exists():
            targets.append((str(front_folder), 'front'))
        if back_folder.exists():
            targets.append((str(back_folder), 'back'))
        
        for i, (target, label) in enumerate(targets):
            n_imgs = len(list(Path(target).glob('*.jpg')))
            print(f"  Geotagging {label}/ ({n_imgs} images)...", flush=True)
            cmd = [exiftool_exe]
            if config_path:
                cmd.extend(["-config", config_path])
            cmd.extend([
                "-geotag", gpx_file,
                "-geotime<${subsecdatetimeoriginal}",
                "-overwrite_original",
                target
            ])
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Error during geotagging {target}:")
                print(f"STDOUT: {result.stdout}")
                print(f"STDERR: {result.stderr}")
                return False
            _print_progress(i + 1, len(targets), prefix=f'  GPS tag   ')
        print("✓ Successfully geotagged all images!")
        return True
    else:
        # Legacy layout: geotag root folder
        n_imgs = len([f for f in Path(folder_path).glob('*.jpg')])
        print(f"  Geotagging {n_imgs} images...", flush=True)
        cmd = [exiftool_exe]
        if config_path:
            cmd.extend(["-config", config_path])
        cmd.extend([
            "-geotag", gpx_file,
            "-geotime<${subsecdatetimeoriginal}",
            "-overwrite_original",
            str(folder_path)
        ])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            _print_progress(1, 1, prefix='  GPS tag   ')
            print("✓ Successfully geotagged all images!")
            return True
        else:
            print(f"Error during geotagging:")
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            return False


def main():
    parser = argparse.ArgumentParser(
        description="Geotag fisheye images from GoPro Max videos using existing GPX file"
    )
    parser.add_argument(
        "folder",
        type=str,
        help="Folder containing the fisheye images and GPX file"
    )
    parser.add_argument(
        "-r", "--fps",
        type=float,
        required=True,
        help="Frame rate (frames per second) used to extract the images"
    )
    parser.add_argument(
        "--start-time",
        type=str,
        help="Override video start time (format: 'YYYY-MM-DD HH:MM:SS')"
    )
    
    args = parser.parse_args()
    
    # Validate folder exists
    if not os.path.exists(args.folder):
        print(f"Error: Folder not found: {args.folder}")
        sys.exit(1)
    
    # Parse start time if provided
    start_time = None
    if args.start_time:
        try:
            start_time = datetime.datetime.strptime(args.start_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print(f"Error: Invalid start time format. Use: YYYY-MM-DD HH:MM:SS")
            sys.exit(1)
    
    # Run geotagging
    success = geotag_images(args.folder, args.fps, start_time)
    
    if success:
        print("\n✓ Geotagging completed successfully!")
        sys.exit(0)
    else:
        print("\n✗ Geotagging failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

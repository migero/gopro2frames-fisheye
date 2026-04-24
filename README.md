# GoPro 360 mp4 video to frames

Converts GoPro mp4s with equirectangular projections into single frames with correct metadata.

## Explorer

If you don't / can't run this script locally, our cloud product, Explorer, provides almost all of this scripts functionality in a web app.

* [Explorer app](https://explorer.trekview.org/).
* [Explorer docs](https://guides.trekview.org/explorer/overview).

## Installation

You must have:

* ffmpeg
    * by default we bind to default path, so test by running `ffmpeg` in your cli
* exiftool
    * by default we bind to default path, so test by running `exiftool` in your cli

Installed on you system.

You can then install the required Trek View components:

This repo:

```
$ git clone https://github.com/migero/gopro2frames-fisheye
$ cd gopro2frames-fisheye
```

**Note:** `max2sphere` and `fusion2sphere` have been merged into this project, so you no longer need to clone them separately.

### Using a virtual environment (RECOMMENDED)

**Important:** Use Python 3.9.23 specifically. Other Python versions may fail during dependency installation with errors like:

```
ERROR: Failed to build 'pandas' when installing build dependencies for pandas
```

To keep things clean on your system and ensure compatibility, run it in a virtual environment:

#### Using conda (recommended):

```
$ conda create -n gopro2frames python=3.9.23
$ conda activate gopro2frames
$ pip install -r requirements.txt
```

#### Using venv:

```
$ python3.9 -m venv env
$ source env/bin/activate
$ pip install -r requirements.txt
```

## Usage

### Added support to use [config.ini](https://github.com/trek-view/gopro-frame-maker/blob/dev/config.ini) file 
If using config.ini file only videos (1 video in case of max, and 2 videos in case of fusion) needs to pass as the arguments all other flags will be taken from config.ini

### Options

```
$ python3 gfm.py VIDEO_NAME.mp4
```

You can set all opitons in the [`config.ini`] file.

```
[DEFAULT]
name=
mode=
magick_path=
ffmpeg_path=
frame_rate=
time_warp=
quality=
logo_image=
logo_percentage=
debug=
```

* `name`: sequence name
	* default: none (you must set a value for this)
	* options: `a-z`,`1-3`,`-`,`_` chars only
* `mode`: determines input type (and processing steps). Either `equirectangular` for 360 .mp4's, `hero` for normal mp4's, `dualfish` for two Fusion fisheye videos, `eac` for MAX .360 files
	* default: none (you must set a value for this)
	* options: `equirectangular`,`hero`,`eac`,`dualfish`
* `magick_path`: path to imagemagick
	* default (if left blank): assumes imagemagick is installed globally
* `ffmpeg_path` (if left blank): path to ffmpeg
	* default: assumes ffmpeg is installed globally
* `frame_rate`: sets the frame rate (frames per second) for extraction,
	* default: `1`
	* options: `0.1`,`0.2`,`0.5`,`1`,`2`,`3`,`4`,`5`
* `quality`: sets the extracted quality between 1-6. 1 being the highest quality (but slower processing). This is value used for ffmpeg `-q:v` flag.
	* default: `1`
	* options: `1`,`2`,`3`,`4`,`5`,`6`

* `logo_image`: Path to logofile used for nadir / watermark
	* default: blank (do not add logo)
* `logo_percentage`: overlay size of nadir / watermark between 8 - 20, in increments of 1.
	* default: 12 (only used if `logo_image` set)
* `debug`: enable debug mode.
	* Default: `FALSE`
	* options: `TRUE`,`FALSE`

### Sharpness-Based Frame Selection

Instead of extracting frames at fixed intervals (which may result in blurry frames), you can enable sharpness-based frame selection to analyze every frame in the video and select only the sharpest frames.

```bash
# Enable sharpness detection with default settings
python3 gfm.py VIDEO_NAME.mp4 -r 2 --detect-sharpness

# Set minimum quality threshold (0-100) - frames below this are skipped
python3 gfm.py VIDEO_NAME.mp4 -r 2 --detect-sharpness --threshold 40

# Adjust crop size for sharpness analysis (64, 128, 256, 384, or 512 pixels)
python3 gfm.py VIDEO_NAME.mp4 -r 2 --detect-sharpness --crop-size 384
```

**How it works:**
1. The entire video is analyzed using ffmpeg's `blurdetect` filter on small crop regions (center + 4 corners)
2. Each frame gets a sharpness score from 0-100 (higher = sharper)
3. The video is divided into intervals based on your target FPS
4. The sharpest frame from each interval is selected for extraction
5. If `--threshold` is set, intervals where even the best frame is below the threshold are skipped

**Options:**
* `--detect-sharpness`: Enable sharpness-based frame selection (analyzes every frame)
* `--crop-size`: Size of crop squares for blur analysis (default: 256px). Larger = more accurate but slower
* `--threshold`: Minimum sharpness score (0-100). Frames/intervals below this are skipped

This is especially useful for:
- Videos with motion blur from fast movement
- Handheld footage with occasional shake
- Ensuring only high-quality frames are extracted for photogrammetry

## Test cases

Our suite of test cases can be downloaded here:

* [Valid video files](https://guides.trekview.org/explorer/developer-docs/sequences/upload/good-test-cases)

### Run Tests

All the tests resides in `tests` folder.

To run all the tests, run:

```
python -m unittest discover tests -p '*_tests.py'
```

### Camera support

This scripte only accepts videos:

* Must be shot on GoPro camera
* Must have telemetry (GPS enabled when shooting)

It supports both 360 and non-360 videos. In the case of 360 videos, these must be processed by GoPro Software to final mp4 versions.

This script has currently been tested with the following GoPro cameras:

* GoPro HERO
	* HERO 8
	* HERO 9
	* HERO 10
* GoPro MAX
* GoPro Fusion

It is very likely that older cameras are also supported, but we provide no support for these as they have not been tested.

### Logic

The general processing pipeline of gopro-frame-maker is as follows;

![](/docs/gopro-frame-maker-video-flow.jpg)

[Image source here](https://docs.google.com/drawings/d/1i6givGQnGsu7dW2fLt3qVSWaHDiP0TCciY_DtY5_mc4/edit)

[To read how this script works in detail, please read this post](/docs/LOGIC.md).

### Test cases

[A full library of sample files for each camera can be accessed here](https://guides.trekview.org/explorer/developer-docs/sequences/capture).

#### Examples (MacOS)

##### Extract at a frame rate of 1 FPS

```
[DEFAULT]
magick_path=
ffmpeg_path=
frame_rate= 1
time_warp=
quality= 1
logo_image=
logo_percentage=
debug=
```

```
$ python3 gfm.py samples/GS018422.mp4
```

##### Run with debug mode

```
[DEFAULT]
magick_path=
ffmpeg_path=
frame_rate= 1
time_warp=
quality= 1
logo_image=
logo_percentage=
debug=TRUE
```

```
$ python3 gfm.py GS018422.mp4
```

##### Extract frames at lowest quality

```
[DEFAULT]
magick_path=
ffmpeg_path=
frame_rate= 1
time_warp=
quality= 6
logo_image=
logo_percentage=
debug=TRUE
```

```
$ python3 gfm.py GS018422.mp4
```

##### Extract from a timewarp video shot at 5x speed

```
[DEFAULT]
magick_path=
ffmpeg_path=
frame_rate= 1
time_warp= 5x
quality= 1
logo_image=
logo_percentage=
debug=TRUE
```

```
$ python3 gfm.py -t 5x GS018422.mp4
```

##### Use a custom ffmpeg path

```
[DEFAULT]
magick_path=
ffmpeg_path= /Users/dgreenwood/bin/ffmpeg
frame_rate= 1
time_warp= 
quality= 1
logo_image=
logo_percentage=
debug=TRUE
```

```
python3 gfm.py GS018422.mp4
```

##### Add a custom nadir

```
[DEFAULT]
magick_path=
ffmpeg_path=
frame_rate= 1
time_warp= 
quality= 1
logo_image= /Users/dgreenwood/logo/trekview.png
logo_percentage= 12
debug=TRUE
```

```
python3 gfm.py -n /Users/dgreenwood/logo/trekview.png -p 12 GS018422.mp4
```

## License

[Apache 2.0](/LICENSE).

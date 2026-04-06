import configparser, subprocess, threading, itertools, argparse, platform, logging, datetime, fnmatch, shutil, shlex, pandas as pd, html, copy, time, json, math, csv, os, re
from colorama import init, deinit, reinit, Fore, Back, Style
from geographiclib.geodesic import Geodesic
from decimal import Decimal, getcontext
from haversine import haversine, Unit
from pathlib import Path
from lxml import etree as ET
from os import walk
import itertools
import gpxpy


class SharpnessAnalyzer:
    """
    Analyzes video frames for sharpness using ffmpeg's blurdetect filter.
    Uses crop regions (small squares) to efficiently detect blur without processing entire frames.
    """
    
    def __init__(self, crop_size: int = 256, ffmpeg_path: str = 'ffmpeg'):
        self.crop_size = crop_size
        self.ffmpeg_path = ffmpeg_path
        self.frame_data = []
        self.video_fps = 0
        self.total_frames = 0
        self.duration = 0
        self.frame_width = 0
        self.frame_height = 0
    
    def get_video_info(self, video_path: str) -> dict:
        """Get video metadata using ffprobe"""
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format', '-show_streams',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        info = json.loads(result.stdout)
        
        for stream in info.get('streams', []):
            if stream.get('codec_type') == 'video':
                fps_str = stream.get('r_frame_rate', '30/1')
                num, den = map(int, fps_str.split('/'))
                self.video_fps = num / den if den else 30
                self.total_frames = int(stream.get('nb_frames', 0))
                self.frame_width = int(stream.get('width', 1920))
                self.frame_height = int(stream.get('height', 1080))
                if self.total_frames == 0:
                    duration = float(info.get('format', {}).get('duration', 0))
                    self.total_frames = int(duration * self.video_fps)
                self.duration = float(info.get('format', {}).get('duration', 0))
                break
        
        return {
            'fps': self.video_fps,
            'total_frames': self.total_frames,
            'duration': self.duration,
            'width': self.frame_width,
            'height': self.frame_height
        }
    
    def get_crop_positions(self) -> list:
        """Calculate 5 crop positions: center + 4 at 50% distance to corners"""
        if not self.frame_width or not self.frame_height:
            return [(0, 0)]
        
        w, h = self.frame_width, self.frame_height
        size = self.crop_size
        
        # Center position
        center_x = (w - size) // 2
        center_y = (h - size) // 2
        
        # 50% distance from center to each corner
        quarter_w = w // 4
        quarter_h = h // 4
        
        positions = [
            (center_x, center_y),                    # Center
            (quarter_w, quarter_h),                  # Top-left region
            (3 * quarter_w - size//2, quarter_h),   # Top-right region  
            (quarter_w, 3 * quarter_h - size//2),   # Bottom-left region
            (3 * quarter_w - size//2, 3 * quarter_h - size//2)  # Bottom-right region
        ]
        
        # Ensure crops are within bounds
        valid_positions = []
        for x, y in positions:
            x = max(0, min(x, w - size))
            y = max(0, min(y, h - size))
            valid_positions.append((x, y))
        
        return valid_positions

    def analyze_frames(self, video_path: str, max_seconds: float = None) -> list:
        """
        Analyze all frames in video for sharpness using crop regions.
        Returns list of dicts with frame number, time, and sharpness score.
        """
        self.get_video_info(video_path)
        crops = self.get_crop_positions()
        
        print(f"Analyzing video for sharpness ({len(crops)} crop regions, {self.crop_size}px squares)...")
        print(f"Video: {self.frame_width}x{self.frame_height}, {self.video_fps:.2f} fps, {self.duration:.1f}s")
        
        self.frame_data = []
        
        # Build filter for center crop (primary analysis)
        center_x, center_y = crops[0]
        
        # Build input options
        input_opts = ['-i', video_path]
        if max_seconds is not None:
            input_opts = ['-t', str(max_seconds)] + input_opts
        
        # Use blurdetect filter for sharpness measurement
        crop_filter = f"crop={self.crop_size}:{self.crop_size}:{center_x}:{center_y},blurdetect,metadata=print"
        
        # Use -map 0:v:0 to select the first video stream (important for .360 files with multiple video tracks)
        cmd = [self.ffmpeg_path] + input_opts + [
            '-map', '0:v:0',
            '-vf', crop_filter,
            '-f', 'null', '-'
        ]
        
        # Run ffmpeg - metadata output goes to stderr
        process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, bufsize=1
        )
        
        # Parse blurdetect output
        blur_pattern = re.compile(r'lavfi\.blur=(\d+\.?\d*)')
        frame_pattern = re.compile(r'frame:(\d+)\s+pts:\d+\s+pts_time:(\d+\.?\d*)')
        current_frame = 0
        current_time = 0.0
        
        for line in process.stderr:
            frame_match = frame_pattern.search(line)
            if frame_match:
                current_frame = int(frame_match.group(1))
                current_time = float(frame_match.group(2))
                continue
            
            blur_match = blur_pattern.search(line)
            if blur_match:
                blur_value = float(blur_match.group(1))
                # Convert blur (0-100+) to sharpness (100-0), clamped
                sharpness = max(0, min(100, 100 - blur_value * 10))
                
                self.frame_data.append({
                    'frame': current_frame,
                    'time': current_time,
                    'blur': blur_value,
                    'sharpness': sharpness
                })
                
                # Progress indicator every 500 frames
                if len(self.frame_data) % 500 == 0:
                    print(f"  Analyzed {len(self.frame_data)} frames...")
        
        process.wait()
        
        print(f"  Analysis complete: {len(self.frame_data)} frames analyzed")
        
        if self.frame_data:
            avg_sharpness = sum(f['sharpness'] for f in self.frame_data) / len(self.frame_data)
            min_sharpness = min(f['sharpness'] for f in self.frame_data)
            max_sharpness = max(f['sharpness'] for f in self.frame_data)
            print(f"  Sharpness stats: avg={avg_sharpness:.1f}, min={min_sharpness:.1f}, max={max_sharpness:.1f}")
        
        return self.frame_data
    
    def select_best_frames(self, target_fps: float, threshold: float = None) -> list:
        """
        Select the best (sharpest) frame from each interval based on target FPS.
        Args:
            target_fps: Desired output frame rate
            threshold: Minimum sharpness score (0-100). Frames below this are skipped.
        
        Returns:
            List of selected frame dicts with interval info
        """
        if not self.frame_data or self.video_fps <= 0:
            return []
        
        # Calculate interval size (how many source frames per output frame)
        interval = int(self.video_fps / target_fps)
        if interval < 1:
            interval = 1
        
        selected = []
        skipped_count = 0
        num_intervals = (len(self.frame_data) + interval - 1) // interval
        
        for i in range(num_intervals):
            start_idx = i * interval
            end_idx = min(start_idx + interval, len(self.frame_data))
            
            interval_frames = self.frame_data[start_idx:end_idx]
            if interval_frames:
                best = max(interval_frames, key=lambda x: x['sharpness'])
                
                # Apply threshold filter
                if threshold is not None and best['sharpness'] < threshold:
                    skipped_count += 1
                    continue
                
                selected.append({
                    'interval': i,
                    'frame': best['frame'],
                    'time': best['time'],
                    'sharpness': best['sharpness'],
                    'blur': best['blur']
                })
        
        if skipped_count > 0:
            print(f"  Skipped {skipped_count} intervals due to sharpness below threshold ({threshold})")
        
        print(f"  Selected {len(selected)} frames from {num_intervals} intervals")
        
        return selected
    
    def get_frame_numbers_for_extraction(self, target_fps: float, threshold: float = None) -> list:
        """
        Get 1-based frame numbers suitable for extraction.
        Frame numbers are 1-based to match ffmpeg output naming (000001.jpg, etc.)
        """
        selected = self.select_best_frames(target_fps, threshold)
        # Convert 0-based frame indices to 1-based frame numbers
        return [f['frame'] + 1 for f in selected]
    
    def generate_sharpness_chart(self, output_path: str, selected_frames: list = None, 
                                  threshold: float = None, video_name: str = "Video"):
        """
        Generate a standalone HTML file with an interactive sharpness chart.
        No external dependencies required - pure HTML/CSS/JavaScript with Canvas.
        """
        if not self.frame_data:
            print("No frame data to generate chart")
            return
        
        # Prepare data for the chart
        sharpness_values = [f['sharpness'] for f in self.frame_data]
        
        # Selected frame indices (if provided)
        selected_indices = set()
        if selected_frames:
            selected_indices = {f['frame'] for f in selected_frames}
        
        # Statistics
        avg_sharpness = sum(sharpness_values) / len(sharpness_values)
        min_sharpness = min(sharpness_values)
        max_sharpness = max(sharpness_values)
        
        # Convert data to JSON for JavaScript
        chart_data = json.dumps([{
            'frame': f['frame'],
            'time': round(f['time'], 3),
            'sharpness': round(f['sharpness'], 2),
            'selected': f['frame'] in selected_indices
        } for f in self.frame_data])
        
        threshold_js = threshold if threshold is not None else 'null'
        threshold_legend = '<div class="legend-item"><div class="legend-color" style="background: #ff6b6b;"></div><span>Threshold</span></div>' if threshold else ''
        
        # Load HTML template
        template_path = os.path.join(os.path.dirname(__file__), 'templates', 'sharpness_chart.html')
        with open(template_path, 'r') as f:
            html_content = f.read()
        
        # Replace template variables; avoid .format() to keep braces in CSS/JS literal
        replacements = {
            'video_name': video_name,
            'frame_count': len(self.frame_data),
            'duration': f"{self.duration:.1f}",
            'fps': f"{self.video_fps:.2f}",
            'avg_sharpness': f"{avg_sharpness:.1f}",
            'min_sharpness': f"{min_sharpness:.1f}",
            'max_sharpness': f"{max_sharpness:.1f}",
            'selected_count': len(selected_frames) if selected_frames else 0,
            'threshold_display': threshold if threshold is not None else 'None',
            'threshold_legend': threshold_legend,
            'chart_data': chart_data,
            'threshold_js': threshold_js,
        }

        for key, value in replacements.items():
            html_content = html_content.replace(f"{{{key}}}", str(value))

        with open(output_path, 'w') as f:
            f.write(html_content)
        
        print(f"  Sharpness chart saved to: {output_path}")


class GoProFrameMakerHelper():
    def __init__(self):
        pass

    @staticmethod
    def getListOfTuples(mylist, n):
        args = [iter(mylist)] * n
        return itertools.zip_longest(fillvalue=None, *args)

    @staticmethod
    def removeEntities(text):
        text = re.sub('"', '', html.unescape(text))
        text = re.sub("'", '', html.unescape(text))
        return html.escape(text)

    @staticmethod
    def latLngDecimalToDecimal(latLng):
        ll = latLng.split(" ")
        return float(ll[0]) * (-1 if ll[1].strip() in ['W', 'S'] else 1)

    @staticmethod
    def latLngToDecimal(latLng):
        deg, minutes, seconds, direction = re.split('[deg\'"]+', latLng)
        return (float(deg.strip()) + float(minutes.strip())/60 + float(seconds.strip())/(60*60)) * (-1 if direction.strip() in ['W', 'S'] else 1)

    @staticmethod
    def latLngToDirection(latLng):
        deg, minutes, seconds, direction = re.split('[deg\'"]+', latLng)
        return direction.strip()

    @staticmethod
    def getAltitudeFloat(altitude):
        alt = float(altitude.split(" ")[0])
        return alt

    @staticmethod
    def decimalDivide(num1, num2):
        num1 = Decimal(round(num1, 6))
        num2 = Decimal(round(num2, 6))
        if num2 == 0.0:
            return 0.0
        if num1 == 0.0:
            return 0.0
        num = Decimal(num1 / num2)
        if num == 0.0:
            num = abs(num)
        return round(float(num), 3)

    @staticmethod
    def calculateBearing(lat1, long1, lat2, long2):
        Long = (long2-long1)
        y = math.sin(Long) * math.cos(lat2)
        x = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(Long)
        brng = math.degrees((math.atan2(y, x)))
        brng = (((brng + 360) % 360))
        return brng

    @staticmethod
    def calculateExtensions(gps, times, positions, etype=1, utype=1):
        if utype == 1:
            gps_speed_accuracy_meters = float('0.1')
            gps_fix_type = gps["GPSMeasureMode"]
            gps_vertical_accuracy_meters = float(gps["GPSHPositioningError"].strip())
            gps_horizontal_accuracy_meters = float(gps["GPSHPositioningError"].strip())
        else:
            gps_speed_accuracy_meters = float('0.1')
            gps_fix_type = '3-Dimensional Measurement'
            gps_vertical_accuracy_meters = float('0.1')
            gps_horizontal_accuracy_meters = float('0.1')
        
        if etype == 1:
            #Get Times from metadata
            start_time = times[0]
            end_time = times[1]
            gps_epoch_seconds = times[2]
            time_diff = (end_time - start_time).total_seconds()

            #Get Latitude, Longitude and Altitude
            start_latitude = positions[0][0]
            start_longitude = positions[0][1]
            start_altitude = positions[0][2]

            end_latitude = positions[1][0]
            end_longitude = positions[1][1]
            end_altitude = positions[1][2]

            #Find Haversine Distance
            distance = haversine((start_latitude, start_longitude), (end_latitude, end_longitude), Unit.METERS)

            #Find Bearing
            brng = Geodesic.WGS84.Inverse(start_latitude, start_longitude, end_latitude, end_longitude)
            azimuth1 = (brng['azi1'] + 360) % 360
            azimuth2 = (brng['azi2'] + 360) % 360

            compass_bearing = azimuth2

            #Create Metada Fields
            AC = math.sin(math.radians(azimuth1))*distance
            BC = math.cos(math.radians(azimuth2))*distance

            #print((start_latitude, start_longitude), (end_latitude, end_longitude))
            #print("AC: {}, BC: {}, azimuth1: {}, azimuth2: {}, \ntime: {}, distance: {} seconds: {}\n\n\n".format(AC, BC, azimuth1, azimuth2, Decimal(time_diff), distance, gps_epoch_seconds))

            gps_elevation_change_next_meters = float(end_altitude - start_altitude)
            gps_velocity_east_next_meters_second = GoProFrameMakerHelper.decimalDivide( AC, time_diff ) 
            gps_velocity_north_next_meters_second = GoProFrameMakerHelper.decimalDivide( BC, time_diff )
            gps_velocity_up_next_meters_second = GoProFrameMakerHelper.decimalDivide( gps_elevation_change_next_meters, time_diff )
            gps_speed_next_meters_second = GoProFrameMakerHelper.decimalDivide( distance, time_diff )
            gps_heading_next_degrees = GoProFrameMakerHelper.decimalDivide( compass_bearing, 1 )
            gps_pitch_next_degrees = GoProFrameMakerHelper.decimalDivide( gps_elevation_change_next_meters, distance ) % 360
            gps_distance_next_meters = distance
            gps_speed_next_kmeters_second = GoProFrameMakerHelper.decimalDivide( gps_distance_next_meters, 1000.0  ) #in kms
            gps_time_next_seconds = time_diff
        else:
            gps_epoch_seconds = times[2]
            gps_velocity_east_next_meters_second = 0.0
            gps_velocity_north_next_meters_second = 0.0
            gps_velocity_up_next_meters_second = 0.0
            gps_speed_next_meters_second = 0.0
            gps_speed_next_kmeters_second = 0.0
            gps_heading_next_degrees = 0.0
            gps_elevation_change_next_meters = 0.0
            gps_pitch_next_degrees = 0.0
            gps_distance_next_meters = 0.0
            gps_time_next_seconds = 0.0
        return {
            "gps_epoch_seconds": gps_epoch_seconds,
            "gps_fix_type": gps_fix_type,
            "gps_vertical_accuracy_meters": "{0:.3f}".format(gps_vertical_accuracy_meters),
            "gps_horizontal_accuracy_meters": "{0:.3f}".format(gps_horizontal_accuracy_meters),
            "gps_velocity_east_next_meters_second": "{0:.3f}".format(gps_velocity_east_next_meters_second),
            "gps_velocity_north_next_meters_second": "{0:.3f}".format(gps_velocity_north_next_meters_second),
            "gps_velocity_up_next_meters_second": "{0:.3f}".format(gps_velocity_up_next_meters_second),
            "gps_speed_accuracy_meters": "{0:.3f}".format(gps_speed_accuracy_meters),
            "gps_speed_next_meters_second": "{0:.3f}".format(gps_speed_next_meters_second),
            "gps_heading_next_degrees": "{0:.3f}".format(gps_heading_next_degrees),
            "gps_elevation_change_next_meters": "{0:.3f}".format(gps_elevation_change_next_meters),
            "gps_pitch_next_degrees": "{0:.3f}".format(gps_pitch_next_degrees),
            "gps_distance_next_meters": "{0:.3f}".format(gps_distance_next_meters),
            "gps_time_next_seconds": "{0:.3f}".format(gps_time_next_seconds),
            "gps_speed_next_kmeters_second": "{0:.3f}".format(gps_speed_next_kmeters_second)
        }

    @staticmethod
    def parseMetadata(xmlFileName):
        root = ET.parse(xmlFileName).getroot()
        nsmap = root[0].nsmap

        videoInfoFields = [
            'Duration',
            'DeviceName', 
            'ProjectionType', 
            'MetaFormat',
            'StitchingSoftware',
            'VideoFrameRate',
            'SourceImageHeight',
            'SourceImageWidth',
            'FileSize',
            'FileType',
            'FileTypeExtension',
            'CompressorName'
        ] 
        gpsFields = [
            'GPSDateTime', 
            'GPSLatitude', 
            'GPSLongitude', 
            'GPSAltitude',
            'GPSHPositioningError',
            'GPSMeasureMode'
        ]
        sensorFields = [
            'ExposureTimes',
            'ISOSpeeds',
            'Accelerometer',
            'Gyroscope',
            'TimeStamp'
        ]
        gpsData = []
        sensorData = []
        current_timestamp = None
        videoFieldData = {}
        videoFieldData['ProjectionType'] = ''
        videoFieldData['StitchingSoftware'] = ''
        videoFieldData['MetaFormat'] = ''
        videoFieldData['CompressorName'] = ''
        videoFieldData['CompressorNameTrack'] = []
        anchor = ''
        data = {}
        ldata = {}
        adata = {}
        for elem in root[0]:
            eltags = elem.tag.split("}")
            nm = eltags[0].replace("{", "")
            tag = eltags[-1].strip()
            if tag in videoInfoFields:
                if tag == 'MetaFormat':
                    if elem.text.strip() == 'gpmd':
                        for k, v in nsmap.items():
                            if v == nm:
                                Track = k
                                break
                        videoFieldData[tag.strip()] = elem.text.strip()
                elif tag == 'ProjectionType':
                    if elem.text.strip() == 'equirectangular':
                        videoFieldData[tag] = elem.text.strip()
                elif tag == 'CompressorName':
                    if elem.text.strip() == 'GoPro H.265 encoder':
                        for k, v in nsmap.items():
                            if v == nm:
                                videoFieldData['CompressorNameTrack'].append(int(k.replace("Track", "")))
                                break
                else:
                    videoFieldData[tag.strip()] = elem.text.strip()
        for elem in root[0]:
            eltags = elem.tag.split("}")
            nm = eltags[0].replace("{", "")
            tag = eltags[-1].strip()
            if (tag in gpsFields) and (nm == nsmap[Track]):
                if tag.strip() in ['GPSHPositioningError', 'GPSMeasureMode']:
                    adata[tag] = elem.text.strip()
                if tag == 'GPSDateTime':
                    if anchor != '': 
                        for k, v in adata.items():
                            data[k] = v
                        gpsData.append(data)
                        anchor = str(elem.text.strip())
                        data = {
                            'GPSData': [],
                            'GPSHPositioningError': '',
                            'GPSMeasureMode': '',
                            'GPSDateTime': anchor
                        }
                    else:
                        anchor = str(elem.text.strip())
                        data = {
                            'GPSData': [],
                            'GPSHPositioningError': '',
                            'GPSMeasureMode': '',
                            'GPSDateTime': anchor
                        }
                        for k, v in adata.items():
                            data[k] = v
                else:
                    if tag.strip() in ['GPSLatitude', 'GPSLongitude', 'GPSAltitude']:
                        if (len(ldata) <= 3):
                            ldata[tag] = elem.text.strip()
                            if len(ldata) == 3:
                                """if len(data['GPSData']) > 0:
                                    prev = data['GPSData'][-1]
                                    if (((ldata['GPSLatitude'] == prev['GPSLatitude']) and (ldata['GPSLongitude'] == prev['GPSLongitude']) and (ldata['GPSAltitude'] == prev['GPSAltitude'])) is not True):
                                        data['GPSData'].append(ldata)
                                    else:
                                        print("Found duplicate GPS POint...")
                                        print(ldata, prev)
                                else:
                                    data['GPSData'].append(ldata)"""
                                data['GPSData'].append(ldata)
                                ldata = {}
        for k, v in adata.items():
            data[k] = v
        gpsData.append(data)

        # Parse sensor data (ISO, Shutter Speed, Accelerometer, Gyroscope) with timestamps
        sensor_anchor = ''
        sensor_data = {}
        for elem in root[0]:
            eltags = elem.tag.split("}")
            nm = eltags[0].replace("{", "")
            tag = eltags[-1].strip()
            if (tag in sensorFields) and (nm == nsmap[Track]):
                if tag == 'TimeStamp':
                    # Update current timestamp for subsequent sensor fields
                    current_timestamp = float(elem.text.strip())
                elif tag == 'ExposureTimes':
                    if sensor_anchor != '':
                        sensorData.append(sensor_data)
                        sensor_data = {
                            'ExposureTimes': elem.text.strip(),
                            'TimeStamp': current_timestamp if current_timestamp is not None else 0.0
                        }
                        sensor_anchor = elem.text.strip()
                    else:
                        sensor_anchor = elem.text.strip()
                        sensor_data = {
                            'ExposureTimes': elem.text.strip(),
                            'TimeStamp': current_timestamp if current_timestamp is not None else 0.0
                        }
                elif tag == 'ISOSpeeds':
                    sensor_data['ISOSpeeds'] = elem.text.strip()
                    sensor_data['TimeStamp'] = current_timestamp if current_timestamp is not None else sensor_data.get('TimeStamp', 0.0)
                elif tag in ['Accelerometer', 'Gyroscope']:
                    # These are binary data, we'll note their presence
                    sensor_data[tag] = 'present' if 'Binary data' in elem.text else elem.text.strip()
        if sensor_data:
            sensorData.append(sensor_data)

        if 'Duration' in videoFieldData:
            _tsm = videoFieldData['Duration'].strip().split(' ')
            if len(_tsm) > 0:
                _first = _tsm[0]
                _sm = _tsm[-1]
                if ':' in _first:
                    # Already in H:MM:SS or HH:MM:SS[:mmm] format
                    _parts = _first.split(':')
                    _h = int(_parts[0])
                    _m = int(_parts[1])
                    _s = float(_parts[2]) if len(_parts) > 2 else 0.0
                    videoFieldData['Duration'] = "{:02d}:{:02d}:{:06.3f}".format(_h, _m, _s)
                else:
                    _t = float(_first)
                    if _sm == 's':
                        videoFieldData['Duration'] = "00:00:{:06.3F}".format(_t)
            else:
                if '.' not in videoFieldData['Duration']:
                    videoFieldData['Duration'] = "{}.000".format(videoFieldData['Duration'].strip())
        return {
            'gps_data': gpsData,
            'video_field_data': videoFieldData,
            'sensor_data': sensorData
        }

    @staticmethod
    def gpsTimestamps(gpsData, videoFieldData, sensorData=None):
        
        gpx = gpxpy.gpx.GPX()

        # Create first track in our GPX:
        gpx_track = gpxpy.gpx.GPXTrack()
        gpx.tracks.append(gpx_track)

        # Create first segment in our GPX track:
        gpx_segment = gpxpy.gpx.GPXTrackSegment()
        gpx_track.segments.append(gpx_segment)
        Timestamps = []
        counter = 0
        gLen = len(gpsData)
        first_start_time = datetime.datetime.strptime(gpsData[0]["GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
        final_end_time = datetime.datetime.strptime(gpsData[0]["GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
        for gps in gpsData:
            if counter < gLen-1:
                start_gps = gpsData[counter]
                end_gps = gpsData[counter + 1]

                #Get Times from metadata
                start_time = datetime.datetime.strptime(start_gps["GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
                end_time = datetime.datetime.strptime(end_gps["GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
                time_diff = (end_time - start_time).total_seconds()
                diff = int((time_diff/float(len(start_gps["GPSData"])))*1000.0)
                #check this later
                if diff == 0:
                    if start_time == end_time:
                        start_time = end_time
                        diff = int((0.05)*1000.0)
                        end_time = end_time+datetime.timedelta(0, 0.05) 
                new = pd.date_range(start=start_time, end=end_time, closed='left', freq="{}ms".format(diff))
                icounter = 0
                dlLen = 1 if len(start_gps["GPSData"]) < 1 else len(start_gps["GPSData"])
                nlLen = 1 if len(new) < 1 else len(new)
                _ms = math.floor(dlLen/nlLen)
                _ms = 1 if _ms < 1 else _ms
                for gps in start_gps["GPSData"]:
                    tBlock = gps.copy()
                    tBlock["GPSDateTime"] = new[min(icounter, len(new) - 1)]
                    tBlock["GPSMeasureMode"] = start_gps["GPSMeasureMode"]
                    tBlock["GPSHPositioningError"] = start_gps["GPSHPositioningError"]
                    Timestamps.append(tBlock)
                    icounter = icounter + _ms
            else:
                #datetime.datetime.strptime("0:0:0 0:0:0.0", "%Y:%m:%d %H:%M:%S.%f")
                start_gps = gpsData[counter]
                #Get Times from metadata

                #_e_date = start_gps["GPSDateTime"].split(" ")[0]
                zero_start = datetime.datetime.strptime("2022:1:1 00:00:00.000", "%Y:%m:%d %H:%M:%S.%f")
                zero_duration = datetime.datetime.strptime("2022:1:1 {}".format(videoFieldData['Duration']), "%Y:%m:%d %H:%M:%S.%f")

                start_time = datetime.datetime.strptime(start_gps["GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
                
                l_1 = (start_time - first_start_time).total_seconds()
                l_2 = (zero_duration - zero_start).total_seconds()
                end_time = start_time+datetime.timedelta(0, l_2-l_1) 
                
                time_diff = (end_time - start_time).total_seconds()
                diff = int((time_diff/float(len(start_gps["GPSData"])))*1000.0)
                #check this later
                if diff == 0:
                    if start_time == end_time:
                        print('####')
                        start_time = end_time
                        diff = int((0.05)*1000.0)
                        end_time = end_time+datetime.timedelta(0, 0.05) 
                final_end_time = end_time
                new = pd.date_range(start=start_time, end=end_time, closed='left', freq="{}ms".format(diff))
                icounter = 0
                dlLen = 1 if len(start_gps["GPSData"]) < 1 else len(start_gps["GPSData"])
                nlLen = 1 if len(new) < 1 else len(new)
                _ms = math.floor(dlLen/nlLen)
                _ms = 1 if _ms < 1 else _ms
                for gps in start_gps["GPSData"]:
                    tBlock = gps.copy()
                    tBlock["GPSDateTime"] = new[min(icounter, len(new) - 1)]
                    tBlock["GPSMeasureMode"] = start_gps["GPSMeasureMode"]
                    tBlock["GPSHPositioningError"] = start_gps["GPSHPositioningError"]
                    Timestamps.append(tBlock)
                    icounter = icounter + _ms
            counter = counter + 1
        icounter = 0
        tlen = len(Timestamps)
        t1970 = datetime.datetime.strptime("1970:01:01 00:00:00.000000", "%Y:%m:%d %H:%M:%S.%f")
        prev = None
        for gps in Timestamps:
            #removing duplicate lat&lng
            if icounter > 0:
                prev = Timestamps[icounter-1]
                if ((gps['GPSLatitude'] == prev['GPSLatitude']) and (gps['GPSLongitude'] == prev['GPSLongitude']) and (gps['GPSAltitude'] == prev['GPSAltitude'])):
                    icounter = icounter + 1
                    continue
            #Get Start Time from metadata
            start_time = gps["GPSDateTime"]
            gps_epoch_seconds = (start_time-t1970).total_seconds()
            #Get Latitude, Longitude and Altitude
            start_latitude = GoProFrameMakerHelper.latLngToDecimal(gps["GPSLatitude"])
            start_longitude = GoProFrameMakerHelper.latLngToDecimal(gps["GPSLongitude"])
            start_altitude = GoProFrameMakerHelper.getAltitudeFloat(gps["GPSAltitude"])
            gpx_point = gpxpy.gpx.GPXTrackPoint(
                latitude=start_latitude, 
                longitude=start_longitude, 
                time=start_time, 
                elevation=start_altitude
            )
            gpx_segment.points.append(gpx_point)
            if icounter < tlen-1:
                #Get End Time from metadata
                end_time = Timestamps[icounter+1]["GPSDateTime"]
                time_diff = (end_time - start_time).total_seconds()

                #Get Latitude, Longitude and Altitude
                end_latitude = GoProFrameMakerHelper.latLngToDecimal(Timestamps[icounter+1]["GPSLatitude"])
                end_longitude = GoProFrameMakerHelper.latLngToDecimal(Timestamps[icounter+1]["GPSLongitude"])
                end_altitude = GoProFrameMakerHelper.getAltitudeFloat(Timestamps[icounter+1]["GPSAltitude"])

                ext = GoProFrameMakerHelper.calculateExtensions(
                    gps, 
                    (start_time, end_time, gps_epoch_seconds),
                    (
                        (start_latitude, start_longitude, start_altitude),
                        (end_latitude, end_longitude, end_altitude)
                    ),
                    1, 1
                )
            else:
                ext = GoProFrameMakerHelper.calculateExtensions(
                    gps, 
                    (start_time, None, gps_epoch_seconds),
                    (
                        (start_latitude, start_longitude, start_altitude),
                        (None, None, None)
                    ),
                    0, 1
                )
            del ext["gps_speed_next_kmeters_second"]
            for k, v in ext.items():
                gpx_extension = ET.fromstring(f"""
                    <{str(k)}>{str(v)}</{str(k)}>
                """)
                gpx_point.extensions.append(gpx_extension)
            icounter = icounter + 1
        gpxData = gpx.to_xml() 

        return {
            "gpx_data": gpxData,
            "start_time": Timestamps[0]['GPSDateTime'],
            "end_time": final_end_time,
            "sensor_data": sensorData if sensorData else []
        }


    @staticmethod
    def getConfig():
        #read config file
        values_required = [
            'ffmpeg_path',
            'frame_rate',
            'quality',
            'debug'
        ]
        data = {}
        config_path = Path('./config.ini')
        status = False
        if config_path.is_file():
            config = configparser.ConfigParser()
            config.read(str(config_path.resolve()))
            status = True
            for val in values_required:
                if val not in config['DEFAULT']:
                    status = False
                    print("Required value '{}' is missing from config.ini please make sure its present before you use connfig.ini\n".format(val))
            if status == False:
                print('Please make sure all required values are present in config file. Falling back to command line arguments mode.\n')
                time.sleep(2)
            else:
                try:
                    if platform.system() == "Windows":
                        ffmpeg = "ffmpeg.exe"
                    else:
                        ffmpeg = "ffmpeg"

                    default = {
                        'debug': config.getboolean('DEFAULT', 'debug'),
                        'ffmpeg_path': config['DEFAULT'].get('ffmpeg_path', ffmpeg),
                        'frame_rate': float(config['DEFAULT'].get('frame_rate', '0.5')),
                        'quality': int(config['DEFAULT'].get('quality', '1'))
                    }
                    status = True
                except:
                    status = False
        return {
            'status': status,
            'config': default
        }

    @staticmethod
    def validateArgs(args):
        status = True
        arguments = {
            'current_directory': Path(),
            'predicted_camera': '',
            'input': '',
            'ffmpeg': '',
            'frame_rate': 0.5,
            'quality': 1,
            'debug': '',
            'detect_sharpness': False,
            'crop_size': 256,
            'threshold': None,
            'startf': None,
            'endf': None
        }
        errors = []
        info = []
        args_input_len = len(args.input)

        #validating length of input video files
        if(args_input_len > 2):
            errors.append("Only (1) Input files is required in case of max video file and (2) in case of fusion video file.")
            status = False

        #validating input video files for max camera (Python max2sphere used — no binary needed)
        if(args_input_len == 1): 
            #camera should be max
            arguments['predicted_camera'] = 'max'
            arguments['folder_mode'] = False  # Will be set to True if folder is detected

        #validating input video files for fusion camera
        #should be only (2) video files (front and back)
        elif(args_input_len == 2):
            #camera should be fusion
            arguments['predicted_camera'] = 'fusion'
            #sort front/back fusion videos
            front = os.path.basename(args.input[0])[0:4]
            back = os.path.basename(args.input[1])[0:4]
            if((front == 'GPFR') and (back == 'GPBK')):
                args.input = [args.input[0], args.input[1]]
            elif((front == 'GPBK') and (back == 'GPFR')):
                args.input = [args.input[1], args.input[0]]
            else:
                errors.append("2 input videos provided but cannot identify front (GPFR) and back (GPBK) videos.")
                status = False
        else:
            errors.append("Please make sure to provide (1) video in case of max camera and (2) in case of fusion camera.")
            status = False


        #validate if the provided input file is actually exists or not.
        if(arguments['predicted_camera'] == 'max'):
            arguments['input'] = [Path(args.input[0])]
            if(arguments['input'][0].is_file()): #input is a list.
                pass
            elif(arguments['input'][0].is_dir()):
                # Check if it's a folder with track0 and track5
                track0_path = arguments['input'][0] / 'track0'
                track5_path = arguments['input'][0] / 'track5'
                if track0_path.is_dir() and track5_path.is_dir():
                    import fnmatch
                    track0_images = fnmatch.filter(os.listdir(str(track0_path)), '*.jpg')
                    track5_images = fnmatch.filter(os.listdir(str(track5_path)), '*.jpg')
                    if len(track0_images) > 0 and len(track5_images) > 0:
                        arguments['folder_mode'] = True
                        info.append("Folder mode detected: using existing track0 ({} images) and track5 ({} images)".format(len(track0_images), len(track5_images)))
                        info.append("Note: GPS tagging will be skipped as no video file is provided.")
                    else:
                        errors.append("Folder {} contains track0 and track5 but they have no .jpg images".format(args.input[0]))
                        status = False
                else:
                    errors.append("Folder {} must contain 'track0' and 'track5' subdirectories with extracted frames".format(args.input[0]))
                    status = False
            else:
                errors.append("Input {} does not exist (must be a video file or folder with track0/track5)".format(args.input[0]))
                status = False

        #validate if the provided input file is actually exists or not.
        elif(arguments['predicted_camera'] == 'fusion'):
            arguments['input'] = [Path(args.input[0]), Path(args.input[1])]
            if((arguments['input'][0].is_file()) and (arguments['input'][1].is_file())): #input is a list.
                pass
            else:
                if((arguments['input'][0].is_file() == False) and (arguments['input'][0].is_file() == False)): #input is a list.
                    errors.append("Input files {}, {} does not exists.".format(args.input[0], args.input[1]))
                elif(arguments['input'][0].is_file()):
                    errors.append("Input file {} does not exists.".format(args.input[0]))
                elif(arguments['input'][1].is_file()):
                    errors.append("Input file {} does not exists.".format(args.input[1]))
                status = False


        #checking is a ffmpeg path is given, if not show the default one.
        if(args.ffmpeg_path is None):
            info.append("Default path for ffmpeg is used as ffmpeg-path is not provided.")
            arguments['ffmpeg'] = Path('.{}FFmpeg{}ffmpeg'.format(os.sep, os.sep))
            if(arguments['ffmpeg'].is_file() == False):
                errors.append("Ffmpeg binary {} does not exists.".format('.{}FFmpeg{}ffmpeg'.format(os.sep, os.sep)))
                status = False
        else:
            arguments['ffmpeg'] = Path(args.ffmpeg_path)
            if(arguments['ffmpeg'].is_file() == False):
                errors.append("Ffmpeg binary {} does not exists.".format(args.ffmpeg_path))
                status = False

        #validating frame rate parameter used for ffmpeg
        if (args.frame_rate is not None):
            frameRate = args.frame_rate
            if frameRate <= 0:
                errors.append("Frame rate {} is not valid. Must be greater than 0.".format(frameRate))
            elif frameRate > 30:
                errors.append("Frame rate {} may be too high. Maximum recommended is 30 fps.".format(frameRate))
            else:
                arguments["frame_rate"] = frameRate
        else:
            arguments["frame_rate"] = 0.5

        #validating quality parameter used for ffmpeg
        if (args.quality is not None):
            quality = int(args.quality)
            qopts = [1,2,3,4,5]
            if quality not in qopts:
                errors.append("Extracted quality {} is not available. Only 1, 2, 3, 4, 5 options are available.".format(quality))
                status = False
            else:
                arguments["quality"] = quality
        else:
            arguments["quality"] = 1

        #validating frame range parameters
        if hasattr(args, 'startf') and args.startf is not None:
            if args.startf < 1:
                errors.append("Starting frame must be >= 1 (got {})".format(args.startf))
            else:
                arguments["startf"] = args.startf
        
        if hasattr(args, 'endf') and args.endf is not None:
            if args.endf < 1:
                errors.append("Ending frame must be >= 1 (got {})".format(args.endf))
            else:
                arguments["endf"] = args.endf
        
        if arguments["startf"] is not None and arguments["endf"] is not None:
            if arguments["startf"] > arguments["endf"]:
                errors.append("Starting frame ({}) must be <= ending frame ({})".format(arguments["startf"], arguments["endf"]))


        # Validate sharpness detection parameters
        if hasattr(args, 'detect_sharpness'):
            arguments['detect_sharpness'] = getattr(args, 'detect_sharpness', False)
        
        if hasattr(args, 'crop_size'):
            crop_size = getattr(args, 'crop_size', 256)
            if crop_size not in [64, 128, 256, 384, 512]:
                errors.append("Crop size {} is not valid. Must be one of: 64, 128, 256, 384, 512.".format(crop_size))
            else:
                arguments['crop_size'] = crop_size
        
        if hasattr(args, 'threshold'):
            threshold = getattr(args, 'threshold', None)
            if threshold is not None:
                if threshold < 0 or threshold > 100:
                    errors.append("Threshold {} is not valid. Must be between 0 and 100.".format(threshold))
                else:
                    arguments['threshold'] = threshold

        return {
            'status': status,
            'args': arguments,
            'errors': errors,
            'info': info
        }

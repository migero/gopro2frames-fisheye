import configparser, subprocess, threading, itertools, argparse, platform, logging, datetime, fnmatch, shutil, pandas as pd, shlex, html, copy, time, json, math, csv, os, re, sys
from multiprocessing import Pool, cpu_count
from colorama import init, deinit, reinit, Fore, Back, Style
from gfmhelper import GoProFrameMakerHelper, SharpnessAnalyzer
from sensor_processing import parse_gpmf_gyro, integrate_gyro_roll
from frame_rendering import _process_fisheye_frame, _process_360_frame, _process_360_frame_wrapper
from exif_utils import ExiftoolGetMetadata, ExiftoolGetImagesMetadata, ExiftoolInjectMetadata, ExiftoolInjectImagesMetadata
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'max2sphere'))
import max2sphere as _max2sphere      # For 360° equirectangular images
import max2fisheye as _max2fisheye    # For fisheye images
from geographiclib.geodesic import Geodesic
from decimal import Decimal, getcontext
from haversine import haversine, Unit
from pathlib import Path
from lxml import etree as ET
from os import walk
import itertools
import gpxpy


class GoProFrameMakerParent():
    def __init__(self, args):
        getcontext().prec = 6
        media_folder_full_path = str(args["media_folder_full_path"].resolve())
        try:
            if os.path.exists(media_folder_full_path):
                # If an existing frame_mapping exists and sharpness mode requested, preserve everything
                frame_mapping_path = os.path.join(media_folder_full_path, 'frame_mapping.json')
                if args.get('detect_sharpness', False) and os.path.exists(frame_mapping_path):
                    print(f"Found existing frame_mapping.json - preserving folder contents and reusing sharpness mapping")
                    logging.info("Preserving existing frame_mapping.json and folder contents")
                else:
                    # Check if track0 and track5 exist with images - preserve them if they do
                    track0_path = os.path.join(media_folder_full_path, 'track0')
                    track5_path = os.path.join(media_folder_full_path, 'track5')
                    preserve_tracks = (os.path.exists(track0_path) and 
                                      len(fnmatch.filter(os.listdir(track0_path), '*.jpg')) > 0 and
                                      os.path.exists(track5_path) and 
                                      len(fnmatch.filter(os.listdir(track5_path), '*.jpg')) > 0)
                    
                    if preserve_tracks:
                        # Only delete non-track folders and files, keep track0 and track5
                        print(f"Found existing track0 and track5 with images - preserving them and skipping ffmpeg extraction")
                        logging.info("Preserving existing track0 and track5 folders")
                        for item in os.listdir(media_folder_full_path):
                            item_path = os.path.join(media_folder_full_path, item)
                            if item not in ['track0', 'track5']:
                                if os.path.isdir(item_path):
                                    shutil.rmtree(item_path)
                                else:
                                    os.remove(item_path)
                    else:
                        # No tracks to preserve, clear all contents but keep base folder
                        for item in os.listdir(media_folder_full_path):
                            item_path = os.path.join(media_folder_full_path, item)
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path)
                            else:
                                os.remove(item_path)
                            os.remove(item_path)
            else:
                os.makedirs(media_folder_full_path, exist_ok=True) 
        except:
            exit('Unable to create main media directory {}'.format(media_folder_full_path))
        
        args['log_folder'] = Path('{}{}{}'.format(str(args['current_directory'].resolve()), os.sep, 'logs'))
        args['date_time_current'] = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        self.__args = copy.deepcopy(args)
        self.__setLogging()

    def get_arguments(self):
        return copy.deepcopy(self.__args)

    def __setLogging(self):
        logFolder = str(self.__args['log_folder'].resolve())
        dateTimeCurrent = self.__args["date_time_current"]
        if not os.path.exists(logFolder):
            os.makedirs(logFolder, exist_ok=True)
        if self.__args['debug'] is True:
            logHandlers = [
                logging.FileHandler(logFolder+os.sep+'trekview-gopro-{}.log'.format(dateTimeCurrent)),
                logging.StreamHandler()
            ]
        else:
            logHandlers = [
                logging.FileHandler(logFolder+os.sep+'trekview-gopro-{}.log'.format(dateTimeCurrent))
            ]
        logging.basicConfig(
            level=logging.DEBUG,
            datefmt='%m/%d/%Y %I:%M:%S %p',
            format="%(asctime)s [%(levelname)s] [Line No.:%(lineno)d] %(message)s",
            handlers=logHandlers
        )

    def getListOfTuples(self, mylist, n):
        args = [iter(mylist)] * n
        return itertools.zip_longest(fillvalue=None, *args)

    def removeEntities(self, text):
        text = re.sub('"', '', html.unescape(text))
        text = re.sub("'", '', html.unescape(text))
        return html.escape(text)

    def latLngDecimalToDecimal(self, latLng):
        ll = latLng.split(" ")
        return float(ll[0]) * (-1 if ll[1].strip() in ['W', 'S'] else 1)

    def latLngToDecimal(self, latLng):
        deg, minutes, seconds, direction = re.split('[deg\'"]+', latLng)
        return (float(deg.strip()) + float(minutes.strip())/60 + float(seconds.strip())/(60*60)) * (-1 if direction.strip() in ['W', 'S'] else 1)

    def latLngToDirection(self, latLng):
        deg, minutes, seconds, direction = re.split('[deg\'"]+', latLng)
        return direction.strip()

    def getAltitudeFloat(self, altitude):
        alt = float(altitude.split(" ")[0])
        return alt

    def decimalDivide(self, num1, num2):
        a = round(num1, 6)
        b = round(num2, 6)
        num1 = Decimal(a)
        num2 = Decimal(b)
        if num2 == 0.0:
            return 0.0
        if num1 == 0.0:
            return 0.0
        num = Decimal(num1 / num2)
        if num == 0.0:
            num = abs(num)
        return round(float(num), 3)

    def calculateBearing(self, lat1, long1, lat2, long2):
        Long = (long2-long1)
        y = math.sin(Long) * math.cos(lat2)
        x = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(Long)
        brng = math.degrees((math.atan2(y, x)))
        brng = (((brng + 360) % 360))
        return brng

    def calculateExtensions(self, gps, times, positions, etype=1, utype=1):
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
            gps_velocity_east_next_meters_second = self.decimalDivide( AC, time_diff ) 
            gps_velocity_north_next_meters_second = self.decimalDivide( BC, time_diff )
            gps_velocity_up_next_meters_second = self.decimalDivide( gps_elevation_change_next_meters, time_diff )
            gps_speed_next_meters_second = self.decimalDivide( distance, time_diff )
            gps_heading_next_degrees = self.decimalDivide( compass_bearing, 1 )
            gps_pitch_next_degrees = self.decimalDivide( gps_elevation_change_next_meters, distance ) % 360
            gps_distance_next_meters = distance
            gps_speed_next_kmeters_second = self.decimalDivide( gps_distance_next_meters, 1000.0  ) #in kms
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

    def __subprocess(self, command, sh=0, capture_output=True):
        ret = None
        try:
            cmd = command
            if sh == 0:
                cmd = shlex.split(" ".join(cmd))
            
            output = subprocess.run(cmd, capture_output=capture_output)
            logging.info(output)
            if output.returncode == 0:
                out = ''
                if output.stdout  is not None:
                    out = output.stdout.decode('utf-8',"ignore")
                    logging.info(str(out))
                ret = {
                    "output": out,
                    "error": None
                }
            else:
                print(output)
                raise Exception(output.stderr.decode('utf-8',"ignore"))
        except Exception as e:
            logging.info(str(e))
            ret = {
                "output": None,
                "error": str(e)
            }
        except:
            exit("Error running subprocess. Please try again.")
        return ret

    def __exiftool(self, command, sh=0):
        if platform.system() == "Windows":
            exiftool = "exiftool.exe"
        else:
            exiftool = "exiftool"
        command.insert(0, "-config")
        command.insert(1, ".ExifTool_config")
        command.insert(0, exiftool)
        ret = self.__subprocess(command, sh)
        if ret["error"] is not None:
            print(command)
            logging.critical(ret["error"])
            print(ret["error"])
            exit("Error occured while executing exiftool.")
        return ret

    def _ffmpeg(self, command, sh=0):
        ffmpeg = str(self.__args['ffmpeg'].resolve())
        command.insert(0, ffmpeg)
        # Add quiet flags to suppress ffmpeg output
        if '-v' not in command and '-loglevel' not in command:
            command.insert(1, '-v')
            command.insert(2, 'error')
        ret = self.__subprocess(command, sh, True)
        
        """if ret["error"] is not None:
            logging.critical(ret["error"])
            exit("Error occured while executing ffmpeg, please see logs for more info.")"""
        return True

    def exiftool(self, cmd):
        output = self.__exiftool(cmd, 1)
        #print(" ".join(output))
        if output["output"] is None:
            logging.critical(output["error"])
            logging.critical("Unable to get metadata information")
            exit("Unable to get metadata information")
        else:
            return output["output"]

    def get_video_exif_data(self):
        video_file = '{}'.format(str(self.__args['input'][0].resolve()))
        output = self.__exiftool(["-ee", "-G3", "-api", "LargeFileSupport=1", "-X", video_file], 1)
        if output["output"] is None:
            logging.critical(output["error"])
            logging.critical("Unable to get metadata information")
            exit("Unable to get metadata information")
        else:
            return output["output"]

class GoProFrameMaker(GoProFrameMakerParent):
    def __init__(self, args):
        args["media_folder"] = os.path.basename(str(args['input'][0].resolve())).split(".")[0]
        args["file_type"] = os.path.basename(str(args['input'][0].resolve())).split(".")[-1]
        args["media_folder_full_path"] = Path('{}{}{}'.format(str(args['current_directory'].resolve()), os.sep, args["media_folder"]))
        super().__init__(args)

    def getArguments(self):
        return copy.deepcopy(self.get_arguments())

    def initiateProcessing(self):
        args = self.getArguments()
        if args.get('folder_mode', False):
            # Folder mode: skip video processing, go directly to fisheye/360 generation
            self.__processFolderMode()
        else:
            # Normal mode: process video file
            self.__startProcessing()

    def __processFolderMode(self):
        """Process existing track0/track5 folders without a video file."""
        args = self.getArguments()
        media_folder_full_path = str(args["input"][0].resolve())
        
        logging.info("Folder mode: Processing existing frames from {}".format(media_folder_full_path))
        print("Folder mode: Processing existing frames from {}".format(media_folder_full_path))
        
        # Update media_folder_full_path in args
        args["media_folder_full_path"] = Path(media_folder_full_path)
        
        # Go directly to fisheye/360 generation (reuse the logic from __breakIntoFrames360)
        # This will automatically detect existing track0/track5 and skip ffmpeg
        track0 = os.path.join(media_folder_full_path, 'track0')
        track5 = os.path.join(media_folder_full_path, 'track5')
        
        total_images = fnmatch.filter(os.listdir(track0), '*.jpg')
        total_images.sort()

        # Build frame_numbers respecting max_seconds
        all_frame_numbers = [int(os.path.splitext(f)[0]) for f in total_images]
        max_seconds = args.get('max_seconds')
        if max_seconds is not None:
            # Without video, we estimate FPS from frame count
            # Default to 30fps for estimation if not specified
            est_fps = args.get('frame_rate', 30.0)
            max_frame_count = max(1, int(max_seconds * est_fps))
            frame_numbers = all_frame_numbers[:max_frame_count]
        else:
            frame_numbers = all_frame_numbers
        max_frames = len(frame_numbers)

        # For folder mode, we can't get video duration, so estimate based on frame count
        duration = max_frames / args.get('frame_rate', 30.0)

        try:
            # Determine template and output size from the first frame pair
            which_template, fw, fh = _max2fisheye.check_frames(
                os.path.join(track0, total_images[0]),
                os.path.join(track5, total_images[0]),
            )
            
            # Determine which modes to run
            fisheye_only = args.get('fisheye_only', False)
            e360_only = args.get('e360_only', False)
            run_fisheye = not e360_only
            run_360 = not fisheye_only
            
            antialias = int(args.get('antialias', 2))
            seq_tmpl = os.path.join(media_folder_full_path, 'track%d', '%06d.jpg')
            
            # FISHEYE IMAGE GENERATION
            if run_fisheye:
                if args.get('fisheye_width') is not None:
                    out_size = (args['fisheye_width'] // 4) * 4
                else:
                    out_size = 2800
                
                lut_file = args.get('lut_file')
                if lut_file and os.path.isfile(lut_file):
                    logging.info("Loading fisheye lookup table from: {}".format(lut_file))
                    print("Loading fisheye lookup table from: {}".format(lut_file))
                    import numpy as _np
                    _d = _np.load(lut_file)
                    face_lut, u_lut, v_lut = _d['face'], _d['u'], _d['v']
                else:
                    if lut_file:
                        print("Warning: --lut-file '{}' not found, generating LUT instead.".format(lut_file))
                    logging.info("Building fisheye lookup table (out_size={}, aa={})...".format(out_size, antialias))
                    print("Building fisheye lookup table (out_size={}, aa={})...".format(out_size, antialias))
                    face_lut, u_lut, v_lut = _max2fisheye.build_lookup_table(
                        out_size, antialias, which_template,
                    )

                out_tmpl = os.path.join(media_folder_full_path, 'lens%d_%06d.jpg')

                logging.info("Rendering fisheye frames...")
                num_cores = max(1, cpu_count() - 1)

                process_args = []
                for nframe in frame_numbers:
                    process_args.append((
                        nframe, seq_tmpl, face_lut, u_lut, v_lut, out_size, antialias,
                        which_template, out_tmpl, bool(args.get('debug', False)),
                        None  # No gyro data in folder mode
                    ))
                
                total_frames = len(frame_numbers)
                completed = 0
                failed_frames = []
                
                with Pool(processes=num_cores) as pool:
                    for result in pool.starmap(_process_fisheye_frame, process_args):
                        nframe, success = result
                        if not success:
                            failed_frames.append(nframe)
                        completed += 1
                        filled = int(45 * completed / total_frames)
                        bar = '█' * filled + '░' * (45 - filled)
                        pct = completed / total_frames * 100
                        print(f'\r  Fisheye   |{bar}| {completed}/{total_frames} ({pct:.1f}%)', end='', flush=True)
                
                print()
                
                if failed_frames:
                    logging.warning(f"Failed to process {len(failed_frames)} frames: {failed_frames[:10]}")
                    print(f"Warning: {len(failed_frames)} frames failed to process")

                # Move fisheye images to subfolders
                front_folder = os.path.join(media_folder_full_path, 'front')
                back_folder = os.path.join(media_folder_full_path, 'back')
                os.makedirs(front_folder, exist_ok=True)
                os.makedirs(back_folder, exist_ok=True)
                for nframe in frame_numbers:
                    src_front = os.path.join(media_folder_full_path, 'lens0_{:06d}.jpg'.format(nframe))
                    dst_front = os.path.join(front_folder, 'front_{:06d}.jpg'.format(nframe))
                    if os.path.exists(src_front):
                        os.rename(src_front, dst_front)
                    src_back = os.path.join(media_folder_full_path, 'lens1_{:06d}.jpg'.format(nframe))
                    dst_back = os.path.join(back_folder, 'back_{:06d}.jpg'.format(nframe))
                    if os.path.exists(src_back):
                        os.rename(src_back, dst_back)
            
            # 360° EQUIRECTANGULAR IMAGE GENERATION
            if run_360:
                out_width_360 = 4096
                out_height_360 = 2048
                
                logging.info("Building 360° equirectangular lookup table ({}×{}, aa={})...".format(
                    out_width_360, out_height_360, antialias))
                print("Building 360° equirectangular lookup table ({}×{}, aa={})...".format(
                    out_width_360, out_height_360, antialias))
                
                face_lut_360, u_lut_360, v_lut_360 = _max2sphere.build_lookup_table(
                    out_width_360, out_height_360, antialias, which_template,
                )
                
                out_tmpl_360 = os.path.join(media_folder_full_path, 'sphere_%06d.jpg')
                
                logging.info("Rendering 360° equirectangular frames...")
                num_cores = max(1, cpu_count() - 1)
                
                process_args_360 = []
                for nframe in frame_numbers:
                    process_args_360.append((
                        nframe, seq_tmpl, face_lut_360, u_lut_360, v_lut_360,
                        out_width_360, out_height_360, antialias,
                        which_template, out_tmpl_360, bool(args.get('debug', False))
                    ))
                
                total_frames = len(frame_numbers)
                completed = 0
                failed_frames_360 = []
                
                with Pool(processes=num_cores) as pool:
                    for result in pool.starmap(_process_360_frame, process_args_360):
                        nframe, success = result
                        if not success:
                            failed_frames_360.append(nframe)
                        completed += 1
                        filled = int(45 * completed / total_frames)
                        bar = '█' * filled + '░' * (45 - filled)
                        pct = completed / total_frames * 100
                        print(f'\r  360°      |{bar}| {completed}/{total_frames} ({pct:.1f}%)', end='', flush=True)
                
                print()
                
                if failed_frames_360:
                    logging.warning(f"Failed to process {len(failed_frames_360)} 360° frames: {failed_frames_360[:10]}")
                    print(f"Warning: {len(failed_frames_360)} 360° frames failed to process")
                
                # Move 360 images to subfolder
                e360_folder = os.path.join(media_folder_full_path, '360')
                os.makedirs(e360_folder, exist_ok=True)
                for nframe in frame_numbers:
                    src_360 = os.path.join(media_folder_full_path, 'sphere_{:06d}.jpg'.format(nframe))
                    dst_360 = os.path.join(e360_folder, '{:06d}.jpg'.format(nframe))
                    if os.path.exists(src_360):
                        os.rename(src_360, dst_360)

        except Exception as e:
            logging.error(str(e))
            print(str(e))
            import traceback
            traceback.print_exc()
            exit("Unable to process frames in folder mode.")

        logging.info("Folder mode processing complete.")
        print("Folder mode processing complete.")

    def __startProcessing(self):
        camera = ''
        equirectangular = False
        args = self.getArguments()
        media_folder_full_path = str(args["media_folder_full_path"].resolve())
        #validation max video file size
        if(len(args["input"]) == 1):
            video_file = str(args["input"][0].resolve())
            fileStat = os.stat(video_file)

        #validation fusion video file size
        elif(len(args["input"]) == 2):
            video_file_front = str(args["input"][0].resolve())
            video_file_back = str(args["input"][1].resolve())
            file_stat_front = os.stat(video_file_front)
            file_stat_back = os.stat(video_file_back)
            if file_stat_front.st_size > 4000000000:
                logging.critical("The following file {} is too large. The maximum size for a single video is 4GB".format(video_file_front))
                exit("The following file {} is too large. The maximum size for a single video is 4GB".format(video_file_front))
            if file_stat_back.st_size > 4000000000:
                logging.critical("The following file {} is too large. The maximum size for a single video is 4GB".format(file_stat_back))
                exit("The following file {} is too large. The maximum size for a single video is 4GB".format(file_stat_back))
        
        #getting video metadata
        metadata = self.__getVideoMetadata()

        #validation video
        self.__validateVideo(metadata["video_field_data"])

        fileType = args["file_type"].strip().lower()

        #checking if projection type is equirectangular
        if(metadata["video_field_data"]["ProjectionType"] == "equirectangular"):
            equirectangular = True
        if(metadata['video_field_data']['DeviceName'] == 'GoPro Max'):
            camera = 'max'
            if((equirectangular == False) and (args['predicted_camera'] == 'max') and (fileType == '360')):
                equirectangular = True
        if(metadata['video_field_data']['DeviceName'].lower() in ['gopro fusion', 'fusion']):
            camera = 'fusion'
            if((equirectangular == False) and (args['predicted_camera'] == 'fusion') and (fileType == 'mp4')):
                equirectangular = True
        
        #getting frames
        if fileType == "360":
            if camera == 'max':
                self.__breakIntoFrames360(metadata, video_file, media_folder_full_path)
            else:
                exit('Unknown camera type.')
        elif fileType in ["mp4", "mov"]:
            if camera == 'max':
                self.__breakIntoFrames(video_file, media_folder_full_path)
            elif camera == 'fusion':
                # Create track0 (front) and track5 (back) folders like Max structure
                track0 = os.path.join(media_folder_full_path, 'track0')
                track5 = os.path.join(media_folder_full_path, 'track5')
                os.makedirs(track0, exist_ok=True)
                os.makedirs(track5, exist_ok=True)
                
                # Extract frames from both videos with sharpness detection support
                logging.info("Processing Fusion front fisheye frames...")
                self.__breakIntoFrames(video_file_front, track0, '')
                
                logging.info("Processing Fusion back fisheye frames...")
                self.__breakIntoFrames(video_file_back, track5, '')
                
                # Note: Fusion fisheye frames are now in track0/ and track5/ folders
                # For 360° stitching, would need additional processing similar to max2sphere
                logging.info("Fusion fisheye frames extracted to track0/ and track5/")
            else:
                exit('Unknown camera type.')
        else:
            exit('Unknown file type.')
        # Geotagging is handled externally by geotag_images.py

    def __validateVideo(self, videoData):
        args = self.getArguments()
        #Validate Critical Errors
        #print(videoData)
        if videoData['MetaFormat'].strip()  != 'gpmd':
            metaFormat = False
            logging.critical("Your video has no telemetry. You need to enable GPS on your GoPro to ensure GPS location is captured.")
            exit("Your video has no telemetry. You need to enable GPS on your GoPro to ensure GPS location is captured.")
        else:
            metaFormat = True
        
        if videoData["ProjectionType"].strip()  != 'equirectangular':
            if metaFormat is False:
                logging.critical("This does not appear to be a GoPro 360 video. Only mp4 videos with a 360 equirectangular projection are accepted. Please make sure you are uploading 360 mp4 videos from your camera.")
                exit("This does not appear to be a GoPro 360 video. Only mp4 videos with a 360 equirectangular projection are accepted. Please make sure you are uploading 360 mp4 videos from your camera.")
        
        devices = ["Fusion", "GoPro Max"]
        if videoData['DeviceName'].strip() not in devices:
            logging.critical("This file does not look like it was captured using a GoPro camera. Only content taken using a GoPro 360 Camera are currently supported.")
            exit("This file does not look like it was captured using a GoPro camera. Only content taken using a GoPro 360 Camera are currently supported.")
        
        if args["frame_rate"] > 24:
            logging.warning("High frame rate extraction (>{} fps) will generate many frames and may take significant time and disk space.".format(args["frame_rate"]))
            print("High frame rate extraction (>{} fps) will generate many frames and may take significant time and disk space.".format(args["frame_rate"]))

        FileType = ["MP4", "360", "MOV"]
        if videoData["FileType"].strip().upper() not in FileType:
            logging.critical("The following filetype {} is not supported. Please upload only .mp4 or .360 videos.".format(videoData["FileType"]))
            exit("The following filetype {} is not supported. Please upload only .mp4 or .360 videos.".format(videoData["FileType"]))
        else:
            if videoData["FileType"].strip() == "360":
                if videoData["CompressorName"] == "H.265":
                    logging.critical("This does not appear to be a GoPro .360 file. Please use the .360 video created from your GoPro camera only.")
                    exit("This does not appear to be a GoPro .360 file. Please use the .360 video created from your GoPro camera only.")

    def __breakIntoFrames(self, filename, fileoutput, prefix=''):
        args = self.getArguments()
        logging.info("Running ffmpeg to extract images...")
        print("Please wait while image extraction is complete.\nRunning ffmpeg to extract images...")
        test_str = ""
        if args['debug'] is True:
            if "time_warp" in args:
                tw = "-t_{}x".format(args["time_warp"])
            else:
                tw = ""
            test_str = "-q_{}-r_{}fps{}".format(
                args["quality"], 
                args["frame_rate"], tw
            )
        
        # Check if sharpness detection is enabled
        detect_sharpness = args.get('detect_sharpness', False)
        crop_size = args.get('crop_size', 256)
        threshold = args.get('threshold', None)
        media_folder_full_path = str(args["media_folder_full_path"].resolve())
        frame_mapping_file = os.path.join(media_folder_full_path, 'frame_mapping.json')
        
        # Check if frame_mapping.json already exists
        if detect_sharpness and os.path.exists(frame_mapping_file):
            print("\n" + "="*60)
            print("REUSING EXISTING SHARPNESS DATA")
            print("="*60)
            print(f"Found existing frame_mapping.json, loading sharpness data...")

            # Load existing frame mapping
            with open(frame_mapping_file, 'r') as f:
                frame_mapping = json.load(f)

            # Convert frame_mapping back to selected_frames format
            selected_frames = [{'frame': info['original_frame'], 'time': info['time'], 'sharpness': info['sharpness']} 
                               for info in frame_mapping.values()]

            # Generate chart from loaded data
            analyzer = SharpnessAnalyzer(crop_size=crop_size, ffmpeg_path=str(args['ffmpeg']))
            video_basename = os.path.splitext(os.path.basename(filename))[0]
            chart_path = os.path.join(media_folder_full_path, f'{video_basename}_sharpness.html')
            analyzer.generate_sharpness_chart(chart_path, selected_frames, threshold, video_basename)

        elif detect_sharpness:
            # SHARPNESS-BASED FRAME SELECTION
            print("\n" + "="*60)
            print("SHARPNESS-BASED FRAME SELECTION ENABLED")
            print("="*60)

            analyzer = SharpnessAnalyzer(crop_size=crop_size, ffmpeg_path=str(args['ffmpeg']))
            max_seconds = args.get('max_seconds')

            # Analyze frames for sharpness
            frame_data = analyzer.analyze_frames(filename, max_seconds=max_seconds)

            if not frame_data:
                print("Warning: No frames could be analyzed, falling back to regular extraction")
                detect_sharpness = False
            else:
                # Select best frames based on target FPS and threshold
                selected_frames = analyzer.select_best_frames(args['frame_rate'], threshold)

                # Generate sharpness chart HTML
                media_folder_full_path = str(args["media_folder_full_path"].resolve())
                video_basename = os.path.splitext(os.path.basename(filename))[0]
                chart_path = os.path.join(media_folder_full_path, f'{video_basename}_sharpness.html')
                analyzer.generate_sharpness_chart(chart_path, selected_frames, threshold, video_basename)

                if not selected_frames:
                    print("Warning: No frames passed the threshold filter!")
                    if threshold is not None:
                        print(f"Consider lowering the threshold (current: {threshold})")
                    return

        # If sharpness-based selection is active and frames have been selected:
        if detect_sharpness and selected_frames:
            frame_nums = [f['frame'] for f in selected_frames]
            
            # Apply frame range filter if specified
            startf = args.get('startf')
            endf = args.get('endf')
            if startf is not None or endf is not None:
                original_count = len(frame_nums)
                # Convert to 0-based for comparison
                start_idx = (startf - 1) if startf is not None else 0
                end_idx = (endf - 1) if endf is not None else float('inf')
                frame_nums = [n for n in frame_nums if start_idx <= n <= end_idx]
                print(f"Frame range filter: {original_count} -> {len(frame_nums)} frames (startf={startf}, endf={endf})")
            
            select_expr = '+'.join([f'eq(n\\,{n})' for n in frame_nums])

            print(f"\nExtracting {len(frame_nums)} selected frames...")

            # Store frame mapping for reference
            frame_mapping_file = os.path.join(media_folder_full_path, 'frame_mapping.json')
            frame_mapping = {str(i+1): {'original_frame': f['frame'], 'time': f['time'], 'sharpness': f['sharpness']} 
                             for i, f in enumerate(selected_frames)}
            with open(frame_mapping_file, 'w') as f:
                json.dump(frame_mapping, f, indent=2)

            # Extract selected frames
            cmd = [
                "-i", filename,
                "-vf", f"select={select_expr}",
                "-vsync", "0",
                "-q:v", str(args["quality"]),
                "{}{}{}%06d.jpg".format(fileoutput, os.sep, prefix)
            ]
            output = self._ffmpeg(cmd, 1)

            # Rename extracted frames to match original frame numbers
            print("Renaming frames to original frame numbers...")
            for output_num, info in frame_mapping.items():
                original_frame = info['original_frame'] + 1  # Convert to 1-based
                src = os.path.join(fileoutput, f"{prefix}{int(output_num):06d}.jpg")
                dst = os.path.join(fileoutput, f"{prefix}{original_frame:06d}.jpg")
                if os.path.exists(src) and src != dst:
                    os.rename(src, dst)

            print(f"Extracted {len(frame_mapping)} frames based on sharpness analysis")
            print("="*60 + "\n")
            return

        # REGULAR FIXED-FPS EXTRACTION (original behavior)
        startf = args.get('startf')
        endf = args.get('endf')
        
        # Build filter expression for frame range
        vf_filters = []
        if startf is not None or endf is not None:
            # Use select filter with between() function for frame range
            # Note: frame numbers in ffmpeg are 0-based, so subtract 1
            start_idx = (startf - 1) if startf is not None else 0
            end_idx = (endf - 1) if endf is not None else 999999999
            vf_filters.append(f"select='between(n\\,{start_idx}\\,{end_idx})'")
            print(f"Frame range: extracting frames {startf if startf else 1} to {endf if endf else 'end'}")
        
        # Build fps filter
        vf_filters.append(f"fps={args['frame_rate']}")
        
        cmd = [
            "-i", filename
        ]
        
        if vf_filters:
            cmd.extend(["-vf", ','.join(vf_filters)])
        else:
            cmd.extend(["-r", str(args["frame_rate"])])
        
        cmd.extend([
            "-q:v", str(args["quality"]), 
            "{}{}{}%06d.jpg".format(fileoutput, os.sep, prefix)
        ])

        output = self._ffmpeg(cmd, 1)
        
    def __breakIntoFrames360(self, videoData, filename, fileoutput):
        args = self.getArguments()
        media_folder_full_path = str(args["media_folder_full_path"].resolve())
        logging.info("Running ffmpeg to extract images...")
        print("Please wait while image extraction is complete.\nRunning ffmpeg to extract images...")

        tracks = videoData['video_field_data']['CompressorNameTrack']
        if (type(tracks) == list) and (len(tracks) == 2):
            tracks[0] = 0 if (tracks[0]-1) < 0 else (tracks[0]-1)
            tracks[1] = 0 if (tracks[1]-1) < 0 else (tracks[1]-1)
            trackmapFirst = "0:{}".format(tracks[0])
            trackmapSecond = "0:{}".format(tracks[1])
        else:
            trackmapFirst = "0:{}".format(0)
            trackmapSecond = "0:{}".format(5)

        track0 = "{}{}{}".format(fileoutput, os.sep, 'track0')
        track5 = "{}{}{}".format(fileoutput, os.sep, 'track5')
        
        # Check if sharpness detection is enabled
        detect_sharpness = args.get('detect_sharpness', False)
        crop_size = args.get('crop_size', 256)
        threshold = args.get('threshold', None)
        frame_mapping_file = os.path.join(media_folder_full_path, 'frame_mapping.json')
        
        selected_frames = None  # Will be populated either from cache or analysis
        
        # Check if tracks already exist with images - if so, skip ffmpeg extraction
        track0_exists = os.path.exists(track0) and len(fnmatch.filter(os.listdir(track0), '*.jpg')) > 0
        track5_exists = os.path.exists(track5) and len(fnmatch.filter(os.listdir(track5), '*.jpg')) > 0
        
        # Check if frame_mapping.json already exists (reuse it if available)
        if detect_sharpness and os.path.exists(frame_mapping_file):
            print("\n" + "="*60)
            print("REUSING EXISTING SHARPNESS DATA")
            print("="*60)
            print(f"Found existing frame_mapping.json, loading sharpness data...")
            
            # Load existing frame mapping
            with open(frame_mapping_file, 'r') as f:
                frame_mapping = json.load(f)
            
            # Convert frame_mapping back to selected_frames format
            selected_frames = [{'frame': info['original_frame'], 'time': info['time'], 'sharpness': info['sharpness']} 
                             for info in frame_mapping.values()]
            
            # Generate chart from loaded data
            analyzer = SharpnessAnalyzer(crop_size=crop_size, ffmpeg_path=str(args['ffmpeg']))
            analyzer.frame_data = selected_frames
            analyzer.frame_data = selected_frames
            video_basename = os.path.splitext(os.path.basename(filename))[0]
            chart_path = os.path.join(media_folder_full_path, f'{video_basename}_sharpness.html')
            analyzer.generate_sharpness_chart(chart_path, selected_frames, threshold, video_basename)
            
            detect_sharpness = True  # Mark as successfully loaded
        
        if track0_exists and track5_exists:
            logging.info("Track folders already exist with images, skipping ffmpeg extraction...")
            print("Track folders already exist with images, skipping ffmpeg extraction...")
        else:
            # Tracks need to be created/recreated
            if selected_frames is not None:
                # We loaded sharpness data from cache, but tracks don't exist yet
                # Need to extract the frames using the cached frame list
                print("Cached sharpness data loaded, extracting frames using cached selection...")
                
                # Create track folders
                if os.path.exists(track0):
                    shutil.rmtree(track0)
                os.makedirs(track0, exist_ok=True) 
                if os.path.exists(track5):
                    shutil.rmtree(track5)
                os.makedirs(track5, exist_ok=True)
                
                # Use cached selected_frames for extraction
                detect_sharpness = True
            else:
                # Remove and recreate track folders for fresh extraction
                if os.path.exists(track0):
                    shutil.rmtree(track0)
                os.makedirs(track0, exist_ok=True) 
                if os.path.exists(track5):
                    shutil.rmtree(track5)
                os.makedirs(track5, exist_ok=True) 
            
            if detect_sharpness and selected_frames is None:
                # SHARPNESS-BASED FRAME SELECTION (only if not loaded from cache)
                # Analyze video and extract only the best frames
                print("\n" + "="*60)
                print("SHARPNESS-BASED FRAME SELECTION ENABLED")
                print("="*60)
                
                analyzer = SharpnessAnalyzer(crop_size=crop_size, ffmpeg_path=str(args['ffmpeg']))
                max_seconds = args.get('max_seconds')
                
                # Analyze frames for sharpness
                frame_data = analyzer.analyze_frames(filename, max_seconds=max_seconds)
                
                if not frame_data:
                    print("Warning: No frames could be analyzed, falling back to regular extraction")
                    detect_sharpness = False
                else:
                    # Select best frames based on target FPS and threshold
                    selected_frames = analyzer.select_best_frames(args['frame_rate'], threshold)
                    
                    # Generate sharpness chart HTML
                    video_basename = os.path.splitext(os.path.basename(filename))[0]
                    chart_path = os.path.join(media_folder_full_path, f'{video_basename}_sharpness.html')
                    analyzer.generate_sharpness_chart(chart_path, selected_frames, threshold, video_basename)
                    
                    if not selected_frames:
                        print("Warning: No frames passed the threshold filter!")
                        if threshold is not None:
                            print(f"Consider lowering the threshold (current: {threshold})")
                        return filename
            
            # Extract frames if sharpness detection is enabled and we have selected_frames
            if detect_sharpness and selected_frames is not None:
                # Build select filter expression for the selected frames
                # Frame numbers from analyzer are 0-based
                frame_nums = [f['frame'] for f in selected_frames]
                
                # Apply frame range filter if specified
                startf = args.get('startf')
                endf = args.get('endf')
                if startf is not None or endf is not None:
                    original_count = len(frame_nums)
                    # Convert to 0-based for comparison
                    start_idx = (startf - 1) if startf is not None else 0
                    end_idx = (endf - 1) if endf is not None else float('inf')
                    frame_nums = [n for n in frame_nums if start_idx <= n <= end_idx]
                    selected_frames = [f for f in selected_frames if start_idx <= f['frame'] <= end_idx]
                    print(f"Frame range filter: {original_count} -> {len(frame_nums)} frames (startf={startf}, endf={endf})")
                
                # Extract selected frames using select filter
                # Output frame numbers will be sequential (000001.jpg, 000002.jpg, etc.)
                # We need to map these back to original frame numbers later
                select_expr = '+'.join([f'eq(n\\,{n})' for n in frame_nums])
                
                print(f"\nExtracting {len(frame_nums)} selected frames...")
                
                # Store frame mapping (output_num -> original_frame_num) if not already cached
                # This is needed because ffmpeg select will output sequential numbers
                frame_mapping_file = os.path.join(media_folder_full_path, 'frame_mapping.json')
                if not os.path.exists(frame_mapping_file):
                    frame_mapping = {str(i+1): {'original_frame': f['frame'], 'time': f['time'], 'sharpness': f['sharpness']} 
                                    for i, f in enumerate(selected_frames)}
                    with open(frame_mapping_file, 'w') as f:
                        json.dump(frame_mapping, f, indent=2)
                else:
                    # Load existing mapping
                    with open(frame_mapping_file, 'r') as f:
                        frame_mapping = json.load(f)
                
                # Extract track0 (front camera)
                cmd_track0 = [
                    "-i", filename,
                    "-map", trackmapFirst,
                    "-vf", f"select={select_expr}",
                    "-vsync", "0",
                    "-q:v", str(args["quality"]),
                    track0 + os.sep + "%06d.jpg"
                ]
                output = self._ffmpeg(cmd_track0, 1)
                
                # Extract track5 (back camera)
                cmd_track5 = [
                    "-i", filename,
                    "-map", trackmapSecond,
                    "-vf", f"select={select_expr}",
                    "-vsync", "0",
                    "-q:v", str(args["quality"]),
                    track5 + os.sep + "%06d.jpg"
                ]
                output = self._ffmpeg(cmd_track5, 1)
                
                # Rename extracted frames to match original frame numbers
                # This maintains compatibility with the rest of the pipeline
                print("Renaming frames to original frame numbers...")
                for output_num, info in frame_mapping.items():
                    original_frame = info['original_frame'] + 1  # Convert to 1-based
                    src0 = os.path.join(track0, f"{int(output_num):06d}.jpg")
                    dst0 = os.path.join(track0, f"{original_frame:06d}.jpg")
                    if os.path.exists(src0) and src0 != dst0:
                        os.rename(src0, dst0)
                    
                    src5 = os.path.join(track5, f"{int(output_num):06d}.jpg")
                    dst5 = os.path.join(track5, f"{original_frame:06d}.jpg")
                    if os.path.exists(src5) and src5 != dst5:
                        os.rename(src5, dst5)
                
                print(f"Extracted {len(frame_mapping)} frames based on sharpness analysis")
                print("="*60 + "\n")
            
            if not detect_sharpness:
                # REGULAR FIXED-FPS EXTRACTION (original behavior)
                startf = args.get('startf')
                endf = args.get('endf')
                
                # Build filter expression for frame range
                vf_filter = None
                if startf is not None or endf is not None:
                    # Use select filter with between() function for frame range
                    start_idx = (startf - 1) if startf is not None else 0
                    end_idx = (endf - 1) if endf is not None else 999999999
                    vf_filter = f"select='between(n\\,{start_idx}\\,{end_idx})',setpts=N/FRAME_RATE/TB"
                    print(f"Frame range: extracting frames {startf if startf else 1} to {endf if endf else 'end'}")
                
                cmd = [
                    "-i", filename,
                    "-map", trackmapFirst
                ]
                
                if vf_filter:
                    cmd.extend(["-vf", vf_filter, "-vsync", "0"])
                else:
                    cmd.extend(["-r", str(args["frame_rate"])])
                
                cmd.extend([
                    "-q:v", str(args["quality"]),
                    track0 + os.sep + "%06d.jpg",
                    "-map", trackmapSecond
                ])
                
                if vf_filter:
                    cmd.extend(["-vf", vf_filter, "-vsync", "0"])
                else:
                    cmd.extend(["-r", str(args["frame_rate"])])
                
                cmd.extend([
                    "-q:v", str(args["quality"]), 
                    track5 + os.sep + "%06d.jpg"
                ])
                
                output = self._ffmpeg(cmd, 1)

        total_images = fnmatch.filter(os.listdir("{}{}{}".format(media_folder_full_path, os.sep, 'track0')), '*.jpg')
        total_images.sort()

        # Build frame_numbers respecting max_seconds
        all_frame_numbers = [int(os.path.splitext(f)[0]) for f in total_images]
        max_seconds = args.get('max_seconds')
        if max_seconds is not None:
            max_frame_count = max(1, int(max_seconds * args['frame_rate']))
            frame_numbers = all_frame_numbers[:max_frame_count]
        else:
            frame_numbers = all_frame_numbers
        max_frames = len(frame_numbers)

        # Get video duration via ffprobe
        try:
            _probe = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', filename],
                capture_output=True, text=True, check=True,
            )
            duration = float(json.loads(_probe.stdout)['format']['duration'])
        except Exception:
            duration = max_frames / args['frame_rate'] if args['frame_rate'] else float(max_frames)

        try:
            # Determine template and output size from the first frame pair
            which_template, fw, fh = _max2fisheye.check_frames(
                os.path.join(track0, total_images[0]),
                os.path.join(track5, total_images[0]),
            )
            
            # Determine which modes to run
            fisheye_only = args.get('fisheye_only', False)
            e360_only = args.get('e360_only', False)
            run_fisheye = not e360_only  # Run fisheye unless --360only is set
            run_360 = not fisheye_only   # Run 360 unless --fisheyeonly is set
            
            antialias = int(args.get('antialias', 2))
            seq_tmpl = os.path.join(media_folder_full_path, 'track%d', '%06d.jpg')
            
            # ═══════════════════════════════════════════════════════════════════
            # FISHEYE IMAGE GENERATION
            # ═══════════════════════════════════════════════════════════════════
            if run_fisheye:
                # If lens0/lens1 already exist in main folder, skip fisheye generation.
                existing_lens0 = fnmatch.filter(os.listdir(media_folder_full_path), 'lens0_*.jpg')
                existing_lens1 = fnmatch.filter(os.listdir(media_folder_full_path), 'lens1_*.jpg')
                front_folder = os.path.join(media_folder_full_path, 'front')
                back_folder = os.path.join(media_folder_full_path, 'back')
                existing_front = os.path.exists(front_folder) and len(fnmatch.filter(os.listdir(front_folder), '*.jpg')) > 0
                existing_back = os.path.exists(back_folder) and len(fnmatch.filter(os.listdir(back_folder), '*.jpg')) > 0

                skip_fisheye = False
                if existing_lens0 and existing_lens1:
                    logging.info("Existing lens0/lens1 fisheye images found, skipping fisheye generation.")
                    print("Existing lens0/lens1 fisheye images found, skipping fisheye generation...")
                    skip_fisheye = True
                elif existing_front and existing_back:
                    logging.info("Existing front/back fisheye images found, skipping fisheye generation.")
                    print("Existing front/back fisheye images found, skipping fisheye generation...")
                    skip_fisheye = True

                if skip_fisheye:
                    logging.info("Skipping fisheye render and using existing images")
                    print("Skipping fisheye render and using existing images")
                else:
                    # Use fisheye_width from args if provided, otherwise default to 2800
                    if args.get('fisheye_width') is not None:
                        out_size = (args['fisheye_width'] // 4) * 4
                    else:
                        out_size = 2800

                    lut_file = args.get('lut_file')
                    if lut_file and os.path.isfile(lut_file):
                        logging.info("Loading fisheye lookup table from: {}".format(lut_file))
                        print("Loading fisheye lookup table from: {}".format(lut_file))
                        import numpy as _np
                        _d = _np.load(lut_file)
                        face_lut, u_lut, v_lut = _d['face'], _d['u'], _d['v']
                    else:
                        if lut_file:
                            print("Warning: --lut-file '{}' not found, generating LUT instead.".format(lut_file))
                        logging.info("Building fisheye lookup table (out_size={}, aa={})...".format(out_size, antialias))
                        print("Building fisheye lookup table (out_size={}, aa={})...".format(out_size, antialias))
                        face_lut, u_lut, v_lut = _max2fisheye.build_lookup_table(
                            out_size, antialias, which_template,
                        )

                    # Output: lens0 = front fisheye, lens1 = back fisheye
                    out_tmpl = os.path.join(media_folder_full_path, 'lens%d_%06d.jpg')

                    logging.info("Rendering fisheye frames...")

                    # Extract and integrate gyro for per-frame roll
                    gyro_samples = parse_gpmf_gyro(filename)
                    gyro_roll = {}
                    if gyro_samples:
                        gyro_roll = integrate_gyro_roll(gyro_samples, duration, args['frame_rate'], max_frames)

                    # Use multiprocessing to parallelize fisheye generation
                    num_cores = max(1, cpu_count() - 1)

                    process_args = []
                    for nframe in frame_numbers:
                        process_args.append((
                            nframe, seq_tmpl, face_lut, u_lut, v_lut, out_size, antialias,
                            which_template, out_tmpl, bool(args.get('debug', False)),
                            gyro_roll if gyro_roll else None
                        ))
                    
                    # Process with progress bar - use imap_unordered for real-time updates
                    total_frames = len(frame_numbers)
                    completed = 0
                    failed_frames = []
                    
                    with Pool(processes=num_cores) as pool:
                        # starmap processes args as tuples
                        for result in pool.starmap(_process_fisheye_frame, process_args):
                            nframe, success = result
                            if not success:
                                failed_frames.append(nframe)
                            completed += 1
                            # Progress bar updates with each completed frame
                            filled = int(45 * completed / total_frames)
                            bar = '█' * filled + '░' * (45 - filled)
                            pct = completed / total_frames * 100
                            print(f'\r  Fisheye   |{bar}| {completed}/{total_frames} ({pct:.1f}%)', end='', flush=True)
                    
                    print()  # New line after progress bar
                    
                    # Check for any failures
                    if failed_frames:
                        logging.warning(f"Failed to process {len(failed_frames)} frames: {failed_frames[:10]}")
                        print(f"Warning: {len(failed_frames)} frames failed to process")

                    # Move front (lens0) to front/ and back (lens1) to back/ subfolders
                    front_folder = os.path.join(media_folder_full_path, 'front')
                    back_folder = os.path.join(media_folder_full_path, 'back')
                    os.makedirs(front_folder, exist_ok=True)
                    os.makedirs(back_folder, exist_ok=True)
                    for nframe in frame_numbers:
                        src_front = os.path.join(media_folder_full_path, 'lens0_{:06d}.jpg'.format(nframe))
                        dst_front = os.path.join(front_folder, 'front_{:06d}.jpg'.format(nframe))
                        if os.path.exists(src_front):
                            os.rename(src_front, dst_front)
                        src_back = os.path.join(media_folder_full_path, 'lens1_{:06d}.jpg'.format(nframe))
                        dst_back = os.path.join(back_folder, 'back_{:06d}.jpg'.format(nframe))
                        if os.path.exists(src_back):
                            os.rename(src_back, dst_back)

            # ═══════════════════════════════════════════════════════════════════
            # 360° EQUIRECTANGULAR IMAGE GENERATION
            # ═══════════════════════════════════════════════════════════════════
            if run_360:
                # Default 360 output: 4096×2048
                out_width_360 = 4096
                out_height_360 = 2048
                
                logging.info("Building 360° equirectangular lookup table ({}×{}, aa={})...".format(
                    out_width_360, out_height_360, antialias))
                print("Building 360° equirectangular lookup table ({}×{}, aa={})...".format(
                    out_width_360, out_height_360, antialias))
                
                face_lut_360, u_lut_360, v_lut_360 = _max2sphere.build_lookup_table(
                    out_width_360, out_height_360, antialias, which_template,
                )
                
                # Output: sphere_NNNNNN.jpg
                out_tmpl_360 = os.path.join(media_folder_full_path, 'sphere_%06d.jpg')
                
                logging.info("Rendering 360° equirectangular frames...")
                
                # Use multiprocessing to parallelize 360 generation
                num_cores = max(1, cpu_count() - 1)
                
                process_args_360 = []
                for nframe in frame_numbers:
                    process_args_360.append((
                        nframe, seq_tmpl, face_lut_360, u_lut_360, v_lut_360,
                        out_width_360, out_height_360, antialias,
                        which_template, out_tmpl_360, bool(args.get('debug', False))
                    ))
                
                # Process with progress bar
                total_frames = len(frame_numbers)
                completed = 0
                failed_frames_360 = []
                
                with Pool(processes=num_cores) as pool:
                    for result in pool.imap(_process_360_frame_wrapper, process_args_360):
                        nframe, success = result
                        if not success:
                            failed_frames_360.append(nframe)
                        completed += 1
                        filled = int(45 * completed / total_frames)
                        bar = '█' * filled + '░' * (45 - filled)
                        pct = completed / total_frames * 100
                        print(f'\r  360°      |{bar}| {completed}/{total_frames} ({pct:.1f}%)', end='', flush=True)
                
                print()  # New line after progress bar
                
                # Check for any failures
                if failed_frames_360:
                    logging.warning(f"Failed to process {len(failed_frames_360)} 360° frames: {failed_frames_360[:10]}")
                    print(f"Warning: {len(failed_frames_360)} 360° frames failed to process")
                
                # Move 360 images to 360/ subfolder
                e360_folder = os.path.join(media_folder_full_path, '360')
                os.makedirs(e360_folder, exist_ok=True)
                for nframe in frame_numbers:
                    src_360 = os.path.join(media_folder_full_path, 'sphere_{:06d}.jpg'.format(nframe))
                    dst_360 = os.path.join(e360_folder, '{:06d}.jpg'.format(nframe))
                    if os.path.exists(src_360):
                        os.rename(src_360, dst_360)

        except Exception as e:
            logging.info(str(e))
            print(str(e))
            exit("Unable to convert 360 deg video.")

        # Keep track folders for re-running tests (they will be reused on next run)
        logging.info("Keeping track0 and track5 folders for reuse.")
        return filename

    def __getVideoMetadata(self):
        args = self.getArguments()
        exif_xml_data = self.get_video_exif_data()
        xmlFileName = "{}{}{}.xml".format(args["media_folder_full_path"], os.sep, args["media_folder"])
        self.__saveAFile(xmlFileName, exif_xml_data)
        if(Path(xmlFileName).is_file() == False):
            exit('Unable to save xml file: {}'.format(xmlFileName))
        return self.__parseMetadata(xmlFileName)

    def __parseMetadata(self, xmlFileName):
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

        output = GoProFrameMakerHelper.gpsTimestamps(gpsData, videoFieldData, sensorData)
        args = self.getArguments()
        output["filename"] = "{}{}{}_video.gpx".format(args["media_folder_full_path"], os.sep, args["media_folder"])
        self.__saveAFile(output["filename"], output['gpx_data'])
        if(Path(output["filename"]).is_file() == False):
            exit('Unable to save file: {}'.format(output["filename"]))

        return {
            "filename": output["filename"],
            "startTime": output["start_time"],
            "video_field_data": videoFieldData,
            "sensor_data": sensorData
        }

    def __gpsTimestamps(self, gpsData, videoFieldData):
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
                    tBlock["GPSDateTime"] = new[icounter]
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
                first_start_time = datetime.datetime.strptime(gpsData[0]["GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
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
                new = pd.date_range(start=start_time, end=end_time, closed='left', freq="{}ms".format(diff))
                icounter = 0
                dlLen = 1 if len(start_gps["GPSData"]) < 1 else len(start_gps["GPSData"])
                nlLen = 1 if len(new) < 1 else len(new)
                _ms = math.floor(dlLen/nlLen)
                _ms = 1 if _ms < 1 else _ms
                for gps in start_gps["GPSData"]:
                    tBlock = gps.copy()
                    tBlock["GPSDateTime"] = new[icounter]
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
            start_latitude = self.latLngToDecimal(gps["GPSLatitude"])
            start_longitude = self.latLngToDecimal(gps["GPSLongitude"])
            start_altitude = self.getAltitudeFloat(gps["GPSAltitude"])
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
                end_latitude = self.latLngToDecimal(Timestamps[icounter+1]["GPSLatitude"])
                end_longitude = self.latLngToDecimal(Timestamps[icounter+1]["GPSLongitude"])
                end_altitude = self.getAltitudeFloat(Timestamps[icounter+1]["GPSAltitude"])

                ext = self.calculateExtensions(
                    gps, 
                    (start_time, end_time, gps_epoch_seconds),
                    (
                        (start_latitude, start_longitude, start_altitude),
                        (end_latitude, end_longitude, end_altitude)
                    ),
                    1, 1
                )
            else:
                ext = self.calculateExtensions(
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
        args = self.getArguments()

        filename = "{}{}{}_video.gpx".format(args["media_folder_full_path"], os.sep, args["media_folder"])
        self.__saveAFile(filename, gpxData)
        if(Path(filename).is_file() == False):
            exit('Unable to save file: {}'.format(filename))

        return {
            "filename": filename,
            "startTime": Timestamps[0]['GPSDateTime']
        }

    def __saveAFile(self, filename, data):
        logging.info("Trying to save file: {}".format(filename))
        with open(filename, "w") as f:
            f.write(data)
            f.close()
        logging.info("Unable to save file: {}".format(filename))

    def __updateImagesMetadata(self, data, equirectangular):
        args = self.getArguments()
        media_folder_full_path = str(args["media_folder_full_path"].resolve())
        print("Starting to inject additional metadata into the images...")

        gpx = gpxpy.gpx.GPX()

        # Create first track in our GPX:
        gpx_track = gpxpy.gpx.GPXTrack()
        gpx.tracks.append(gpx_track)

        # Create first segment in our GPX track:
        gpx_segment = gpxpy.gpx.GPXTrackSegment()
        gpx_track.segments.append(gpx_segment)

        counter = 0
        photosLen = len(data['images'])
        t1970 = datetime.datetime.strptime("1970:01:01 00:00:00.000000", "%Y:%m:%d %H:%M:%S.%f")

        imageData = {}
        front_images_folder = os.path.join(media_folder_full_path, 'front')
        images_folder = front_images_folder if os.path.exists(front_images_folder) else media_folder_full_path
        imageData = ExiftoolGetImagesMetadata(images_folder, data['images'], imageData)

        cmdMetaDataAll = []

        counter = 0
        for img in data['images']:
            if counter < photosLen - 1:
                photo = [data['images'][counter], data['images'][counter + 1]]

                start_photo = imageData[data['images'][counter]]
                end_photo   = imageData[data['images'][counter + 1]]

                #Get Times from metadata
                start_time = datetime.datetime.strptime(start_photo["Main:GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
                end_time = datetime.datetime.strptime(end_photo["Main:GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
                time_diff = (end_time - start_time).total_seconds()
                gps_epoch_seconds = (start_time-t1970).total_seconds()

                #Get Latitude, Longitude and Altitude
                start_latitude = self.latLngToDecimal(start_photo["Main:GPSLatitude"])
                start_longitude = self.latLngToDecimal(start_photo["Main:GPSLongitude"])
                start_altitude = self.getAltitudeFloat(start_photo["Main:GPSAltitude"])
                end_latitude = self.latLngToDecimal(end_photo["Main:GPSLatitude"])
                end_longitude = self.latLngToDecimal(end_photo["Main:GPSLongitude"])
                end_altitude = self.getAltitudeFloat(end_photo["Main:GPSAltitude"])

                ext = self.calculateExtensions(
                    start_photo, 
                    (start_time, end_time, gps_epoch_seconds),
                    (
                        (start_latitude, start_longitude, start_altitude),
                        (end_latitude, end_longitude, end_altitude)
                    ),
                    1, 0
                )
            else:
                photo = [data['images'][counter], None]

                start_photo = imageData[data['images'][counter]]

                #Get Times from metadata
                start_time = datetime.datetime.strptime(start_photo["Main:GPSDateTime"].replace("Z", ""), "%Y:%m:%d %H:%M:%S.%f")
                gps_epoch_seconds = (start_time-t1970).total_seconds()

                ext = self.calculateExtensions(
                    start_photo, 
                    (start_time, None, gps_epoch_seconds),
                    (
                        (start_latitude, start_longitude, start_altitude),
                        (None, None, None)
                    ),
                    0, 0
                )
            gpx_point = gpxpy.gpx.GPXTrackPoint(
                latitude=start_latitude, 
                longitude=start_longitude, 
                time=start_time, 
                elevation=start_altitude
            )
            gpx_segment.points.append(gpx_point)
            kms = ext["gps_speed_next_kmeters_second"]
            del ext["gps_speed_next_kmeters_second"]
            for k, v in ext.items():
                gpx_extension = ET.fromstring(f"""
                    <{str(k)}>{str(v)}</{str(k)}>
                """)
                gpx_point.extensions.append(gpx_extension)

            cmdMetaData = [
                '-DateTimeOriginal={0}'.format(start_photo["Main:DateTimeOriginal"]),
                '-SubSecTimeOriginal={0}'.format(start_photo["Main:SubSecTimeOriginal"]),
                '-SubSecDateTimeOriginal={0}'.format(start_photo["Main:SubSecDateTimeOriginal"]),

                '-GPSDateTime={0}"'.format(start_photo["Main:GPSDateTime"]),
                '-GPSLatitude="{0}"'.format(start_photo["Main:GPSLatitude"]),
                '-GPSLongitude="{0}"'.format(start_photo["Main:GPSLongitude"]),
                '-GPSAltitude="{0}"'.format(start_photo["Main:GPSAltitude"]),

                '-GPSSpeed={}'.format(kms),
                '-GPSSpeedRef=k',
                '-GPSImgDirection={}'.format(ext['gps_heading_next_degrees']),
                '-GPSImgDirectionRef=m',
                '-GPSPitch={}'.format(ext['gps_pitch_next_degrees']),
                '-IFD0:Model="{}"'.format(self.removeEntities(data["video_field_data"]["DeviceName"]))
            ]
            
            # Add sensor data if available - match by timestamp
            if 'sensor_data' in data and data['sensor_data']:
                # Calculate frame timestamp relative to video start
                args = self.getArguments()
                video_start = data.get('startTime', start_time)
                frame_time = (start_time - video_start).total_seconds()
                
                # Find the sensor block that matches this frame's timestamp
                sensor_info = None
                value_index = 0
                
                for i, sensor_block in enumerate(data['sensor_data']):
                    block_timestamp = sensor_block.get('TimeStamp', 0.0)
                    # Check if this frame falls within this sensor block's time range
                    # Each block covers ~1 second with 24 values
                    if i < len(data['sensor_data']) - 1:
                        next_timestamp = data['sensor_data'][i + 1].get('TimeStamp', block_timestamp + 1.0)
                    else:
                        next_timestamp = block_timestamp + 1.0
                    
                    if block_timestamp <= frame_time < next_timestamp:
                        sensor_info = sensor_block
                        # Calculate which value index to use within the 24 values
                        time_within_block = frame_time - block_timestamp
                        block_duration = next_timestamp - block_timestamp
                        # There are typically 24 values per block for ~24fps
                        value_index = int((time_within_block / block_duration) * 24)
                        value_index = max(0, min(value_index, 23))  # Clamp to 0-23
                        break
                
                # Fallback to simple index matching if timestamp matching fails
                if sensor_info is None and len(data['sensor_data']) > 0:
                    sensor_idx = min(counter, len(data['sensor_data']) - 1)
                    sensor_info = data['sensor_data'][sensor_idx]
                    value_index = 0
                
                if sensor_info:
                    # Add ISO if available
                    if 'ISOSpeeds' in sensor_info:
                        iso_values = sensor_info['ISOSpeeds'].split()
                        if iso_values and value_index < len(iso_values):
                            cmdMetaData.append('-ISO={}'.format(iso_values[value_index]))
                        elif iso_values:
                            cmdMetaData.append('-ISO={}'.format(iso_values[0]))
                    
                    # Add Shutter Speed / Exposure Time if available
                    if 'ExposureTimes' in sensor_info:
                        exposure_values = sensor_info['ExposureTimes'].split()
                        if exposure_values and value_index < len(exposure_values):
                            cmdMetaData.append('-ExposureTime={}'.format(exposure_values[value_index]))
                            cmdMetaData.append('-ShutterSpeed={}'.format(exposure_values[value_index]))
                        elif exposure_values:
                            cmdMetaData.append('-ExposureTime={}'.format(exposure_values[0]))
                            cmdMetaData.append('-ShutterSpeed={}'.format(exposure_values[0]))
                    
                    # Add note about accelerometer and gyroscope data
                    if 'Accelerometer' in sensor_info or 'Gyroscope' in sensor_info:
                        sensor_note = []
                        if 'Accelerometer' in sensor_info:
                            sensor_note.append('Accelerometer')
                        if 'Gyroscope' in sensor_info:
                            sensor_note.append('Gyroscope')
                        cmdMetaData.append('-UserComment=Sensor data available: {}'.format(', '.join(sensor_note)))
            
            if (data["video_field_data"]["ProjectionType"] == "equirectangular") or ("360ProjectionType" in data["video_field_data"]):
                cmdMetaData.append('-XMP-GPano:StitchingSoftware="Spherical Metadata Tool"')
                cmdMetaData.append('-XMP-GPano:ProjectionType="equirectangular"')
                cmdMetaData.append('-XMP-GPano:SourcePhotosCount="{}"'.format(2))
                cmdMetaData.append('-XMP-GPano:UsePanoramaViewer="{}"'.format("TRUE"))
                cmdMetaData.append('-XMP-GPano:CroppedAreaImageHeightPixels="{}"'.format(data["video_field_data"]["SourceImageHeight"]))
                cmdMetaData.append('-XMP-GPano:CroppedAreaImageWidthPixels="{}"'.format(data["video_field_data"]["SourceImageWidth"]))
                cmdMetaData.append('-XMP-GPano:FullPanoHeightPixels="{}"'.format(data["video_field_data"]["SourceImageHeight"]))
                cmdMetaData.append('-XMP-GPano:FullPanoWidthPixels="{}"'.format(data["video_field_data"]["SourceImageWidth"]))
                cmdMetaData.append('-XMP-GPano:CroppedAreaLeftPixels="{}"'.format(0))
                cmdMetaData.append('-XMP-GPano:CroppedAreaTopPixels="{}"'.format(0))
            cmdMetaData.append('-overwrite_original')
            cmdMetaData.append("{}{}{}".format(images_folder, os.sep, photo[0]))
            cmdMetaDataAll.append(cmdMetaData)
            counter = counter + 1
        ExiftoolInjectImagesMetadata(cmdMetaDataAll)
        gpxData = gpx.to_xml()
        filename = "{}{}{}_photos.gpx".format(args["media_folder_full_path"], os.sep, args["media_folder"])
        self.__saveAFile(filename, gpxData)
        if(Path(filename).is_file() == False):
            exit('Unable to save file: {}'.format(filename))

import subprocess, argparse, time, os, sys
from colorama import init, Fore, Style
from gfmhelper import GoProFrameMakerHelper
from gfmmain import GoProFrameMaker

if __name__ == '__main__':
    init(autoreset=True)
    print("\n")
    print(Fore.YELLOW + "########################################")
    print(Fore.YELLOW + "#           GOPRO FRAME MAKER          #")
    print(Fore.YELLOW + "########################################")
    print(Style.RESET_ALL)
    time.sleep(1)

    #parsing command line arguments
    parser = argparse.ArgumentParser()

    #input video files or folder
    parser.add_argument("input", type=str, help="Input a valid video file OR a folder containing track0 and track5 subdirectories.", nargs="+", )
    
    #fisheye resolution (available in both config and no-config mode)
    parser.add_argument("-w", "--fisheye-width", type=int, help="Output fisheye image diameter in pixels (default: uses frame height).", default=None)
    
    #frame rate (available in both config and no-config mode, overrides config if specified)
    parser.add_argument("-r", "--frame-rate", type=float, help="Sets the frame rate (frames per second) for extraction (any value from 0.1 to 30), default: from config or 2.0.", default=None)

    #max seconds to process (for testing)
    parser.add_argument("--max-seconds", type=float, help="Only process the first N seconds of video (for testing). Default: process all.", default=None)

    #frame range options
    parser.add_argument("--startf", type=int, help="Starting frame number (1-based). Only extract frames starting from this frame.", default=None)
    parser.add_argument("--endf", type=int, help="Ending frame number (1-based). Only extract frames up to and including this frame.", default=None)

    #sharpness detection options
    parser.add_argument("--detect-sharpness", action='store_true', help="Analyze video for sharpness and select best frames from each interval. Analyzes every frame using crop regions.")
    parser.add_argument("--crop-size", type=int, default=256, choices=[64, 128, 256, 384, 512], help="Size of crop squares for sharpness analysis (default: 256).")
    parser.add_argument("--threshold", type=float, default=None, help="Minimum sharpness score (0-100) for frame extraction. Frames below this threshold will be skipped.")
    
    #output mode selection
    parser.add_argument("--fisheyeonly", action='store_true', help="Generate only fisheye images (skip 360° equirectangular).", dest='fisheye_only')
    parser.add_argument("--360only", action='store_true', help="Generate only 360° equirectangular images (skip fisheye).", dest='e360_only')

    #check if .config is available
    cfg = GoProFrameMakerHelper.getConfig()
    if cfg['status'] == False:

        #ffmpeg binary
        parser.add_argument("-f", "--ffmpeg-path", type=str, help="Set the path for ffmpeg.")
        #ffmpeg options
        parser.add_argument("-q", "--quality", type=int, help="Sets the extracted quality between 2-6. 1 being the highest quality (but slower processing), default: 1. This is value used for ffmpeg -q:v flag. ", default=1)
        
        #debug option
        parser.add_argument("-d", "--debug", action='store_true', help="Enable debug mode, default: off.")

        #pre-made lookup table
        parser.add_argument("--lut-file", type=str, help="Path to a pre-made fisheye LUT .npz file. Skips LUT generation if provided.", default=None)

        #getting args
        args = parser.parse_args()

        #validate arguments
        gfmValidated = GoProFrameMakerHelper.validateArgs(args)
    else:
        #pre-made lookup table (config path)
        parser.add_argument("--lut-file", type=str, help="Path to a pre-made fisheye LUT .npz file. Skips LUT generation if provided.", default=None)

        #getting args
        args = parser.parse_args()

        #get config default
        default = cfg['config']
        default['input'] = args.input
        # Pass fisheye_width if provided via command line
        if args.fisheye_width is not None:
            default['fisheye_width'] = args.fisheye_width
        # Pass frame_rate if provided via command line (overrides config)
        if args.frame_rate is not None:
            default['frame_rate'] = args.frame_rate
        # Pass max_seconds if provided via command line
        if args.max_seconds is not None:
            default['max_seconds'] = args.max_seconds
        else:
            default['max_seconds'] = None
        # Pass frame range if provided
        default['startf'] = args.startf
        default['endf'] = args.endf
        # Pass output mode flags
        default['fisheye_only'] = args.fisheye_only
        default['e360_only'] = args.e360_only
        # Pass lut_file if provided
        default['lut_file'] = args.lut_file
        # Pass sharpness detection options
        default['detect_sharpness'] = args.detect_sharpness
        default['crop_size'] = args.crop_size
        default['threshold'] = args.threshold
        args = type('args', (object,), default)

        #validate arguments
        gfmValidated = GoProFrameMakerHelper.validateArgs(args)

    for info in gfmValidated['info']:
        print(Fore.BLUE + info)
        print(Style.RESET_ALL)

    for error in gfmValidated['errors']:
        print(Fore.RED + error)
        print(Style.RESET_ALL)
        exit(0)

    # Inject testing/convenience params into validated args
    gfmValidated['args']['max_seconds'] = getattr(args, 'max_seconds', None) if hasattr(args, 'max_seconds') else None
    gfmValidated['args']['startf'] = getattr(args, 'startf', None) if hasattr(args, 'startf') else None
    gfmValidated['args']['endf'] = getattr(args, 'endf', None) if hasattr(args, 'endf') else None
    gfmValidated['args']['lut_file'] = getattr(args, 'lut_file', None) if hasattr(args, 'lut_file') else None
    gfmValidated['args']['fisheye_only'] = getattr(args, 'fisheye_only', False) if hasattr(args, 'fisheye_only') else False
    gfmValidated['args']['e360_only'] = getattr(args, 'e360_only', False) if hasattr(args, 'e360_only') else False
    # Inject sharpness detection params
    gfmValidated['args']['detect_sharpness'] = getattr(args, 'detect_sharpness', False) if hasattr(args, 'detect_sharpness') else False
    gfmValidated['args']['crop_size'] = getattr(args, 'crop_size', 256) if hasattr(args, 'crop_size') else 256
    gfmValidated['args']['threshold'] = getattr(args, 'threshold', None) if hasattr(args, 'threshold') else None

    if((gfmValidated['status'] == True) and (len(gfmValidated['errors']) == 0)):
        gfm = GoProFrameMaker(gfmValidated['args'])
        selected_args = gfm.getArguments()
        
        gfm.initiateProcessing()
        
        # Skip geotagging if in folder mode (no video file available)
        if not gfmValidated['args'].get('folder_mode', False):
            print(Fore.GREEN + "\nFrame extraction finished. Starting geotagging...")
            print(Style.RESET_ALL)

            geotag_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'geotag_images.py')
            geotag_folder = str(selected_args['media_folder_full_path'])
            geotag_fps = str(selected_args['frame_rate'])
            geotag_cmd = [sys.executable, geotag_script, geotag_folder, '-r', geotag_fps]
            geotag_result = subprocess.run(geotag_cmd)

            if geotag_result.returncode != 0:
                print(Fore.RED + "\nGeotagging failed! Check the output above for details.")
            else:
                print(Fore.GREEN + "\nProcessing finished! If there are no images in the folder please see logs to gain additional information.")
                print(Fore.GREEN + "\nYou can see {} folder to see the images.".format(selected_args['media_folder_full_path']))
        else:
            print(Fore.GREEN + "\nProcessing finished! (GPS tagging skipped - no video file)")
            print(Fore.GREEN + "\nYou can see {} folder to see the images.".format(selected_args['media_folder_full_path']))
        
        print(Fore.BLUE + "\nHave a nice day!")
        print(Style.RESET_ALL)
        exit(0)
            
    else:
        input(Fore.RED + "Processing stopped!")
        print(Style.RESET_ALL)
    exit(0)
    


    


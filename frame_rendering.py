"""
Frame rendering workers for parallel processing of GoPro Max 360 frames.
Handles both fisheye and 360° equirectangular frame processing.
"""

import logging
import os
import sys

# Import max2sphere modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'max2sphere'))
import max2sphere as _max2sphere
import max2fisheye as _max2fisheye


def _process_fisheye_frame(nframe, seq_tmpl, face_lut, u_lut, v_lut, out_size, antialias,
                           which_template, out_tmpl, debug,
                           gyro_roll=None):
    """Process a single fisheye frame - used for parallel processing"""
    try:
        _max2fisheye.process_frame(
            nframe, seq_tmpl,
            face_lut, u_lut, v_lut,
            out_size, antialias, which_template,
            out_tmpl, debug,
        )

        from PIL import Image

        gyro_angles = {}
        if gyro_roll:
            roll_deg = gyro_roll.get(nframe, 0.0)
            gyro_angles[0] = -roll_deg   # front lens
            gyro_angles[1] =  roll_deg   # back lens

        # Compose all transforms in memory, save once per lens
        for lens in range(2):
            img_path = out_tmpl % (lens, nframe)
            if os.path.exists(img_path):
                img = Image.open(img_path)
                # 1) gyro correction (if applicable)
                angle = gyro_angles.get(lens, 0.0)
                if abs(angle) > 0.5:
                    img = img.rotate(angle, resample=Image.BICUBIC,
                                     expand=False, fillcolor=(0, 0, 0))
                # 2) orientation fix: 180° rotation then horizontal flip
                img = img.rotate(180)
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
                img.save(img_path, quality=95)

        return nframe, True
    except Exception as e:
        logging.error(f"Error processing frame {nframe}: {e}")
        return nframe, False


def _process_360_frame(nframe, seq_tmpl, face_lut, u_lut, v_lut, out_width, out_height,
                       antialias, which_template, out_tmpl, debug):
    """Process a single 360° equirectangular frame - used for parallel processing"""
    try:
        _max2sphere.process_frame(
            nframe, seq_tmpl,
            face_lut, u_lut, v_lut,
            out_width, out_height, antialias, which_template,
            out_tmpl, debug,
        )
        return nframe, True
    except Exception as e:
        logging.error(f"Error processing 360 frame {nframe}: {e}")
        return nframe, False


def _process_360_frame_wrapper(args):
    """Wrapper to unpack tuple for imap compatibility"""
    return _process_360_frame(*args)

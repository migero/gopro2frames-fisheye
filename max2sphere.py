#!/usr/bin/env python3
"""
GoPro Max dual-lens frames → equirectangular 360° image
Based on the C implementation of max2sphere.c

Usage:
    python max2sphere.py [options] track%d/frame%04d.jpg

    -w N   Output image width in pixels (default: 4096)
    -a N   Antialiasing level (default: 2)
    -o S   Output filename template — must contain ONE %d field for frame number
           Default: derived from input path, e.g. track0/frame0001_sphere.jpg
    -n N   Start frame index (default: 0)
    -m N   End frame index   (default: 100000)
    -d     Enable debug / verbose output
"""

import argparse
import math
import os
import sys
import time

import numpy as np
from PIL import Image

LEFT  = 0
RIGHT = 1
TOP   = 2
FRONT = 3
BACK  = 4
DOWN  = 5

NEARLYONE = 0.9999   # u/v cap to stay safely below 1.0
TWOPI = 2.0 * math.pi

TEMPLATES = [
    (4096, 1344, 1376, 1344, 32, 5376),   # template 0 – 5.6 k mode
    (2272,  736,  768,  736, 16, 2944),   # template 1 – 3 k mode
]

# ── Face plane coefficients  ax + by + cz = d  (from Init() in C source) ────
FACE_PLANES = [
    (-1,  0,  0, -1),   # LEFT   k=0
    ( 1,  0,  0, -1),   # RIGHT  k=1
    ( 0,  0,  1, -1),   # TOP    k=2
    ( 0,  1,  0, -1),   # FRONT  k=3
    ( 0, -1,  0, -1),   # BACK   k=4
    ( 0,  0, -1, -1),   # DOWN   k=5
]

FACE_IDS = [LEFT, RIGHT, TOP, FRONT, BACK, DOWN]


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def check_template(s: str, nexpect: int) -> bool:
    """Verify that *s* contains exactly *nexpect* '%' characters."""
    n = s.count('%')
    if n != nexpect:
        print(
            f"ERROR: template '{s}' has {n} %%-entry/entries, expected {nexpect}",
            file=sys.stderr,
        )
        return False
    return True


def check_frames(fname1: str, fname2: str):
    """Check frame files and determine template."""
    for fname in (fname1, fname2):
        if os.path.splitext(fname)[1].lower() not in ('.jpg', '.jpeg'):
            raise ValueError(f"File '{fname}' does not look like a JPEG")
        if not os.path.exists(fname):
            raise FileNotFoundError(f"Frame not found: '{fname}'")

    with Image.open(fname1) as im1, Image.open(fname2) as im2:
        w1, h1 = im1.size
        w2, h2 = im2.size

    if w1 != w2 or h1 != h2:
        raise ValueError(
            f"Frame sizes don't match: {w1}×{h1} vs {w2}×{h2}"
        )

    for idx, t in enumerate(TEMPLATES):
        if w1 == t[0] and h1 == t[1]:
            return idx, w1, h1

    known = ', '.join(f"{t[0]}×{t[1]}" for t in TEMPLATES)
    raise ValueError(
        f"No recognised frame template for {w1}×{h1}. Known sizes: {known}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Core geometry – FindFaceUV (vectorized)
# ═══════════════════════════════════════════════════════════════════════════════

def find_face_uv_vectorized(
    lon: np.ndarray,
    lat: np.ndarray,
) -> tuple:
    """
    Given longitude and latitude arrays, find corresponding face id and (u,v) coordinate.
    Returns (face_array, u_array, v_array).
    """
    cos_lat = np.cos(lat)
    px = cos_lat * np.sin(lon)   # world X
    py = cos_lat * np.cos(lon)   # world Y  (+Y = FRONT)
    pz = np.sin(lat)             # world Z  (+Z = UP = TOP)

    N = lon.size
    face_out = np.full(N, -1, dtype=np.int16)
    u_out    = np.zeros(N, dtype=np.float32)
    v_out    = np.zeros(N, dtype=np.float32)
    remain   = np.ones(N,  dtype=bool)   # pixels not yet assigned to a face

    FOURPI = 4.0 / math.pi

    for (a, b, c, d), k in zip(FACE_PLANES, FACE_IDS):
        if not remain.any():
            break

        # denom = -(a·px + b·py + c·pz)
        denom = -(a * px + b * py + c * pz)

        # μ = d / denom  (distance along ray to plane intersection)
        with np.errstate(divide='ignore', invalid='ignore'):
            mu = np.where(np.abs(denom) > 1e-10, d / denom, -1.0)

        # Only consider forward intersections (μ > 0) on unassigned pixels
        fwd = (mu > 0) & remain

        qx = mu * px
        qy = mu * py
        qz = mu * pz

        # Check whether the intersection point lies within the face square
        if k in (LEFT, RIGHT):
            on_face = (qy >= -1) & (qy <= 1) & (qz >= -1) & (qz <= 1)
        elif k in (FRONT, BACK):
            on_face = (qx >= -1) & (qx <= 1) & (qz >= -1) & (qz <= 1)
        else:  # TOP, DOWN
            on_face = (qx >= -1) & (qx <= 1) & (qy >= -1) & (qy <= 1)

        hit = fwd & on_face

        # Atan lens-distortion correction (matches C source)
        aqx = np.arctan(qx) * FOURPI
        aqy = np.arctan(qy) * FOURPI
        aqz = np.arctan(qz) * FOURPI

        # u, v within the face  (before ×0.5 scaling)
        if k == LEFT:
            fu, fv = aqy + 1.0,       aqz + 1.0
        elif k == RIGHT:
            fu, fv = 1.0 - aqy,       aqz + 1.0
        elif k == FRONT:
            fu, fv = aqx + 1.0,       aqz + 1.0
        elif k == BACK:
            fu, fv = 1.0 - aqx,       aqz + 1.0
        elif k == DOWN:
            fu, fv = 1.0 - aqx,       1.0 - aqy
        else:  # TOP
            fu, fv = 1.0 - aqx,       aqy + 1.0

        fu = np.minimum((fu * 0.5).astype(np.float32), NEARLYONE)
        fv = np.minimum((fv * 0.5).astype(np.float32), NEARLYONE)

        face_out = np.where(hit, k,  face_out)
        u_out    = np.where(hit, fu, u_out)
        v_out    = np.where(hit, fv, v_out)
        remain   = remain & ~hit

    return face_out, u_out, v_out


# ═══════════════════════════════════════════════════════════════════════════════
# Lookup table  (equirectangular pixel → face/u/v)
# ═══════════════════════════════════════════════════════════════════════════════

def build_lookup_table(
    out_width: int,
    out_height: int,
    antialias: int,
    which_template: int,
) -> tuple:
    """
    Build lookup table for equirectangular projection.
    Returns (face_lut, u_lut, v_lut) with shape (out_height, out_width, antialias, antialias).
    """
    # Cache directory sits next to this file — shared across all videos
    _cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lut_cache')
    os.makedirs(_cache_dir, exist_ok=True)
    _cache_path = os.path.join(
        _cache_dir,
        f"equirect_lut_{which_template}_{out_width}_{out_height}_{antialias}.npz",
    )

    if os.path.exists(_cache_path):
        print(f"Loading cached equirectangular lookup table: {_cache_path}", file=sys.stderr)
        d = np.load(_cache_path)
        return d['face'], d['u'], d['v']

    print(f"Generating equirectangular lookup table (will be cached to {_cache_path}) …", file=sys.stderr)
    t0 = time.time()

    shape    = (out_height, out_width, antialias, antialias)
    face_lut = np.full(shape, -1, dtype=np.int16)
    u_lut    = np.zeros(shape, dtype=np.float32)
    v_lut    = np.zeros(shape, dtype=np.float32)

    dx = antialias * out_width
    dy = antialias * out_height

    # Integer pixel coordinate grids
    j_arr = np.arange(out_height)       # row index (y)
    i_arr = np.arange(out_width)        # column index (x)
    jj, ii = np.meshgrid(j_arr, i_arr, indexing='ij')  # (out_height, out_width)

    for aj in range(antialias):
        print(f"  antialias row {aj+1}/{antialias} …", file=sys.stderr)
        for ai in range(antialias):
            # Sub-pixel position in normalised [0, 1) space
            x_n = (ii + ai / antialias) / out_width    # 0 → 1, left → right
            y_n = (jj + aj / antialias) / out_height   # 0 → 1, top  → bottom

            # Convert to longitude/latitude
            # longitude: -π … π (left → right)
            # latitude:  -π/2 … π/2 (top → bottom)
            longitude = x_n * TWOPI - math.pi
            latitude  = y_n * math.pi - math.pi / 2

            face_2d, u_2d, v_2d = find_face_uv_vectorized(
                longitude.ravel(), latitude.ravel()
            )

            face_lut[:, :, aj, ai] = face_2d.reshape(out_height, out_width)
            u_lut[:, :, aj, ai]    = u_2d.reshape(out_height, out_width)
            v_lut[:, :, aj, ai]    = v_2d.reshape(out_height, out_width)

    elapsed = time.time() - t0
    print(
        f"Lookup table generated in {elapsed:.1f}s — saving to {_cache_path}",
        file=sys.stderr,
    )
    np.savez_compressed(_cache_path, face=face_lut, u=u_lut, v=v_lut)
    return face_lut, u_lut, v_lut


# ═══════════════════════════════════════════════════════════════════════════════
# Colour sampling – GetColour (vectorized)
# ═══════════════════════════════════════════════════════════════════════════════

def rotate_uv_90(u: np.ndarray, v: np.ndarray) -> tuple:
    """Rotate u,v by 90 degrees counterclockwise."""
    return v.copy(), np.minimum(NEARLYONE - u, NEARLYONE)


def sample_frame(
    face_flat: np.ndarray,
    u_flat: np.ndarray,
    v_flat: np.ndarray,
    frame1: np.ndarray,
    frame2: np.ndarray,
    tmpl: int,
) -> np.ndarray:
    """
    Sample colours from frame1 and frame2 based on face/u/v coordinates.
    Returns array of shape (N, 3) with RGB values.
    """
    tw, th, sw, cw, bw, _ = TEMPLATES[tmpl]
    N       = face_flat.size
    colours = np.zeros((N, 3), dtype=np.float32)

    for face in range(6):
        mask = face_flat == face
        if not mask.any():
            continue

        midx = np.where(mask)[0]
        u = u_flat[mask].copy()
        v = v_flat[mask].copy()

        # Rotate u,v counterclockwise by 90 degrees for DOWN, BACK, and TOP faces
        if face in (DOWN, BACK, TOP):
            u, v = rotate_uv_90(u, v)

        if face in (FRONT, BACK):
            x0  = sw
            w   = cw
            ix  = np.clip((x0 + u * w).astype(np.int32), 0, tw - 1)
            iy  = np.clip((v * th).astype(np.int32),      0, th - 1)
            src = frame1 if face == FRONT else frame2
            colours[midx] = src[iy, ix].astype(np.float32)

        elif face in (LEFT, DOWN):
            w   = sw
            duv = bw / w

            uvl = 2 * (0.5 - duv) * u
            uvr = 2 * (0.5 - duv) * (u - 0.5) + 0.5 + duv

            rl = uvl <= 0.5 - 2 * duv   # purely left of blend zone
            rr = uvr >= 0.5 + 2 * duv   # purely right of blend zone
            rb = ~rl & ~rr              # inside the blend zone

            src = frame1 if face == LEFT else frame2

            if rl.any():
                ix = np.clip((uvl[rl] * w).astype(np.int32), 0, tw - 1)
                iy = np.clip((v[rl]   * th).astype(np.int32), 0, th - 1)
                colours[midx[rl]] = src[iy, ix].astype(np.float32)

            if rr.any():
                ix = np.clip((uvr[rr] * w).astype(np.int32), 0, tw - 1)
                iy = np.clip((v[rr]   * th).astype(np.int32), 0, th - 1)
                colours[midx[rr]] = src[iy, ix].astype(np.float32)

            if rb.any():
                alpha = ((uvl[rb] - 0.5 + 2 * duv) / (2 * duv))[:, None]
                ix1 = np.clip((uvl[rb] * w).astype(np.int32), 0, tw - 1)
                iy1 = np.clip((v[rb]   * th).astype(np.int32), 0, th - 1)
                ix2 = np.clip((uvr[rb] * w).astype(np.int32), 0, tw - 1)
                iy2 = np.clip((v[rb]   * th).astype(np.int32), 0, th - 1)
                c1  = src[iy1, ix1].astype(np.float32)
                c2  = src[iy2, ix2].astype(np.float32)
                colours[midx[rb]] = (1.0 - alpha) * c1 + alpha * c2

        elif face in (RIGHT, TOP):
            x0  = sw + cw
            w   = sw
            duv = bw / w

            uvl = 2 * (0.5 - duv) * u
            uvr = 2 * (0.5 - duv) * (u - 0.5) + 0.5 + duv

            rl = uvl <= 0.5 - 2 * duv
            rr = uvr >= 0.5 + 2 * duv
            rb = ~rl & ~rr

            src = frame1 if face == RIGHT else frame2

            if rl.any():
                ix = np.clip((x0 + uvl[rl] * w).astype(np.int32), 0, tw - 1)
                iy = np.clip((v[rl]         * th).astype(np.int32), 0, th - 1)
                colours[midx[rl]] = src[iy, ix].astype(np.float32)

            if rr.any():
                ix = np.clip((x0 + uvr[rr] * w).astype(np.int32), 0, tw - 1)
                iy = np.clip((v[rr]         * th).astype(np.int32), 0, th - 1)
                colours[midx[rr]] = src[iy, ix].astype(np.float32)

            if rb.any():
                alpha = ((uvl[rb] - 0.5 + 2 * duv) / (2 * duv))[:, None]
                ix1 = np.clip((x0 + uvl[rb] * w).astype(np.int32), 0, tw - 1)
                iy1 = np.clip((v[rb]         * th).astype(np.int32), 0, th - 1)
                ix2 = np.clip((x0 + uvr[rb] * w).astype(np.int32), 0, tw - 1)
                iy2 = np.clip((v[rb]         * th).astype(np.int32), 0, th - 1)
                c1  = src[iy1, ix1].astype(np.float32)
                c2  = src[iy2, ix2].astype(np.float32)
                colours[midx[rb]] = (1.0 - alpha) * c1 + alpha * c2

    return colours


# ═══════════════════════════════════════════════════════════════════════════════
# Per-frame processing
# ═══════════════════════════════════════════════════════════════════════════════

def process_frame(
    nframe: int,
    seq_tmpl: str,
    face_lut: np.ndarray,
    u_lut: np.ndarray,
    v_lut: np.ndarray,
    out_width: int,
    out_height: int,
    antialias: int,
    which_template: int,
    out_tmpl: str,
    debug: bool,
) -> bool:
    """
    Read one frame pair, render equirectangular 360° image, and save it.

    Returns True on success, False when the frame files are not found
    (used as the stop signal for the outer loop).
    """
    fname1 = seq_tmpl % (0, nframe)
    fname2 = seq_tmpl % (5, nframe)

    if not os.path.exists(fname1) or not os.path.exists(fname2):
        if debug:
            print(f"Frame {nframe}: file(s) not found → stopping.", file=sys.stderr)
        return False

    if debug:
        print(f"Frame {nframe}: {fname1}  +  {fname2}", file=sys.stderr)

    img1 = np.array(Image.open(fname1).convert('RGB'), dtype=np.uint8)
    img2 = np.array(Image.open(fname2).convert('RGB'), dtype=np.uint8)
    
    # Rotate track5 (img2) 180 degrees for correct orientation in 360° output
    img2 = np.rot90(img2, 2)

    t0  = time.time()
    aa2 = antialias * antialias

    colour_sum = np.zeros((out_height * out_width, 3), dtype=np.float32)

    for aj in range(antialias):
        for ai in range(antialias):
            face_2d = face_lut[:, :, aj, ai]
            u_2d    = u_lut[:, :, aj, ai]
            v_2d    = v_lut[:, :, aj, ai]

            face_flat = face_2d.ravel()
            u_flat    = u_2d.ravel()
            v_flat    = v_2d.ravel()

            valid = face_flat >= 0
            if not valid.any():
                continue

            c = np.zeros((face_flat.size, 3), dtype=np.float32)
            c[valid] = sample_frame(
                face_flat[valid], u_flat[valid], v_flat[valid],
                img1, img2, which_template,
            )
            colour_sum += c

    result = (
        np.clip(colour_sum / aa2, 0, 255)
        .astype(np.uint8)
        .reshape(out_height, out_width, 3)
    )

    # ── Output filename ──────────────────────────────────────────────────
    if out_tmpl:
        fname_out = out_tmpl % nframe
    else:
        # Derive from the track-0 input path
        base      = os.path.splitext(fname1)[0]
        fname_out = f"{base}_sphere.jpg"

    # Ensure the output directory exists
    out_dir = os.path.dirname(fname_out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    if debug:
        print(f"  → {fname_out}", file=sys.stderr)

    Image.fromarray(result).save(fname_out, quality=95)

    if debug:
        print(f"  processing time: {time.time() - t0:.2f}s", file=sys.stderr)

    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description='GoPro Max dual-lens frames → equirectangular 360° image',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
The input template must contain exactly TWO %d entries:
  first  = track number (0 or 5)
  second = frame number

The output template (if given with -o) must contain ONE %d entry for the frame number.

Examples:
  %(prog)s -w 4096 -n 1 -m 500 track%%d/frame%%04d.jpg
  %(prog)s -w 2048 -o out/sphere_%%04d.jpg track%%d/frame%%04d.jpg
""",
    )
    ap.add_argument(
        'template',
        help='Input filename template (two %%d: track number, frame number)',
    )
    ap.add_argument(
        '-w', dest='outwidth', type=int, default=4096, metavar='N',
        help='Output image width in pixels (default: 4096)',
    )
    ap.add_argument(
        '-a', dest='antialias', type=int, default=2, metavar='N',
        help='Antialiasing level — supersampling per axis (default: 2)',
    )
    ap.add_argument(
        '-o', dest='out_tmpl', default='', metavar='TMPL',
        help='Output filename template (one %%d: frame number)',
    )
    ap.add_argument(
        '-n', dest='nstart', type=int, default=0, metavar='N',
        help='Start frame index (default: 0)',
    )
    ap.add_argument(
        '-m', dest='nstop', type=int, default=100000, metavar='N',
        help='End frame index   (default: 100000)',
    )
    ap.add_argument(
        '-d', dest='debug', action='store_true',
        help='Enable verbose / debug output',
    )
    args = ap.parse_args()

    if not check_template(args.template, 2):
        sys.exit(1)
    if args.out_tmpl and not check_template(args.out_tmpl, 1):
        sys.exit(1)

    fname1 = args.template % (0, args.nstart)
    fname2 = args.template % (5, args.nstart)
    which_template, fw, fh = check_frames(fname1, fname2)

    if args.debug:
        print(f"Frame size: {fw}×{fh}  (template {which_template + 1})",
              file=sys.stderr)

    # Default width = 4096; snap to a multiple of 4
    out_width = (args.outwidth // 4) * 4
    out_height = out_width // 2

    antialias = max(1, args.antialias)

    if args.debug:
        print(
            f"Output: {out_width}×{out_height}px equirectangular  AA={antialias}×{antialias}",
            file=sys.stderr,
        )

    face_lut, u_lut, v_lut = build_lookup_table(
        out_width, out_height, antialias, which_template
    )

    # Determine total frames to process
    total_frames = args.nstop - args.nstart + 1
    processed = 0
    
    print(f"Processing {total_frames} frames...", file=sys.stderr)
    
    for nframe in range(args.nstart, args.nstop + 1):
        ok = process_frame(
            nframe, args.template,
            face_lut, u_lut, v_lut,
            out_width, out_height, antialias, which_template,
            args.out_tmpl, args.debug,
        )
        if not ok:
            # End of available frames
            break
        
        processed += 1
        
        # Progress bar (45 chars wide)
        filled = int(45 * processed / max(1, processed))  # Use processed as max since we don't know total
        bar = '█' * min(filled, 45) + '░' * max(0, 45 - filled)
        pct = 100.0 if not ok else (processed / max(1, processed) * 100)
        print(f'\r  360°      |{bar}| {processed} frames processed', end='', flush=True, file=sys.stderr)
    
    print(file=sys.stderr)  # New line after progress bar
    print(f"Done — {processed} frame(s) processed.", file=sys.stderr)


if __name__ == '__main__':
    main()

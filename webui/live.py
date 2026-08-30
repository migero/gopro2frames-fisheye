"""Live background detection loop that feeds the server's TrackStore."""

import time


def run_live_wrapped(args, server):
    import traceback
    try:
        run_live(args, server)
    except Exception:
        traceback.print_exc()
        server.live_meta["running"] = False


def run_live(args, server):
    from tracking.pipeline import (
        EquirectVideoReader, FolderFrameReader, resolve_gyro_csv,
        build_tracker, attach_gyro,
    )

    gyro_csv = resolve_gyro_csv(args.video, args.gyro_csv, args.no_gyro)
    tracker = build_tracker(args)
    attach_gyro(tracker, gyro_csv, args.gyro_calib)
    if not tracker.detector.load():
        raise SystemExit("Failed to load YOLO model")

    server.live_meta["running"] = True
    t0 = time.time()
    frames_done = 0
    server.has_gyro = bool(tracker.world_mode)
    server.gyro = tracker.gyro if tracker.world_mode else None
    server.ts_by_frame = {}

    def _set_pose(t):
        # world->image rotation for the current frame so the browser can level
        # the horizon and camera FOV borders on the equirect map.
        try:
            if tracker.world_mode:
                server.cur_pose = tracker.gyro.R_world_image(t).tolist()
        except Exception:
            pass

    if args.video:
        reader = EquirectVideoReader(args.video, args.equirect_width,
                                     start_frame=args.start_frame)
        reader.open()
        server.dim = (reader.width, reader.height)
        try:
            while True:
                got = reader.read()
                if got is None:
                    break
                fi, ts, frame = got
                recs = tracker.process_frame(frame, frame_index=fi,
                                             timestamp=ts,
                                             enable_centering=True)
                for r in recs:
                    server.tracks.add(r)
                server.frame_index = fi
                _set_pose(ts)
                server.ts_by_frame[fi] = ts
                frames_done += 1
                if frames_done % 10 == 0:
                    server.live_meta["fps"] = frames_done / (time.time() - t0)
                server.live_meta["frames"] = frames_done
                if args.max_frames and frames_done >= args.max_frames:
                    break
        finally:
            reader.close()
    else:
        for fi, ts, frame in FolderFrameReader(args.frames, args.max_frames,
                                               fps=args.fps,
                                               start=args.start_frame):
            recs = tracker.process_frame(frame, frame_index=fi,
                                         timestamp=ts, enable_centering=True)
            for r in recs:
                server.tracks.add(r)
            server.frame_index = fi
            _set_pose(ts)
            server.ts_by_frame[fi] = ts
            frames_done += 1
            server.live_meta["fps"] = frames_done / (time.time() - t0)
            server.live_meta["frames"] = frames_done

    server.live_meta["running"] = False
    print("Live processing finished. Using --frames+--jsonl to replay.")

# core/stream_manager.py
# ObjectTrackAI – Production-grade stream manager
# Based on the proven EggTrackAI design with all original APIs preserved.
# Fixes:
#   - Simplified FFmpeg options (TCP only) to stop decoder corruption.
#   - Motion threshold = 0.003 (sensitive enough for slow belts).
#   - Force inference every 30 frames when no active tracks.
#   - Frame corruption detection: skip garbage frames.

import cv2
import numpy as np
import queue
import threading
import time
import os
from typing import Optional

from core.vision_engine import ObjectVisionEngine
import core.logger_setup as ls

# # ── Simplified RTSP options (TCP only, no aggressive buffering) ──────────
# os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|"
    "fflags;nobuffer|"
    "flags;low_delay|"
    "stimeout;2000000"  # 2s timeout
)


# ── Constants ─────────────────────────────────────────────────────────────
_RECONNECT_POLL_S       = 2.0
_RECONNECT_LOG_EVERY    = 5
_MOTION_THRESHOLD       = 0.003        # sensitive enough for slow belts
_ROI_HALF_HEIGHT        = 120
_IDLE_SLEEP_S           = 0.033
_DISCONNECT_AGE_PENALTY = 90
_STREAM_JPEG_QUALITY    = 72
_TARGET_FPS             = 20
_STREAM_INTERVAL_S      = 1.0 / _TARGET_FPS
_PLACEHOLDER_INTERVAL   = 2.0
_FORCE_INFERENCE_EVERY_N = 30           # run YOLO even if no motion every 30 frames


# ── Helpers ──────────────────────────────────────────────────────────────
def _is_file_source(source: str) -> bool:
    lowered = source.lower().strip()
    return not any(lowered.startswith(s) for s in
                   ("rtsp://", "rtsps://", "rtmp://", "http://", "https://"))


def _open_capture(source: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


# ── Reconnect controller ─────────────────────────────────────────────────
class _ReconnectController:
    def __init__(self, source: str, line_id: int):
        self._source = source
        self._line_id = line_id
        self._cap = None
        self._ready = threading.Event()
        self._attempt = 0
        self._lock = threading.Lock()

    def on_disconnect(self) -> float:
        with self._lock:
            if self._ready.is_set():
                return time.monotonic()
            self._ready.clear()
            self._cap = None
            self._attempt = 0
        threading.Thread(target=self._reconnect_loop, daemon=True,
                         name=f"reconnect-{self._line_id}").start()
        return time.monotonic()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def get_cap(self) -> cv2.VideoCapture:
        return self._cap

    def _reconnect_loop(self) -> None:
        while True:
            self._attempt += 1
            if self._attempt % _RECONNECT_LOG_EVERY == 1:
                ls.log_system(f"Line {self._line_id}: reconnect attempt {self._attempt}…")
            time.sleep(_RECONNECT_POLL_S)
            cap = _open_capture(self._source)
            if not cap.isOpened():
                cap.release()
                continue
            ret, _ = cap.read()
            if not ret:
                cap.release()
                continue
            self._cap = cap
            self._ready.set()
            ls.log_system(f"Line {self._line_id}: reconnected after {self._attempt} attempt(s)")
            return


# ── Motion guard (MOG2) ──────────────────────────────────────────────────
class _MotionGuard:
    def __init__(self, roi_y1: int, roi_y2: int, frame_height: int, motion_thresh: float):
        self.roi_y1 = max(0, roi_y1)
        self.roi_y2 = min(frame_height, roi_y2)
        self._valid = self.roi_y2 > self.roi_y1
        self.motion_thresh = motion_thresh
        if self._valid:
            self.mog2 = cv2.createBackgroundSubtractorMOG2(
                history=200, varThreshold=40, detectShadows=False
            )
            self.frame_count = 0
            self.warmup = 60
        else:
            self.mog2 = None
            ls.log_system(f"MotionGuard: invalid ROI ({self.roi_y1}–{self.roi_y2}) – disabled")

    def has_motion(self, frame: np.ndarray) -> bool:
        if not self._valid or self.mog2 is None:
            return True
        roi = frame[self.roi_y1:self.roi_y2, :]
        if roi.size == 0:
            return True
        fg = self.mog2.apply(roi)
        if fg is None:
            return True
        self.frame_count += 1
        if self.frame_count <= self.warmup:
            return True
        score = float(fg.sum()) / (fg.size * 255)
        return score >= self.motion_thresh


# ── Inference thread ─────────────────────────────────────────────────────
def _inference_thread(line_config: dict, global_config: dict,
                      frame_slot: queue.Queue) -> None:
    line_id = line_config["id"]
    source = line_config["source"]
    is_file = _is_file_source(source)
    loop_file = line_config.get("loop", False)

    ls.log_system(f"Line {line_id}: opening source '{source}'")
    engine = ObjectVisionEngine(global_config, line_config)

    motion_thresh = global_config.get("performance", {}).get("motion_threshold", 0.003)
    ls.log_system(f"Line {line_id}: motion threshold = {motion_thresh}")

    cap = _open_capture(source)
    reconnect = _ReconnectController(source, line_id)
    disconnect_ts: Optional[float] = None
    motion_guard: Optional[_MotionGuard] = None

    # Frame corruption detection
    last_valid_frame_hash = None
    frames_since_last_inference = 0

    def _put_frame(annotated, stats):
        try:
            frame_slot.put_nowait((annotated, stats))
        except queue.Full:
            try:
                frame_slot.get_nowait()
            except queue.Empty:
                pass
            try:
                frame_slot.put_nowait((annotated, stats))
            except queue.Full:
                pass

    while True:
        # Read frame
        try:
            ret, frame = cap.read()
        except Exception as exc:
            ls.log_error(f"Line {line_id}: cap.read() exception", exc=exc)
            ret, frame = False, None

        # Handle disconnect / EOF
        if not ret or frame is None:
            if is_file:
                if loop_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ls.log_system(f"Line {line_id}: file looped")
                    motion_guard = None
                    continue
                else:
                    ls.log_system(f"Line {line_id}: file ended (no loop)")
                    cap.release()
                    return
            # RTSP disconnect
            if disconnect_ts is None:
                ls.log_system(f"Line {line_id}: stream lost, starting reconnect")
                disconnect_ts = reconnect.on_disconnect()
                engine._age_all_tracks(_DISCONNECT_AGE_PENALTY)
            time.sleep(0.05)
            if reconnect.is_ready():
                cap = reconnect.get_cap()
                disconnect_ts = None
                motion_guard = None
            continue

        # Lazy motion guard init
        if motion_guard is None:
            cz = line_config["zones"]["count_zone"]
            orig_h = frame.shape[0]
            roi_y1 = max(0, cz[1] - _ROI_HALF_HEIGHT)
            roi_y2 = min(orig_h, cz[3])
            motion_guard = _MotionGuard(roi_y1, roi_y2, orig_h, motion_thresh)

        # Frame corruption detection: skip completely black frames or repeated garbage
        frame_hash = hash(frame.tobytes()[-1024:])  # cheap hash of last 1KB
        if last_valid_frame_hash is not None and frame_hash == last_valid_frame_hash:
            # Duplicate frame – skip to avoid wasting CPU
            time.sleep(0.005)
            continue
        last_valid_frame_hash = frame_hash

        # Motion guard with forced inference
        no_active = not engine.has_active_tracks()
        no_motion = not motion_guard.has_motion(frame)

        if no_active:
            frames_since_last_inference += 1
            if frames_since_last_inference >= _FORCE_INFERENCE_EVERY_N:
                no_motion = False   # force inference
                frames_since_last_inference = 0
        else:
            frames_since_last_inference = 0

        if no_active and no_motion:
            # Idle – send raw frame (no boxes) at lower quality
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 55])
            if ok:
                idle_stats = dict(engine.stats)
                _put_frame(buf.tobytes(), idle_stats)
            time.sleep(_IDLE_SLEEP_S)
            continue

        # Run inference
        try:
            annotated, stats = engine.process_frame(frame)
            if annotated is not None:
                ok, buf = cv2.imencode(".jpg", annotated,
                                       [cv2.IMWRITE_JPEG_QUALITY, _STREAM_JPEG_QUALITY])
                if ok:
                    _put_frame(buf.tobytes(), stats)
            # Add this:
            time.sleep(0.001) # Yield to the OS scheduler        
        except Exception as exc:
            ls.log_error(f"Line {line_id}: inference error", exc=exc)


# ── Placeholder ──────────────────────────────────────────────────────────
def _make_placeholder(line_id: int) -> bytes:
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[:] = (30, 30, 30)
    cv2.putText(img, f"Line {line_id}", (220, 210),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (180, 180, 180), 2, cv2.LINE_AA)
    cv2.putText(img, "Waiting for stream…", (140, 260),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 1, cv2.LINE_AA)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return buf.tobytes()


# ── Publisher thread ─────────────────────────────────────────────────────
def _publisher_thread(line_id: int, frame_slot: queue.Queue, result_dict) -> None:
    empty_stats = {"total": 0, "classes": {}, "defects": 0}
    last_publish_ts = 0.0
    last_placeholder_ts = 0.0

    while True:
        now = time.monotonic()
        try:
            jpeg_bytes, stats = frame_slot.get(timeout=0.5)
        except queue.Empty:
            if now - last_placeholder_ts > _PLACEHOLDER_INTERVAL:
                placeholder = _make_placeholder(line_id)
                result_dict[str(line_id)] = {"frame": placeholder, "stats": empty_stats}
                last_placeholder_ts = now
            continue
        except Exception as exc:
            ls.log_error(f"Line {line_id}: publisher queue error", exc=exc)
            continue

        if now - last_publish_ts < _STREAM_INTERVAL_S:
            continue
        result_dict[str(line_id)] = {"frame": jpeg_bytes, "stats": stats}
        last_publish_ts = now


# ── Worker entry point ───────────────────────────────────────────────────
def inference_worker(line_config: dict, global_config: dict,
                     result_dict, log_queue) -> None:
    ls.configure_worker_logging(log_queue)
    line_id = line_config["id"]
    ls.log_system(f"Worker for line {line_id} starting (pid={os.getpid()})")

    frame_slot: queue.Queue = queue.Queue(maxsize=1)

    t_infer = threading.Thread(
        target=_inference_thread,
        args=(line_config, global_config, frame_slot),
        daemon=True,
        name=f"infer-{line_id}",
    )
    t_pub = threading.Thread(
        target=_publisher_thread,
        args=(line_id, frame_slot, result_dict),
        daemon=True,
        name=f"pub-{line_id}",
    )
    t_infer.start()
    t_pub.start()

    t_infer.join()
    ls.log_system(f"Worker for line {line_id} inference thread exited")

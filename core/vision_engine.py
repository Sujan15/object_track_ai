# core/vision_engine.py


import logging
from collections import Counter

import cv2
import numpy as np
import openvino as ov
import supervision as sv

from core.class_mapper import ClassMapper
from core.logger_setup import log_object_event
from core.tracker import ObjectTracker

_logger = logging.getLogger("ObjectTrackAI.vision")

# Confidence thresholds
_CONF_THRESH = 0.15
_NMS_THRESH  = 0.25

# Minimum frames inside count zone before a confirmed track is counted
_MIN_FRAMES_IN_COUNT = 3


class ObjectVisionEngine:
    def __init__(self, config: dict, line_config: dict):
        self.line_id      = line_config["id"]
        self.class_mapper = ClassMapper()

        # ── OpenVINO model ─────────────────────────────────────────────────
        model_path = config["models"]["detection"]
        core       = ov.Core()
        compiled   = core.compile_model(model_path, "CPU", {
            "PERFORMANCE_HINT":       "LATENCY",
            "INFERENCE_NUM_THREADS":  str(config["performance"].get("cpu_threads", 4)),
        })
        self.infer_req = compiled.create_infer_request()

        self.tracker = ObjectTracker(
            max_lost=config.get("disconnect_track_age_penalty", 90),
        )

        # ── Raw zones from config (will be clipped to frame size on 1st frame) ─
        zones = line_config.get("zones", {})
        raw_dz = zones.get("defect_zone", [0, 0, 0, 0])
        self._raw_defect_zone  = raw_dz
        self._raw_measure_zone = zones["measure_zone"]
        self._raw_count_zone   = zones["count_zone"]

        # Actual zones set on first frame (after we know resolution)
        self.defect_zone:  tuple | None = None
        self.measure_zone: tuple | None = None
        self.count_zone:   tuple | None = None
        self._zones_initialised = False

        # ── Per-track state ────────────────────────────────────────────────
        self.id_states:   dict[int, _TrackState] = {}
        self.counted_ids: set[int]               = set()
        self.stats: dict = {"total": 0, "classes": {}, "defects": 0}

        # Configurable thresholds
        self._min_frames_count  = config.get("min_frames_in_count_zone", _MIN_FRAMES_IN_COUNT)
        self._min_measure_votes = config.get("min_measure_votes", 5)
        self._force_count_px    = config.get("force_count_zone_px", 80)

    # ── Zone initialisation ────────────────────────────────────────────────

    def _init_zones(self, orig_w: int, orig_h: int) -> None:
        """Clip configured zones to actual frame resolution and log them."""
        def _clip(z):
            return (
                max(0,      min(z[0], orig_w - 1)),
                max(0,      min(z[1], orig_h - 1)),
                max(0,      min(z[2], orig_w)),
                max(0,      min(z[3], orig_h)),
            )

        raw_dz = self._raw_defect_zone
        self.defect_zone = (
            None if all(v == 0 for v in raw_dz) else _clip(raw_dz)
        )
        self.measure_zone = _clip(self._raw_measure_zone)
        self.count_zone   = _clip(self._raw_count_zone)
        self._zones_initialised = True

        _logger.info(
            "Line %d | frame=%dx%d | measure=%s | count=%s | defect=%s",
            self.line_id, orig_w, orig_h,
            self.measure_zone, self.count_zone, self.defect_zone,
        )

        # Warn if zones look wrong after clipping
        if self.measure_zone[2] - self.measure_zone[0] < 10:
            _logger.warning(
                "Line %d measure_zone is very narrow after clipping (%s). "
                "Check cameras.yaml zone coordinates vs actual resolution.",
                self.line_id, self.measure_zone,
            )
        if self.count_zone[3] - self.count_zone[1] < 10:
            _logger.warning(
                "Line %d count_zone is very short after clipping (%s). "
                "Check cameras.yaml zone coordinates vs actual resolution.",
                self.line_id, self.count_zone,
            )

    # ── Public interface ───────────────────────────────────────────────────

    def has_active_tracks(self) -> bool:
        for tid, track in list(self.tracker.tracks.items()):
            if not track.confirmed:
                continue
            st = self.id_states.get(tid)
            if st is None or not st.counted:
                return True
        return False

    def _age_all_tracks(self, penalty: int) -> None:
        for track in self.tracker.tracks.values():
            track.age += penalty

    # ── Main frame processing ──────────────────────────────────────────────

    def process_frame(self, frame: np.ndarray):
        if frame is None:
            return None, {}

        orig_h, orig_w = frame.shape[:2]

        # Initialise zones on first frame (now we know real resolution)
        if not self._zones_initialised:
            self._init_zones(orig_w, orig_h)

        # ── Inference ─────────────────────────────────────────────────────
        input_img  = cv2.resize(frame, (640, 640))
        input_blob = (
            input_img.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32) / 255.0
        )
        self.infer_req.infer({0: input_blob})
        raw_output = self.infer_req.get_output_tensor().data

        detections = self._parse_yolo(raw_output, orig_w, orig_h)
        # detections = self.tracker.update(detections)
        detections = self.tracker.update(detections)
        # --- DEBUG: log number of detections ---
        if len(detections) > 0:
            _logger.info(f"Line {self.line_id}: YOLO found {len(detections)} objects")
        # ---------------------------------------

        annotated = frame.copy()

        if detections is not None and len(detections) > 0:
            self._process_detections(detections, annotated)

        self._cleanup_id_states()
        self._draw_zones(annotated)
        self._draw_stats(annotated)

        # Return a shallow copy of stats so the caller gets a stable snapshot
        return annotated, {
            "total":   self.stats["total"],
            "classes": dict(self.stats["classes"]),
            "defects": self.stats["defects"],
        }

    # ── Detection processing ───────────────────────────────────────────────

    def _process_detections(self, detections: sv.Detections, annotated: np.ndarray) -> None:
        if self.measure_zone is None or self.count_zone is None:
            return

        mzx1, mzy1, mzx2, mzy2     = self.measure_zone
        ctzx1, ctzy1, ctzx2, ctzy2 = self.count_zone

        for i in range(len(detections)):
            tid = int(detections.tracker_id[i]) if detections.tracker_id is not None else -1

            bbox     = detections.xyxy[i]
            class_id = int(detections.class_id[i]) if detections.class_id is not None else 0
            cx = int((bbox[0] + bbox[2]) / 2)
            cy = int((bbox[1] + bbox[3]) / 2)

            if tid not in self.id_states:
                self.id_states[tid] = _TrackState()
            st = self.id_states[tid]

            track_obj    = self.tracker.tracks.get(tid)
            is_confirmed = track_obj is not None and track_obj.confirmed

            # ── Measure zone: class votes ──────────────────────────────────
            if mzx1 <= cx <= mzx2 and mzy1 <= cy <= mzy2:
                class_name = self.class_mapper.get_name(class_id)
                st.class_votes.append(class_name)
                if not st.locked:
                    st.color = self.class_mapper.get_color(class_name)

            # ── Lock class ─────────────────────────────────────────────────
            if not st.locked:
                enough = len(st.class_votes) >= self._min_measure_votes
                left   = cy > mzy2 and len(st.class_votes) > 0
                if enough or left:
                    if st.class_votes:
                        st.class_name = Counter(st.class_votes).most_common(1)[0][0]
                    else:
                        st.class_name = self.class_mapper.get_name(class_id)
                    st.locked = True
                    st.color  = self.class_mapper.get_color(st.class_name)

            # ── Defect zone ────────────────────────────────────────────────
            if self.defect_zone and not st.defect_locked:
                dzx1, dzy1, dzx2, dzy2 = self.defect_zone
                if dzx1 <= cx <= dzx2 and dzy1 <= cy <= dzy2:
                    pass   # plug defect classifier here

            # ── Count zone ─────────────────────────────────────────────────
            if not st.counted:
                if ctzx1 <= cx <= ctzx2 and ctzy1 <= cy <= ctzy2:
                    st.frames_in_count += 1
                    if is_confirmed and st.frames_in_count >= self._min_frames_count:
                        if not st.locked:
                            st.class_name = self.class_mapper.get_name(class_id)
                        self._do_count(tid, st)
                else:
                    if st.frames_in_count > 0 and cy < ctzy1:
                        st.frames_in_count = 0

            # ── Annotate ───────────────────────────────────────────────────
            x1, y1, x2, y2 = map(int, bbox)
            color     = st.color
            thickness = 2

            if tid < 0:
                # Unmatched detection (no tracker ID assigned yet)
                color     = (80, 80, 80)
                thickness = 1
            elif not is_confirmed:
                # Tentative track: thinner, greyed-out box so operator can
                # see something is detected even before confirmation
                color     = (160, 160, 100)
                thickness = 1

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)

            # Label: only show class + ID when we have meaningful info
            if tid >= 0:
                label_text = f"{st.class_name} #{tid}"
                if not is_confirmed:
                    label_text = f"? #{tid}"
                cv2.putText(
                    annotated, label_text,
                    (x1, max(y1 - 8, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
                )

    def _do_count(self, tid: int, st: "_TrackState") -> None:
        if st.counted or tid in self.counted_ids:
            return
        st.counted = True
        self.counted_ids.add(tid)
        log_object_event(self.line_id, tid, st.class_name, st.is_defective)
        self.stats["total"] += 1
        self.stats["classes"][st.class_name] = (
            self.stats["classes"].get(st.class_name, 0) + 1
        )
        if st.is_defective:
            self.stats["defects"] += 1

    # ── YOLO output parser ─────────────────────────────────────────────────

    def _parse_yolo(self, output: np.ndarray, orig_w: int, orig_h: int) -> sv.Detections:
        arr = np.squeeze(output)

        if arr.ndim == 2 and arr.shape[0] < arr.shape[1]:
            arr = arr.T

        if arr.ndim != 2 or arr.shape[1] < 5:
            return sv.Detections.empty()

        num_cls = arr.shape[1] - 4

        obj_scores = arr[:, 4] if num_cls == 0 else arr[:, 4:].max(axis=1)
        mask  = obj_scores > _CONF_THRESH
        valid = arr[mask]

        if len(valid) == 0:
            return sv.Detections.empty()

        sx = orig_w / 640.0
        sy = orig_h / 640.0
        cx_v, cy_v, w_v, h_v = valid[:, 0], valid[:, 1], valid[:, 2], valid[:, 3]
        x1 = (cx_v - w_v / 2) * sx
        y1 = (cy_v - h_v / 2) * sy
        x2 = (cx_v + w_v / 2) * sx
        y2 = (cy_v + h_v / 2) * sy
        xyxy = np.column_stack([x1, y1, x2, y2])

        if num_cls > 0:
            classes = np.argmax(valid[:, 4:], axis=1).astype(int)
            confs   = valid[:, 4:].max(axis=1)
        else:
            classes = np.zeros(len(valid), dtype=int)
            confs   = valid[:, 4]

        boxes_xywh = [
            [float(x1[i]), float(y1[i]),
             float(x2[i] - x1[i]), float(y2[i] - y1[i])]
            for i in range(len(valid))
        ]
        indices = cv2.dnn.NMSBoxes(
            boxes_xywh, confs.tolist(), _CONF_THRESH, _NMS_THRESH
        )
        if len(indices) == 0:
            return sv.Detections.empty()
        indices = np.asarray(indices).flatten()

        return sv.Detections(
            xyxy=xyxy[indices],
            confidence=confs[indices],
            class_id=classes[indices],
        )

    # ── Housekeeping ───────────────────────────────────────────────────────

    def _cleanup_id_states(self) -> None:
        if len(self.id_states) > 500:
            live = set(self.tracker.tracks.keys())
            self.id_states = {k: v for k, v in self.id_states.items() if k in live}

    def _draw_zones(self, frame: np.ndarray) -> None:
        if self.measure_zone:
            mz = self.measure_zone
            cv2.rectangle(frame, (mz[0], mz[1]), (mz[2], mz[3]), (255, 255, 0), 2)
            cv2.putText(frame, "MEASURE", (mz[0] + 5, mz[1] + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2, cv2.LINE_AA)

        if self.count_zone:
            cz = self.count_zone
            cv2.rectangle(frame, (cz[0], cz[1]), (cz[2], cz[3]), (0, 255, 0), 3)
            cv2.putText(frame, "COUNT", (cz[0] + 5, cz[1] + 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2, cv2.LINE_AA)

        if self.defect_zone:
            dz = self.defect_zone
            cv2.rectangle(frame, (dz[0], dz[1]), (dz[2], dz[3]), (0, 0, 200), 2)
            cv2.putText(frame, "DEFECT", (dz[0] + 5, dz[1] + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 200), 2, cv2.LINE_AA)

    def _draw_stats(self, frame: np.ndarray) -> None:
        line_h  = 28
        classes = self.stats.get("classes", {})
        n_rows  = 2 + len(classes) + (1 if self.stats.get("defects") else 0)
        box_h   = max(60, n_rows * line_h + 16)
        box_w   = 300

        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (10 + box_w, 10 + box_h), (0, 255, 0), -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

        y = 38
        cv2.putText(
            frame, f"Line {self.line_id}  Total: {self.stats['total']}",
            (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
            (255, 255, 255), 2, cv2.LINE_AA,
        )
        y += line_h

        for cls, cnt in classes.items():
            if y > frame.shape[0] - 10:
                break
            cv2.putText(frame, f"  {cls}: {cnt}",
                        (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (200, 200, 200), 1, cv2.LINE_AA)
            y += line_h

        if self.stats.get("defects"):
            cv2.putText(frame, f"  Defects: {self.stats['defects']}",
                        (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (80, 80, 255), 1, cv2.LINE_AA)


class _TrackState:
    __slots__ = (
        "class_votes", "class_name", "color",
        "locked", "defect_locked", "is_defective",
        "frames_in_count", "counted",
    )

    def __init__(self):
        self.class_votes:     list  = []
        self.class_name:      str   = "Unknown"
        self.color:           tuple = (255, 0, 0)
        self.locked:          bool  = False
        self.defect_locked:   bool  = False
        self.is_defective:    bool  = False
        self.frames_in_count: int   = 0
        self.counted:         bool  = False
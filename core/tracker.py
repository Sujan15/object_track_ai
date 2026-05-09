# core/tracker.py


import numpy as np
import supervision as sv
from scipy.optimize import linear_sum_assignment
from typing import Dict, List, Set, Tuple

# ── Cost-matrix weights ────────────────────────────────────────────────────────
_W_IOU    = 0.50
_W_DIST   = 0.25
_W_SIZE   = 0.15
_W_MOTION = 0.10

# ── ByteTrack thresholds ───────────────────────────────────────────────────────
_HIGH_CONF = 0.50
_MIN_HITS  = 1      # Confirm on first detection so boxes appear immediately.
                    # Set to 2 once your zones and model are validated.
_MAX_COST  = 0.82

# ── Normalisation constants ────────────────────────────────────────────────────
_MAX_CENTRE_DIST = 150.0    # raised from 120 for faster belts
_MAX_SIZE_DIFF   = 80.0


# ── 2-D Kalman Filter ─────────────────────────────────────────────────────────

class KalmanFilter2D:
    def __init__(self, cx: float, cy: float):
        self.x = np.array([cx, cy, 0.0, 0.0], dtype=np.float64)
        self.P = np.diag([10.0, 10.0, 100.0, 100.0])
        self.F = np.array([[1,0,1,0],[0,1,0,1],[0,0,1,0],[0,0,0,1]], dtype=np.float64)
        self.H = np.array([[1,0,0,0],[0,1,0,0]], dtype=np.float64)
        self.R = np.diag([5.0, 5.0])
        q      = 0.5
        self.Q = np.diag([q, q, q * 4.0, q * 4.0])

    def predict(self) -> np.ndarray:
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.x[:2].copy()

    def update(self, z: np.ndarray) -> None:
        innov  = z - self.H @ self.x
        S      = self.H @ self.P @ self.H.T + self.R
        K      = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ innov
        self.P = (np.eye(4) - K @ self.H) @ self.P

    def innovation_magnitude(self, z: np.ndarray) -> float:
        innov = z - self.H @ self.x
        S     = self.H @ self.P @ self.H.T + self.R
        try:
            maha = float(innov.T @ np.linalg.inv(S) @ innov)
        except np.linalg.LinAlgError:
            return 1.0
        return min(1.0, maha / 6.0)


# ── Track ─────────────────────────────────────────────────────────────────────

class Track:
    __slots__ = ("track_id", "last_bbox", "pred_bbox", "age",
                 "hit_streak", "confirmed", "kf")

    def __init__(self, bbox: np.ndarray, track_id: int):
        self.track_id   = track_id
        self.last_bbox  = bbox.copy()
        self.pred_bbox  = bbox.copy()
        self.age        = 0
        self.hit_streak = 1
        self.confirmed  = False
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        self.kf = KalmanFilter2D(cx, cy)

    def predict(self) -> None:
        pred_c = self.kf.predict()
        hw = (self.last_bbox[2] - self.last_bbox[0]) / 2.0
        hh = (self.last_bbox[3] - self.last_bbox[1]) / 2.0
        self.pred_bbox = np.array([
            pred_c[0] - hw, pred_c[1] - hh,
            pred_c[0] + hw, pred_c[1] + hh,
        ])

    def update(self, bbox: np.ndarray) -> None:
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        self.kf.update(np.array([cx, cy]))
        self.last_bbox = bbox.copy()
        self.age       = 0
        self.hit_streak += 1
        self.confirmed = (self.hit_streak >= _MIN_HITS)

    def motion_cost(self, det_bbox: np.ndarray) -> float:
        cx = (det_bbox[0] + det_bbox[2]) / 2.0
        cy = (det_bbox[1] + det_bbox[3]) / 2.0
        return self.kf.innovation_magnitude(np.array([cx, cy]))


# ── Cost matrix ───────────────────────────────────────────────────────────────

def _build_cost_matrix(
    tracks: List[Track],
    det_bboxes: np.ndarray,
    conveyor_axis: str = "y",          # "y" = top-bottom, "x" = left-right
) -> np.ndarray:
    T, D = len(tracks), len(det_bboxes)
    cost = np.ones((T, D), dtype=np.float64)

    for i, trk in enumerate(tracks):
        pred   = trk.pred_bbox
        cx_p   = (pred[0] + pred[2]) / 2.0
        cy_p   = (pred[1] + pred[3]) / 2.0
        size_p = max(pred[2] - pred[0], pred[3] - pred[1])

        if conveyor_axis == "y":
            pos_last = (trk.last_bbox[1] + trk.last_bbox[3]) / 2.0  # last cy
        else:
            pos_last = (trk.last_bbox[0] + trk.last_bbox[2]) / 2.0  # last cx

        for j in range(D):
            det  = det_bboxes[j]
            cx_d = (det[0] + det[2]) / 2.0
            cy_d = (det[1] + det[3]) / 2.0

            # Directional gate – reject detections that moved against the belt
            # direction by more than 2px, or jumped forward > 150px.
            if conveyor_axis == "y":
                pos_d = cy_d
                if pos_d < pos_last - 2.0 or pos_d - pos_last > 150.0:
                    continue
            else:
                pos_d = cx_d
                if pos_d < pos_last - 2.0 or pos_d - pos_last > 150.0:
                    continue

            ix1 = max(pred[0], det[0]); iy1 = max(pred[1], det[1])
            ix2 = min(pred[2], det[2]); iy2 = min(pred[3], det[3])
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            a_p   = max(1e-6, (pred[2] - pred[0]) * (pred[3] - pred[1]))
            a_d   = max(1e-6, (det[2]  - det[0])  * (det[3]  - det[1]))
            iou   = inter / (a_p + a_d - inter)

            c_iou    = 1.0 - iou
            c_dist   = min(1.0, np.hypot(cx_d - cx_p, cy_d - cy_p) / _MAX_CENTRE_DIST)
            size_d   = max(det[2] - det[0], det[3] - det[1])
            c_size   = min(1.0, abs(size_d - size_p) / _MAX_SIZE_DIFF)
            c_motion = trk.motion_cost(det)

            cost[i, j] = (
                _W_IOU * c_iou + _W_DIST * c_dist
                + _W_SIZE * c_size + _W_MOTION * c_motion
            )
    return cost


def _hungarian_match(
    cost: np.ndarray, max_cost: float = _MAX_COST
) -> Tuple[Dict[int, int], Set[int], Set[int]]:
    if cost.size == 0:
        return {}, set(range(cost.shape[0])), set(range(cost.shape[1]))

    row_ind, col_ind = linear_sum_assignment(cost)
    matches:      Dict[int, int] = {}
    unmatched_tr: Set[int]       = set(range(cost.shape[0]))
    unmatched_dt: Set[int]       = set(range(cost.shape[1]))

    for r, c in zip(row_ind, col_ind):
        if cost[r, c] <= max_cost:
            matches[r] = c
            unmatched_tr.discard(r)
            unmatched_dt.discard(c)

    return matches, unmatched_tr, unmatched_dt


# ── ObjectTracker ─────────────────────────────────────────────────────────────

class ObjectTracker:
    def __init__(
        self,
        max_lost:       int   = 150,
        min_iou:        float = 0.3,
        conveyor_axis:  str   = "y",   # "y" for top-down, "x" for side-to-side
    ):
        self.tracks:       Dict[int, Track] = {}
        self.next_id:      int  = 0
        self.max_lost:     int  = max_lost
        self._max_cost:    float = min(0.95, 1.0 - min_iou + 0.12)
        self.conveyor_axis: str = conveyor_axis

    def update(self, detections: sv.Detections) -> sv.Detections:
        # Predict all tracks one step forward
        for trk in self.tracks.values():
            trk.predict()

        if len(detections) == 0:
            self._age_and_evict(set())
            empty = sv.Detections.empty()
            empty.tracker_id = np.array([], dtype=int)
            return empty

        bboxes      = detections.xyxy
        confs       = detections.confidence
        n_dets      = len(detections)
        tracker_ids = np.full(n_dets, -1, dtype=int)
        matched_tids: Set[int] = set()

        hi_idx = np.where(confs >= _HIGH_CONF)[0]
        lo_idx = np.where(confs <  _HIGH_CONF)[0]

        # ── Stage 1: high-conf dets × confirmed tracks ─────────────────────
        confirmed  = [t for t in self.tracks.values() if t.confirmed]
        lost_pool: List[Track] = []

        if confirmed and len(hi_idx):
            cost1 = _build_cost_matrix(
                confirmed, bboxes[hi_idx], self.conveyor_axis
            )
            m1, unm_tr1, unm_dt1 = _hungarian_match(cost1, self._max_cost)
            for tr_r, det_l in m1.items():
                trk = confirmed[tr_r]
                g   = int(hi_idx[det_l])
                trk.update(bboxes[g])
                tracker_ids[g] = trk.track_id
                matched_tids.add(trk.track_id)
            lost_pool    = [confirmed[r] for r in unm_tr1]
            unmatched_hi = [int(hi_idx[c]) for c in unm_dt1]
        else:
            lost_pool    = list(confirmed)
            unmatched_hi = hi_idx.tolist()

        # ── Stage 2: low-conf dets × lost confirmed tracks (recovery) ──────
        if lost_pool and len(lo_idx):
            cost2 = _build_cost_matrix(
                lost_pool, bboxes[lo_idx], self.conveyor_axis
            )
            m2, _, _ = _hungarian_match(cost2, self._max_cost)
            for tr_r, det_l in m2.items():
                trk = lost_pool[tr_r]
                g   = int(lo_idx[det_l])
                trk.update(bboxes[g])
                tracker_ids[g] = trk.track_id
                matched_tids.add(trk.track_id)

        # ── Stage 3: remaining high-conf × tentative tracks ─────────────────
        tentative    = [t for t in self.tracks.values()
                        if not t.confirmed and t.track_id not in matched_tids]
        remaining_hi = [g for g in unmatched_hi if tracker_ids[g] == -1]

        if tentative and remaining_hi:
            cost3 = _build_cost_matrix(
                tentative, bboxes[np.array(remaining_hi)], self.conveyor_axis
            )
            m3, _, unm_dt3 = _hungarian_match(cost3, self._max_cost)
            for tr_r, det_l in m3.items():
                trk = tentative[tr_r]
                g   = remaining_hi[det_l]
                trk.update(bboxes[g])
                tracker_ids[g] = trk.track_id
                matched_tids.add(trk.track_id)
            new_dets = [remaining_hi[c] for c in unm_dt3
                        if tracker_ids[remaining_hi[c]] == -1]
        else:
            new_dets = [g for g in remaining_hi if tracker_ids[g] == -1]

        # ── Stage 4: new tentative tracks ───────────────────────────────────
        for g in new_dets:
            tid            = self._new_track(bboxes[g])
            tracker_ids[g] = tid

        self._age_and_evict(matched_tids)

        return sv.Detections(
            xyxy=bboxes,
            confidence=confs,
            class_id=detections.class_id,
            tracker_id=tracker_ids,
            mask=detections.mask if detections.mask is not None else None,
        )

    def _new_track(self, bbox: np.ndarray) -> int:
        tid = self.next_id
        self.next_id += 1
        self.tracks[tid] = Track(bbox, tid)
        return tid

    def _age_and_evict(self, matched_tids: Set[int]) -> None:
        for tid, trk in self.tracks.items():
            if tid not in matched_tids:
                trk.age += 1
        to_evict = [tid for tid, trk in self.tracks.items()
                    if trk.age > self.max_lost]
        for tid in to_evict:
            del self.tracks[tid]
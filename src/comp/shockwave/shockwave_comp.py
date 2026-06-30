import math
import numpy as np
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


class InflectionType(Enum):
    RELEASE     = "release"      # slope increases → green
    COMPRESSION = "compression"  # slope decreases → red


@dataclass
class InflectionPoint:
    veh_id:     int
    time:       float
    distance:   float
    type:       Optional[InflectionType] = None


@dataclass
class ShockwaveResult:
    id:         int
    mode:       str            # "semi_auto" | "manual" | "automatic"
    slope:      float          # shockwave speed (distance unit / time unit)
    intercept:  float
    r_squared:  float
    equation:   str            # "distance = {slope} * time + {intercept}"
    points:     List[Tuple[float, float]] = field(default_factory=list)
    # Full InflectionPoint objects (veh_id + type) behind `points` above.
    # Lets a committed shockwave be re-opened for editing, hit-tested by
    # position, and colored by its locked inflection type.
    source_points: List[InflectionPoint] = field(default_factory=list)
    point_type:    Optional[InflectionType] = None   # None → Manual mode
    # bfs_chain populated only by Automatic mode (Phase 3):
    bfs_chain:  List[Tuple[float, float]] = field(default_factory=list)

    @property
    def num_points(self) -> int:
        return len(self.points)


class ShockwaveComp:

    # ── Rolling mean helper ───────────────────────────────────────────────

    @staticmethod
    def _rolling_mean(signal: np.ndarray, window: int) -> np.ndarray:
        """Centred rolling arithmetic mean, same length as input.

        Uses a cumulative-sum trick for speed; boundary bins use whatever
        samples are available (min_periods=1 equivalent).
        """
        n      = len(signal)
        half   = window // 2
        result = np.empty(n, dtype=float)
        cs     = np.concatenate(([0.0], np.cumsum(signal)))
        for i in range(n):
            lo = max(0, i - half)
            hi = min(n - 1, i + half)
            result[i] = (cs[hi + 1] - cs[lo]) / (hi - lo + 1)
        return result

    # ── OLS helper ───────────────────────────────────────────────────────

    @staticmethod
    def _ols(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
        """Ordinary least-squares fit; returns (slope, intercept).

        Falls back to (0, mean(y)) when the x-range is degenerate.
        """
        n    = len(x)
        xbar = x.mean()
        ybar = y.mean()
        denom = np.sum((x - xbar) ** 2)
        if denom < 1e-12:
            return 0.0, float(ybar)
        slope     = float(np.sum((x - xbar) * (y - ybar)) / denom)
        intercept = float(ybar - slope * xbar)
        return slope, intercept

    # ── R² helper ────────────────────────────────────────────────────────

    @staticmethod
    def _r_squared(x: np.ndarray, y: np.ndarray,
                   slope: float, intercept: float) -> float:
        y_pred  = slope * x + intercept
        ss_res  = float(np.sum((y - y_pred) ** 2))
        ss_tot  = float(np.sum((y - y.mean()) ** 2))
        if ss_tot <= 0.0:
            return 1.0
        return 1.0 - ss_res / ss_tot

    # ── Rolling OLS helper ───────────────────────────────────────────────

    @staticmethod
    def _rolling_ols_windows(times: np.ndarray, signal: np.ndarray, min_seg: int):
        """Compute left/right-window OLS slope+intercept at every interior
        index using incrementally-maintained moment sums, in O(n) total
        instead of recomputing each window's regression from scratch
        (which is O(n * min_seg), i.e. effectively quadratic in n since
        min_seg itself grows with n).

        For index i, the "left window" is times[i-min_seg : i+1] and the
        "right window" is times[i : i+min_seg+1] — identical definition to
        the original per-index slicing approach, just computed via rolling
        sums of x, y, xy, x^2 instead of fresh slices + fresh OLS each step.

        Window degeneracy (fewer than 2 unique time values) is tracked via
        a rolling count of (value -> occurrences) using a dict, so the
        "distinct values present" check is also O(1) amortized per slide
        step rather than re-scanning the window (which would silently
        reintroduce an O(n * min_seg) cost while looking "incremental").

        Returns:
            m_left, b_left, m_right, b_right, valid : five np.ndarray of
            length n. valid[i] is False where either window has fewer than
            2 unique time values (degenerate fit), matching the original
            np.unique-based skip condition. Values are 0 where invalid.
        """
        n = len(times)
        w = min_seg + 1  # number of points in each window

        m_left  = np.zeros(n, dtype=float)
        b_left  = np.zeros(n, dtype=float)
        m_right = np.zeros(n, dtype=float)
        b_right = np.zeros(n, dtype=float)
        right_valid = np.zeros(n, dtype=bool)
        left_valid  = np.zeros(n, dtype=bool)

        def window_ols(sx, sy, sxy, sxx, count):
            """Closed-form OLS slope/intercept from moment sums."""
            denom = count * sxx - sx * sx
            if denom <= 1e-12:
                return 0.0, (sy / count if count else 0.0)
            slope     = (count * sxy - sx * sy) / denom
            intercept = (sy - slope * sx) / count
            return slope, intercept

        class _RollingDistinctCounter:
            """O(1) amortized add/remove, tracks number of distinct values
            currently in the window via a value -> count dict."""
            __slots__ = ("counts", "distinct")

            def __init__(self):
                self.counts  = {}
                self.distinct = 0

            def add(self, v):
                c = self.counts.get(v, 0)
                self.counts[v] = c + 1
                if c == 0:
                    self.distinct += 1

            def remove(self, v):
                c = self.counts[v]
                if c == 1:
                    del self.counts[v]
                    self.distinct -= 1
                else:
                    self.counts[v] = c - 1

        if n >= w:
            # ── Right windows: right_window(i) = times[i : i+w] ──────────
            sx  = float(np.sum(times[0:w]))
            sy  = float(np.sum(signal[0:w]))
            sxy = float(np.sum(times[0:w] * signal[0:w]))
            sxx = float(np.sum(times[0:w] * times[0:w]))
            counter = _RollingDistinctCounter()
            for v in times[0:w]:
                counter.add(float(v))

            for i in range(0, n - w + 1):
                m_right[i], b_right[i] = window_ols(sx, sy, sxy, sxx, w)
                right_valid[i] = counter.distinct >= 2

                if i + w < n:
                    old_t, old_s = float(times[i]), float(signal[i])
                    new_t, new_s = float(times[i + w]), float(signal[i + w])
                    sx  += new_t - old_t
                    sy  += new_s - old_s
                    sxy += new_t * new_s - old_t * old_s
                    sxx += new_t * new_t - old_t * old_t
                    counter.remove(old_t)
                    counter.add(new_t)

            # ── Left windows: left_window(i) = times[i-min_seg : i+1] ────
            sx  = float(np.sum(times[0:w]))
            sy  = float(np.sum(signal[0:w]))
            sxy = float(np.sum(times[0:w] * signal[0:w]))
            sxx = float(np.sum(times[0:w] * times[0:w]))
            counter = _RollingDistinctCounter()
            for v in times[0:w]:
                counter.add(float(v))

            idx = w - 1  # left_window(idx) == times[0:w]
            m_left[idx], b_left[idx] = window_ols(sx, sy, sxy, sxx, w)
            left_valid[idx] = counter.distinct >= 2

            for i in range(idx + 1, n):
                old_t, old_s = float(times[i - w]), float(signal[i - w])
                new_t, new_s = float(times[i]),     float(signal[i])
                sx  += new_t - old_t
                sy  += new_s - old_s
                sxy += new_t * new_s - old_t * old_s
                sxx += new_t * new_t - old_t * old_t
                counter.remove(old_t)
                counter.add(new_t)

                m_left[i], b_left[i] = window_ols(sx, sy, sxy, sxx, w)
                left_valid[i] = counter.distinct >= 2

        valid = right_valid & left_valid
        return m_left, b_left, m_right, b_right, valid

    # ── Core algorithm ───────────────────────────────────────────────────

    @staticmethod
    def identify_inflection_points(
        veh_id:     int,
        times:      np.ndarray,
        distances:  np.ndarray,
        percentile: float,
    ) -> List[InflectionPoint]:
        """Detect inflection points on a single trajectory.

        Implements the pseudocode from ShockwaveAnalysisReport_v1.0,
        Section "Inflection Point Identification".

        Performance note: Step 2 (per-index left/right window regression)
        is computed via incremental rolling OLS moment sums rather than
        re-slicing and re-fitting each window from scratch. This reduces
        Step 2 from O(n * min_seg) to O(n) overall — significant because
        min_seg itself grows with n (min_seg = max(3, floor(n/20))),
        making the original per-index re-fit approach effectively
        quadratic for long trajectories. Output is numerically identical
        to the original (same closed-form OLS, same degeneracy/skip
        conditions); only the computation path changed.

        Constants (from pseudocode):
            rolling window width  w = 9
            continuity penalty coefficient = 0.15
            min_separation = max(2, floor(n / 10))
            max candidates retained per trajectory = 8
        """
        times     = np.asarray(times,     dtype=float)
        distances = np.asarray(distances, dtype=float)
        n         = len(times)

        WIN = 9  # rolling window width

        # Guard: too few points
        if n < WIN:
            return []

        min_seg = max(3, int(math.floor(n / 20)))

        if n < 2 * min_seg + 3:
            return []

        # Step 1 – smooth the distance signal
        s_smooth = ShockwaveComp._rolling_mean(distances, WIN)

        # Step 2 – score every interior index, via incremental rolling OLS
        scores      = np.zeros(n, dtype=float)
        slope_delta = np.zeros(n, dtype=float)

        m_left, b_left, m_right, b_right, valid = ShockwaveComp._rolling_ols_windows(
            times, s_smooth, min_seg
        )

        for i in range(min_seg, n - min_seg):
            if not valid[i]:
                continue

            m_l, b_l = m_left[i],  b_left[i]
            m_r, b_r = m_right[i], b_right[i]

            slope_jump      = abs(m_r - m_l)
            continuity_gap  = abs((m_l * times[i] + b_l) - (m_r * times[i] + b_r))
            scores[i]       = slope_jump - 0.15 * continuity_gap
            slope_delta[i]  = m_r - m_l

        # Step 3 – apply percentile threshold over interior range
        interior = scores[min_seg : n - min_seg]
        if len(interior) == 0:
            return []

        threshold     = float(np.percentile(interior, percentile))
        candidate_idx = [i for i in range(min_seg, n - min_seg)
                         if scores[i] >= threshold]

        if not candidate_idx:
            return []

        # Step 4 – rank by score (descending) and enforce minimum spacing
        candidate_idx.sort(key=lambda i: scores[i], reverse=True)
        min_separation = max(2, int(math.floor(n / 10)))

        selected: List[int] = []
        for idx in candidate_idx:
            if idx < min_seg or idx >= n - min_seg:
                continue
            if all(abs(idx - s) >= min_separation for s in selected):
                selected.append(idx)
            if len(selected) >= 8:   # max 8 candidates per trajectory
                break

        if not selected:
            return []

        # Step 5 – build output list (time-sorted)
        selected.sort()
        candidates: List[InflectionPoint] = []
        for idx in selected:
            itype = (InflectionType.RELEASE
                     if slope_delta[idx] > 0
                     else InflectionType.COMPRESSION)
            candidates.append(InflectionPoint(
                veh_id   = veh_id,
                time     = float(times[idx]),
                distance = float(distances[idx]),
                type     = itype,
            ))
        return candidates

    # ── Cache builder ─────────────────────────────────────────────────────

    @staticmethod
    def rebuild_inflection_cache(
        series_dict: dict,
        percentile:  float,
    ) -> dict:
        """Call identify_inflection_points for every series that has data.

        Args:
            series_dict: TimeSpacePlotWidget._series
            percentile:  current Min Slope Change value (0–95)

        Returns:
            dict mapping veh_id -> List[InflectionPoint]
        """
        cache = {}
        for veh_id, series in series_dict.items():
            times = series.get("veh_time")
            dists = series.get("veh_dist_trav")
            if times is not None and len(times) > 0:
                result = ShockwaveComp.identify_inflection_points(
                    veh_id, times, dists, percentile
                )
                cache[veh_id] = result if result is not None else []
            else:
                cache[veh_id] = []
        return cache

    # ── Trajectory ordering ───────────────────────────────────────────────

    @staticmethod
    def build_trajectory_order(
        reference_distance: float,
        series_dict:        dict,
    ) -> List[int]:
        """Order veh_ids by the time each trajectory crosses reference_distance.

        Implements pseudocode BuildTrajectoryOrder. Trajectories that do
        not reach reference_distance (it lies outside their distance range)
        are excluded from the returned list.

        Returns:
            List[int] of veh_ids sorted ascending by crossing time.
        """
        traj_times: List[Tuple[int, float]] = []

        for veh_id, series in series_dict.items():
            times = series.get("veh_time")
            dists = series.get("veh_dist_trav")
            if times is None or dists is None or len(times) < 2:
                continue

            times = np.asarray(times, dtype=float)
            dists = np.asarray(dists, dtype=float)

            d_min, d_max = dists.min(), dists.max()
            if reference_distance < d_min or reference_distance > d_max:
                continue

            # Linear interpolation: find time when trajectory crosses
            # reference_distance.  Use the first crossing found when
            # traversing the array in order.
            t_ref = None
            for k in range(len(dists) - 1):
                d0, d1 = dists[k], dists[k + 1]
                if (d0 <= reference_distance <= d1) or (d1 <= reference_distance <= d0):
                    span = d1 - d0
                    if abs(span) < 1e-12:
                        t_ref = float(times[k])
                    else:
                        frac  = (reference_distance - d0) / span
                        t_ref = float(times[k] + frac * (times[k + 1] - times[k]))
                    break

            if t_ref is not None:
                traj_times.append((veh_id, t_ref))

        traj_times.sort(key=lambda x: x[1])
        return [veh_id for veh_id, _ in traj_times]

    # ── Adjacency ─────────────────────────────────────────────────────────

    @staticmethod
    def get_adjacent_trajectories(
        last_veh_id:      int,
        ordered_sequence: List[int],
    ) -> set:
        """Return the set of veh_ids immediately before and after
        last_veh_id in ordered_sequence.

        Implements pseudocode GetAdjacentTrajectories.
        Returns a set with 0, 1, or 2 elements.
        """
        if last_veh_id not in ordered_sequence:
            return set()

        idx   = ordered_sequence.index(last_veh_id)
        valid = set()
        if idx > 0:
            valid.add(ordered_sequence[idx - 1])
        if idx < len(ordered_sequence) - 1:
            valid.add(ordered_sequence[idx + 1])
        return valid

    # ── Regression and reporting ──────────────────────────────────────────

    @staticmethod
    def fit_and_report(
        points:       List[Tuple[float, float]],
        mode:         str,
        shockwave_id: int,
    ) -> "ShockwaveResult":
        """OLS linear regression through (time, distance) points.

        Implements pseudocode FitAndReport (shared across all modes).

        Args:
            points:       list of (time, distance) pairs, at least 2 entries
            mode:         "manual" | "semi_auto" | "automatic"
            shockwave_id: 1-based identifier assigned by the caller

        Returns:
            ShockwaveResult with slope, intercept, r_squared, equation, and
            the input points stored verbatim.

        Raises:
            ValueError: if fewer than 2 points are supplied.
        """
        if len(points) < 2:
            raise ValueError("fit_and_report requires at least 2 points.")

        x = np.array([p[0] for p in points], dtype=float)
        y = np.array([p[1] for p in points], dtype=float)

        slope, intercept = ShockwaveComp._ols(x, y)
        r2               = ShockwaveComp._r_squared(x, y, slope, intercept)

        equation = f"distance = {slope:.4f} * time + {intercept:.4f}"

        return ShockwaveResult(
            id        = shockwave_id,
            mode      = mode,
            slope     = slope,
            intercept = intercept,
            r_squared = r2,
            equation  = equation,
            points    = list(points),
        )

    # ── Phase 3 stubs (Automatic mode — implement when needed) ────────────

    @staticmethod
    def bfs_collect(seed, inflection_cache, vicinity, series_dict):
        raise NotImplementedError("Phase 3 — Automatic mode")

    @staticmethod
    def best_fit_by_elimination(chain):
        raise NotImplementedError("Phase 3 — Automatic mode")
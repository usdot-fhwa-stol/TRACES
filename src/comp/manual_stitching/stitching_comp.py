"""Manual Trajectory Stitching -- computational core (Task 9.2).

Qt-free: the preview dialog calls ManualStitchingComp.prepare() once per
selected pair, StitchInputs.plan() on every slider / ID change, and
ManualStitchingComp.apply() on Apply.

Temporal cases (spec "Stitching Logic"), with dt = the event's sampling
interval (comp.trajectory_utils.event_time_step) and
gap = second.start - first.end:

    gap <= 0            OVERLAP     both have observations in a shared span
    0 < gap <= 1.5*dt   CONTINUOUS  second starts at the next expected sample
    gap > 1.5*dt        GAP         at least one expected sample missing

The 1.5*dt boundary is the spec's "timestamp immediately preceding the
first observation of the second trajectory" with half a step of tolerance
for float jitter.

Overlap split. The overlap window is [second.start, min(first.end,
second.end)]. split_fraction p in [0, 1] is the share of that window kept
from the first trajectory (spec default 50%):

    t_split = overlap_start + p * (overlap_end - overlap_start)
    first  contributes t <  t_split   (t <= t_split when p == 1)
    second contributes t >= t_split   (t >  t_split when p == 1)

so p=0 keeps all of the second trajectory's overlap and p=1 all of the
first's. The two sets are disjoint by construction, so the stitched
trajectory can never contain a duplicate timestamp.

Containment (second lies entirely within first's span) isn't addressed
by the spec. Here it is an OVERLAP with contained=True: the first
trajectory also contributes t > second.end, so nothing after the second
trajectory ends is lost, and the result has two seams.

Only measured rows are used: SplineComp synthetic rows (imputed_status =
'S', no measurement) are excluded from both preview and apply. Gap-filled
rows (A/M) are included and keep their imputed_status.
"""

import json
import logging
import math
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from utils import (
    events_columns, measured_rows_clause,
    TRAJ_STATUS_COL, TRAJ_REMOVED, TRAJ_STITCHED,
)
from ..base_comp import BaseComp, DERIVED_EVENTS_COLUMNS, ensure_traj_status_column
from ..trajectory_utils import TrajectoryData, event_time_step, load_trajectory

logger = logging.getLogger(__name__)

NAN = float("nan")
_CONTINUOUS_MAX_STEPS = 1.5

STITCHES_TABLE = "stitches"
_STITCHES_DDL = f"""
CREATE TABLE IF NOT EXISTS {STITCHES_TABLE} (
    stitch_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL,
    new_veh_id      INTEGER NOT NULL,
    source_veh_ids  TEXT    NOT NULL,   -- JSON list, chronological order
    temporal_case   TEXT    NOT NULL,   -- 'gap' | 'continuous' | 'overlap'
    split_fraction  REAL,               -- overlap only
    split_time      REAL,               -- overlap only
    seam_json       TEXT    NOT NULL,   -- SeamInfo + JoinMetrics
    safety_warnings TEXT    NOT NULL,   -- JSON list of messages shown at Apply
    created_at      TEXT    NOT NULL,   -- local ISO-8601
    UNIQUE (event_id, new_veh_id)
)"""


# ── Value types ─────────────────────────────────────────────────────────────

class TemporalCase(str, Enum):
    GAP = "gap"
    CONTINUOUS = "continuous"
    OVERLAP = "overlap"


@dataclass(frozen=True)
class TimeRange:
    """A veh_time interval, unbounded where lo/hi is None. One definition
    drives both the numpy preview mask and the SQL WHERE clause."""
    lo: Optional[float] = None
    hi: Optional[float] = None
    lo_incl: bool = True
    hi_incl: bool = False

    def mask(self, t: np.ndarray) -> np.ndarray:
        m = np.ones(t.shape, dtype=bool)
        if self.lo is not None:
            m &= (t >= self.lo) if self.lo_incl else (t > self.lo)
        if self.hi is not None:
            m &= (t <= self.hi) if self.hi_incl else (t < self.hi)
        return m

    def sql(self) -> Tuple[str, List[float]]:
        parts, params = [], []
        if self.lo is not None:
            parts.append("veh_time >= ?" if self.lo_incl else "veh_time > ?")
            params.append(float(self.lo))
        if self.hi is not None:
            parts.append("veh_time <= ?" if self.hi_incl else "veh_time < ?")
            params.append(float(self.hi))
        return (" AND ".join(parts) or "1"), params


@dataclass
class SeamInfo:
    """Temporal relationship between the chronologically ordered pair."""
    case: TemporalCase
    time_step: float
    first_veh_id: int
    second_veh_id: int
    first_end: float
    second_start: float
    gap_duration: float                 # second_start - first_end (<= 0 when overlapping)
    missing_samples: int = 0            # GAP only
    overlap_start: Optional[float] = None
    overlap_end: Optional[float] = None
    contained: bool = False
    n_overlap_first: int = 0
    n_nonoverlap_first: int = 0
    n_overlap_second: int = 0
    n_nonoverlap_second: int = 0

    @property
    def overlap_duration(self) -> float:
        if self.overlap_start is None:
            return 0.0
        return self.overlap_end - self.overlap_start


@dataclass
class JoinMetrics:
    """Discontinuity where the stitched trajectory switches source."""
    from_veh_id: int
    to_veh_id: int
    t_before: float
    t_after: float
    dt: float
    d_dist: float
    implied_speed: float                # d_dist / dt
    speed_before: float = NAN
    speed_after: float = NAN
    d_speed: float = NAN


@dataclass
class StitchPreview:
    trajectory: TrajectoryData          # veh_id = proposed new ID
    source: np.ndarray                  # source veh_id per point


@dataclass
class StitchPlan:
    event_id: int
    new_veh_id: int
    first: TrajectoryData
    second: TrajectoryData
    seam: SeamInfo
    split_fraction: Optional[float]     # None unless OVERLAP
    split_time: Optional[float]
    ranges: Dict[int, Tuple[TimeRange, ...]]
    preview: StitchPreview
    joins: List[JoinMetrics] = field(default_factory=list)

    @property
    def source_veh_ids(self) -> Tuple[int, int]:
        return self.first.veh_id, self.second.veh_id


# ── Pure functions ──────────────────────────────────────────────────────────

def order_pair(a: TrajectoryData, b: TrajectoryData) -> Tuple[TrajectoryData, TrajectoryData]:
    """Chronological order -- by start time, then end time, then veh_id --
    independent of the order the user clicked them."""
    key = lambda tr: (tr.start_time, tr.end_time, tr.veh_id)
    return (a, b) if key(a) <= key(b) else (b, a)


def analyze_seam(first: TrajectoryData, second: TrajectoryData,
                 time_step: float) -> SeamInfo:
    """Classify a chronologically ordered pair (see module docstring)."""
    gap = second.start_time - first.end_time
    info = SeamInfo(
        case=TemporalCase.GAP, time_step=float(time_step),
        first_veh_id=first.veh_id, second_veh_id=second.veh_id,
        first_end=first.end_time, second_start=second.start_time,
        gap_duration=float(gap),
        n_nonoverlap_first=first.n, n_nonoverlap_second=second.n,
    )
    if gap <= 0:
        o_start = second.start_time
        o_end = min(first.end_time, second.end_time)
        in_first = int(np.count_nonzero((first.time >= o_start) & (first.time <= o_end)))
        in_second = int(np.count_nonzero((second.time >= o_start) & (second.time <= o_end)))
        info.case = TemporalCase.OVERLAP
        info.overlap_start, info.overlap_end = float(o_start), float(o_end)
        info.contained = second.end_time <= first.end_time
        info.n_overlap_first, info.n_nonoverlap_first = in_first, first.n - in_first
        info.n_overlap_second, info.n_nonoverlap_second = in_second, second.n - in_second
    elif gap <= _CONTINUOUS_MAX_STEPS * time_step:
        info.case = TemporalCase.CONTINUOUS
    else:
        info.missing_samples = max(int(round(gap / time_step)) - 1, 0)
    return info


def _mask(traj: TrajectoryData, ranges: Sequence[TimeRange]) -> np.ndarray:
    m = np.zeros(traj.time.shape, dtype=bool)
    for r in ranges:
        m |= r.mask(traj.time)
    return m


def _build_preview(first: TrajectoryData, second: TrajectoryData,
                   ranges: Dict[int, Tuple[TimeRange, ...]],
                   new_veh_id: int) -> StitchPreview:
    parts = [(tr, _mask(tr, ranges[tr.veh_id])) for tr in (first, second)]
    t = np.concatenate([tr.time[m] for tr, m in parts])
    order = np.argsort(t, kind="stable")

    def cat(attr):
        arrays = [getattr(tr, attr) for tr, _ in parts]
        if any(a is None for a in arrays):
            return None
        return np.concatenate([a[m] for a, (_, m) in zip(arrays, parts)])[order]

    source = np.concatenate(
        [np.full(int(np.count_nonzero(m)), tr.veh_id) for tr, m in parts])[order]
    t = t[order]
    if t.size >= 2 and np.any(np.diff(t) <= 0):
        # Unreachable given disjoint ranges; guards against future edits.
        raise ValueError("Stitched trajectory would contain duplicate timestamps.")

    return StitchPreview(
        trajectory=TrajectoryData(
            event_id=first.event_id, veh_id=new_veh_id,
            time=t, dist=cat("dist"), speed=cat("speed"), accel=cat("accel"),
            lane_id=cat("lane_id"), imputed_status=cat("imputed_status"),
        ),
        source=source,
    )


def _join_metrics(preview: StitchPreview) -> List[JoinMetrics]:
    tr, src = preview.trajectory, preview.source
    joins = []
    for i in np.nonzero(src[1:] != src[:-1])[0]:
        dt = float(tr.time[i + 1] - tr.time[i])
        d_dist = float(tr.dist[i + 1] - tr.dist[i])
        jm = JoinMetrics(
            from_veh_id=int(src[i]), to_veh_id=int(src[i + 1]),
            t_before=float(tr.time[i]), t_after=float(tr.time[i + 1]),
            dt=dt, d_dist=d_dist,
            implied_speed=d_dist / dt if dt > 0 else NAN,
        )
        if tr.speed is not None:
            jm.speed_before = float(tr.speed[i])
            jm.speed_after = float(tr.speed[i + 1])
            jm.d_speed = jm.speed_after - jm.speed_before
        joins.append(jm)
    return joins


def build_plan(first: TrajectoryData, second: TrajectoryData, time_step: float,
               new_veh_id: int, split_fraction: float = 0.5,
               seam: Optional[SeamInfo] = None) -> StitchPlan:
    """Build the stitch for a chronologically ordered pair. Cheap enough
    to call on every slider move. split_fraction is ignored unless the
    pair overlaps."""
    if first.veh_id == second.veh_id:
        raise ValueError("Cannot stitch a trajectory to itself.")
    if first.n == 0 or second.n == 0:
        raise ValueError("Both trajectories need at least one measured observation.")

    seam = seam or analyze_seam(first, second, time_step)
    p = t_split = None

    if seam.case is TemporalCase.OVERLAP:
        p = float(min(max(split_fraction, 0.0), 1.0))
        t_split = seam.overlap_start + p * (seam.overlap_end - seam.overlap_start)
        full_first = p >= 1.0   # ties at t_split go to first only at p == 1
        first_ranges = [TimeRange(hi=t_split, hi_incl=full_first)]
        if seam.contained:
            first_ranges.append(TimeRange(lo=second.end_time, lo_incl=False))
        ranges = {first.veh_id: tuple(first_ranges),
                  second.veh_id: (TimeRange(lo=t_split, lo_incl=not full_first),)}
    else:
        ranges = {first.veh_id: (TimeRange(),), second.veh_id: (TimeRange(),)}

    preview = _build_preview(first, second, ranges, new_veh_id)
    if preview.trajectory.n < 2:
        raise ValueError("Stitched trajectory would have fewer than two observations.")

    return StitchPlan(
        event_id=first.event_id, new_veh_id=new_veh_id,
        first=first, second=second, seam=seam,
        split_fraction=p, split_time=t_split,
        ranges=ranges, preview=preview, joins=_join_metrics(preview),
    )


def _json_safe(obj: Any) -> Any:
    """NaN/inf -> None, numpy scalars -> Python, Enum -> value."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


# ── Dialog-facing inputs ────────────────────────────────────────────────────

@dataclass
class StitchInputs:
    """Everything that stays fixed while the preview dialog is open. The
    seam (and so the safety check) doesn't depend on the split slider,
    so it is computed once here."""
    event_id: int
    first: TrajectoryData
    second: TrajectoryData
    time_step: float
    seam: SeamInfo
    default_new_veh_id: int

    def plan(self, new_veh_id: int, split_fraction: float = 0.5) -> StitchPlan:
        return build_plan(self.first, self.second, self.time_step,
                          new_veh_id, split_fraction, seam=self.seam)


# ── BaseComp subclass ──────────────────────────────────────────────────────

class ManualStitchingComp(BaseComp):
    """Right-click Trajectory Stitching.

    comp_series requires exactly two veh_ids and returns [StitchPlan];
    BaseComp.comp()/comp_event() (veh_ids=None) are not meaningful here
    and raise ValueError.
    """

    OPTIONS_SCHEMA: Dict[str, Any] = {
        "split_fraction": 0.5,
        "new_veh_id": None,      # None = next available
    }

    @staticmethod
    def ensure_schema(conn: sqlite3.Connection) -> None:
        """events.traj_status (+ inherit trigger) and the stitches table.
        Commits -- call before the data transaction, never inside it."""
        ensure_traj_status_column(conn)
        conn.execute(_STITCHES_DDL)
        conn.commit()

    # ── ID and status queries ──────────────────────────────────────────

    @staticmethod
    def next_available_veh_id(conn: sqlite3.Connection, event_id: int) -> int:
        """One more than the largest veh_id in the event, including
        removed trajectories, so an ID is never reused."""
        row = conn.execute("SELECT MAX(veh_id) FROM events WHERE event_id = ?",
                           (event_id,)).fetchone()
        return (row[0] + 1) if row and row[0] is not None else 1

    @staticmethod
    def is_veh_id_available(conn: sqlite3.Connection, event_id: int, veh_id: int) -> bool:
        """False if any row in the event -- active or removed -- uses veh_id
        (spec: no conflict with the active dataset or processed-data layer)."""
        return conn.execute(
            "SELECT 1 FROM events WHERE event_id = ? AND veh_id = ? LIMIT 1",
            (event_id, int(veh_id))).fetchone() is None

    @staticmethod
    def is_trajectory_active(conn: sqlite3.Connection, event_id: int, veh_id: int) -> bool:
        status = (f" AND {TRAJ_STATUS_COL} <> '{TRAJ_REMOVED}'"
                  if TRAJ_STATUS_COL in events_columns(conn) else "")
        return conn.execute(
            f"SELECT 1 FROM events WHERE event_id = ? AND veh_id = ?{status} LIMIT 1",
            (event_id, veh_id)).fetchone() is not None

    # ── Preparation ────────────────────────────────────────────────────

    @classmethod
    def prepare(cls, conn: sqlite3.Connection, event_id: int,
                veh_id_a: int, veh_id_b: int) -> StitchInputs:
        """Validate and load a pair in click order; returns them in
        chronological order. Raises ValueError with a user-facing message."""
        if veh_id_a == veh_id_b:
            raise ValueError("Select a different trajectory to stitch with.")
        for v in (veh_id_a, veh_id_b):
            if not cls.is_trajectory_active(conn, event_id, v):
                raise ValueError(f"Vehicle {v} is not an active trajectory in event {event_id}.")

        a = load_trajectory(conn, event_id, veh_id_a)
        b = load_trajectory(conn, event_id, veh_id_b)
        for tr in (a, b):
            if tr.n == 0:
                raise ValueError(f"Vehicle {tr.veh_id} has no measured observations.")

        first, second = order_pair(a, b)
        time_step = event_time_step(conn, event_id)
        return StitchInputs(
            event_id=event_id, first=first, second=second, time_step=time_step,
            seam=analyze_seam(first, second, time_step),
            default_new_veh_id=cls.next_available_veh_id(conn, event_id),
        )

    def comp_series(self, db_connection: sqlite3.Connection, event_id: int,
                    veh_ids: Optional[Iterable[int]],
                    options: Optional[Dict[str, Any]] = None) -> List[StitchPlan]:
        options = self._resolve_options(options)
        veh_ids = list(veh_ids) if veh_ids is not None else []
        if len(veh_ids) != 2:
            raise ValueError("Trajectory stitching requires exactly two veh_ids.")
        inputs = self.prepare(db_connection, event_id, veh_ids[0], veh_ids[1])
        new_id = options["new_veh_id"]
        if new_id is None:
            new_id = inputs.default_new_veh_id
        return [inputs.plan(int(new_id), float(options["split_fraction"]))]

    # ── Persistence ─────────────────────────────────────────────────────

    @classmethod
    def apply(cls, conn: sqlite3.Connection, plan: StitchPlan,
              safety_warnings: Sequence[str] = ()) -> int:
        """Write *plan* atomically: insert stitched rows (traj_status S),
        mark sources removed (R), record lineage, refresh ranges.summary_json.
        Returns the new stitch_id. On any failure nothing is committed."""
        from data_import.db_utils import update_event_summary

        cls.ensure_schema(conn)
        event_id, new_id = plan.event_id, plan.new_veh_id

        if not cls.is_veh_id_available(conn, event_id, new_id):
            raise ValueError(f"Vehicle ID {new_id} is already used in event {event_id}.")
        for v in plan.source_veh_ids:
            if not cls.is_trajectory_active(conn, event_id, v):
                raise ValueError(f"Vehicle {v} is no longer an active trajectory.")

        cols = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
        select_exprs = []
        for c in cols:
            if c == "veh_id":
                select_exprs.append("?")
            elif c == TRAJ_STATUS_COL:
                select_exprs.append(f"'{TRAJ_STITCHED}'")
            elif c in DERIVED_EVENTS_COLUMNS:
                select_exprs.append("NULL")
            else:
                select_exprs.append(f'"{c}"')
        insert_sql = (
            f"INSERT INTO events ({', '.join(chr(34) + c + chr(34) for c in cols)}) "
            f"SELECT {', '.join(select_exprs)} FROM events "
            f"WHERE event_id = ? AND veh_id = ? AND {{cond}}{measured_rows_clause(set(cols))}"
        )

        seam_json = json.dumps(_json_safe({
            "seam": asdict(plan.seam),
            "joins": [asdict(j) for j in plan.joins],
        }))

        try:
            with conn:   # one transaction: commit on success, rollback on error
                inserted = 0
                for veh_id, ranges in plan.ranges.items():
                    for r in ranges:
                        cond, cond_params = r.sql()
                        cur = conn.execute(insert_sql.format(cond=cond),
                                           [new_id, event_id, veh_id, *cond_params])
                        inserted += cur.rowcount
                if inserted != plan.preview.trajectory.n:
                    raise RuntimeError(
                        f"Stitch wrote {inserted} rows but the preview shows "
                        f"{plan.preview.trajectory.n}; the data may have changed "
                        f"since the preview was built. Nothing was saved.")

                conn.execute(
                    f"UPDATE events SET {TRAJ_STATUS_COL} = ? "
                    f"WHERE event_id = ? AND veh_id IN (?, ?)",
                    (TRAJ_REMOVED, event_id, *plan.source_veh_ids))

                cur = conn.execute(
                    f"INSERT INTO {STITCHES_TABLE} (event_id, new_veh_id, source_veh_ids, "
                    f"temporal_case, split_fraction, split_time, seam_json, "
                    f"safety_warnings, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event_id, new_id, json.dumps(list(plan.source_veh_ids)),
                     plan.seam.case.value, plan.split_fraction, plan.split_time,
                     seam_json, json.dumps(list(safety_warnings)),
                     datetime.now().isoformat(timespec="seconds")))
                stitch_id = cur.lastrowid

                update_event_summary(conn, event_id)
        except Exception:
            logger.exception("Trajectory stitch failed; rolled back (event %s, sources %s)",
                             event_id, plan.source_veh_ids)
            raise

        logger.info("Stitched vehicles %s into %s (event %s, %s, stitch_id %s)",
                    plan.source_veh_ids, new_id, event_id, plan.seam.case.value, stitch_id)
        return stitch_id
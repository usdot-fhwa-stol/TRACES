import json
import sqlite3

import numpy as np
import pytest

from comp.trajectory_utils import TrajectoryData
from comp.manual_stitching import (
    ManualStitchingComp, SafetyContext, TemporalCase,
    analyze_seam, build_plan, evaluate_safety, order_pair,
)

DT = 0.1


def make_traj(veh_id, t0, t1, d0=0.0, speed=10.0):
    """Constant-speed trajectory on a 0.1 s grid. d0 is the distance at t0."""
    t = np.round(np.arange(t0, t1 + DT / 2, DT), 3)
    n = t.size
    return TrajectoryData(
        event_id=1, veh_id=veh_id, time=t, dist=d0 + speed * (t - t0),
        speed=np.full(n, speed), accel=np.zeros(n),
        lane_id=np.full(n, 1, dtype=object),
        imputed_status=np.full(n, " ", dtype=object),
    )


# ── Classification ─────────────────────────────────────────────────────

@pytest.mark.parametrize("b_start, case, missing", [
    (10.1, TemporalCase.CONTINUOUS, 0),
    (10.05, TemporalCase.CONTINUOUS, 0),   # off-grid jitter
    (10.2, TemporalCase.GAP, 1),
    (11.0, TemporalCase.GAP, 9),
    (10.0, TemporalCase.OVERLAP, 0),       # shared timestamp
    (9.0, TemporalCase.OVERLAP, 0),
])
def test_classification(b_start, case, missing):
    a = make_traj(1, 0.0, 10.0)
    b = make_traj(2, b_start, 20.0, d0=10.0 * b_start)
    seam = analyze_seam(a, b, DT)
    assert seam.case is case
    assert seam.missing_samples == missing


def test_order_is_chronological_not_click_order():
    a, b = make_traj(1, 0.0, 10.0), make_traj(2, 9.0, 20.0, d0=90.0)
    assert order_pair(b, a) == (a, b)


# ── Overlap split ──────────────────────────────────────────────────────

@pytest.mark.parametrize("p, n_from_first", [(0.0, 90), (0.5, 95), (1.0, 101)])
def test_overlap_split(p, n_from_first):
    a, b = make_traj(1, 0.0, 10.0), make_traj(2, 9.0, 20.0, d0=90.0)
    plan = build_plan(a, b, DT, new_veh_id=3, split_fraction=p)
    t = plan.preview.trajectory.time
    assert t.size == 201                      # 0.0 .. 20.0, no duplicates, no holes
    assert np.all(np.diff(t) > 0)
    assert np.count_nonzero(plan.preview.source == 1) == n_from_first
    assert len(plan.joins) == 1


def test_contained_keeps_first_after_second_ends():
    a, b = make_traj(1, 0.0, 20.0), make_traj(2, 5.0, 8.0, d0=50.0)
    full_first = build_plan(a, b, DT, 3, split_fraction=1.0)
    assert np.all(full_first.preview.source == 1) and full_first.preview.trajectory.n == 201
    half = build_plan(a, b, DT, 3, split_fraction=0.5)
    assert half.preview.trajectory.n == 201
    assert len(half.joins) == 2


# ── Safety rule ────────────────────────────────────────────────────────

def _safety(a, b):
    first, second = order_pair(a, b)
    return evaluate_safety(SafetyContext(first, second, analyze_seam(first, second, DT)))


def test_safety_warns_for_parallel_distant_tracks():
    assert _safety(make_traj(1, 0.0, 10.0), make_traj(2, 2.0, 12.0, d0=120.0))


def test_safety_quiet_when_tracks_coincide():
    assert not _safety(make_traj(1, 0.0, 10.0), make_traj(2, 2.0, 12.0, d0=20.0))


def test_safety_quiet_when_overlap_not_dominant_for_both():
    assert not _safety(make_traj(1, 0.0, 10.0), make_traj(2, 9.0, 20.0, d0=500.0))


def test_safety_quiet_for_gap():
    assert not _safety(make_traj(1, 0.0, 10.0), make_traj(2, 12.0, 20.0, d0=900.0))


# ── Database apply ─────────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE events (
        event_id INTEGER NOT NULL, veh_id INTEGER NOT NULL, veh_time REAL NOT NULL,
        lane_id INTEGER, veh_dist_trav REAL, veh_speed REAL, calc_position REAL,
        imputed_status TEXT NOT NULL DEFAULT ' ',
        PRIMARY KEY (event_id, veh_id, veh_time)) WITHOUT ROWID""")
    c.execute("CREATE TABLE ranges (event_id INTEGER PRIMARY KEY, summary_json TEXT NOT NULL)")
    for tr in (make_traj(1, 0.0, 10.0), make_traj(2, 9.0, 20.0, d0=90.0)):
        c.executemany(
            "INSERT INTO events (event_id, veh_id, veh_time, lane_id, veh_dist_trav, "
            "veh_speed, calc_position) VALUES (1, ?, ?, 1, ?, ?, ?)",
            [(tr.veh_id, float(t), float(d), float(s), float(d))
             for t, d, s in zip(tr.time, tr.dist, tr.speed)])
    # A SplineComp synthetic row: must be excluded from preview AND apply.
    c.execute("INSERT INTO events VALUES (1, 1, 10.05, 1, NULL, NULL, 100.5, 'S')")
    from data_import.db_utils import build_event_summary
    c.execute("INSERT INTO ranges VALUES (1, ?)", (json.dumps(build_event_summary(c, 1)),))
    c.commit()
    yield c
    c.close()


def test_apply(conn):
    inputs = ManualStitchingComp.prepare(conn, 1, 2, 1)   # reversed click order
    assert inputs.first.veh_id == 1 and inputs.default_new_veh_id == 3
    plan = inputs.plan(3, 0.5)
    stitch_id = ManualStitchingComp.apply(conn, plan, safety_warnings=[])

    status, n, n_calc = conn.execute(
        "SELECT MIN(traj_status), COUNT(*), COUNT(calc_position) "
        "FROM events WHERE veh_id = 3").fetchone()
    assert (status, n, n_calc) == ("S", plan.preview.trajectory.n, 0)
    assert {r[0] for r in conn.execute(
        "SELECT DISTINCT traj_status FROM events WHERE veh_id IN (1, 2)")} == {"R"}

    summary = json.loads(conn.execute("SELECT summary_json FROM ranges").fetchone()[0])
    assert summary["veh_id"] == [3]

    sources, case = conn.execute(
        "SELECT source_veh_ids, temporal_case FROM stitches WHERE stitch_id = ?",
        (stitch_id,)).fetchone()
    assert json.loads(sources) == [1, 2] and case == "overlap"

    # Trigger: a later insert into the stitched trajectory inherits 'S'.
    conn.execute("INSERT INTO events (event_id, veh_id, veh_time, lane_id, veh_dist_trav) "
                 "VALUES (1, 3, 30.0, 1, 300.0)")
    assert conn.execute("SELECT traj_status FROM events WHERE veh_id = 3 AND veh_time = 30.0"
                        ).fetchone()[0] == "S"


def test_apply_rejects_taken_id_and_removed_sources(conn):
    inputs = ManualStitchingComp.prepare(conn, 1, 1, 2)
    with pytest.raises(ValueError):
        ManualStitchingComp.apply(conn, inputs.plan(1, 0.5))       # ID in use
    ManualStitchingComp.apply(conn, inputs.plan(3, 0.5))
    with pytest.raises(ValueError):
        ManualStitchingComp.prepare(conn, 1, 1, 3)                  # 1 is now removed
    assert conn.execute("SELECT COUNT(*) FROM stitches").fetchone()[0] == 1

from comp.trajectory_utils import generate_complete_timestamps


def _grid(t0, t1, dt=0.1, offset=0.0):
    return np.round(np.arange(t0, t1 + dt / 2, dt) + offset, 3)


def test_regular_gap_filled_as_before():
    t = np.concatenate([_grid(0.0, 1.0), _grid(1.8, 3.0)])
    full = generate_complete_timestamps(t)
    assert full.size == t.size + 7
    assert np.allclose(full[11:18], np.arange(1.1, 1.75, 0.1))


def test_phase_shift_without_gap_has_no_fills():
    # 1712 pattern: on-grid, then every timestamp offset by 0.05 s
    t = np.concatenate([_grid(0.0, 3.0), _grid(3.05, 6.0, offset=0.0)])
    assert generate_complete_timestamps(t).size == t.size


def test_gap_across_phase_shift_continues_prior_phase():
    t = np.concatenate([_grid(0.0, 1.0), _grid(1.85, 3.0)])
    full = generate_complete_timestamps(t)
    fills = np.setdiff1d(full, t)
    assert fills.size == 7 and np.isclose(fills[0], 1.1) and np.isclose(fills[-1], 1.7)
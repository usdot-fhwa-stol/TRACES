import math
import sqlite3
import unittest

from comp.platoon.platoon_comp import PlatoonConsistencyComp, summarize


class PlatoonConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('CREATE TABLE events (event_id INTEGER, veh_id INTEGER, '
                          'veh_time REAL, lane_id INTEGER, veh_dist_trav REAL, '
                          'veh_speed REAL, preceding_veh_id INTEGER, spacing REAL)')
        self.addCleanup(self.conn.close)
        self.comp = PlatoonConsistencyComp()

    def add(self, vehicle, time, position, speed=10, leader=None, spacing=None,
            lane=1, event=1):
        self.conn.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                          (event, vehicle, time, lane, position, speed, leader, spacing))

    def run_comp(self, followers=None, **options):
        return self.comp.comp_series(self.conn, 1, followers, options)

    def test_selected_follower_uses_unselected_nearest_same_lane_leader(self):
        for t in (0, 0.1, 0.2):
            self.add(1, t, 10*t)
            self.add(2, t, 20+10*t)
            self.add(3, t, 5+10*t, lane=2)
            self.add(4, t, 30+10*t)
        pair, = self.run_comp([1])[0].pairs
        self.assertEqual(pair.leader_id, 2)
        self.assertEqual(pair.minimum_spacing, 20)
        self.assertEqual(pair.rmse_bias, 0)
        self.assertEqual(pair.sample_count, 3)

    def test_trapezoidal_bias_and_rmspe_ratio(self):
        for t, spacing in ((0, 10), (1, 12), (3, 18)):
            self.add(1, t, 0, speed=0, leader=2, spacing=spacing)
            self.add(2, t, 100, speed=2*t, lane=2)
        pair, = self.run_comp([1])[0].pairs
        # Integrated relative displacement: [0, 1, 9]; bias: [0, -1, 1].
        self.assertEqual(pair.min_bias, -1)
        self.assertEqual(pair.max_bias, 1)
        self.assertEqual(pair.mean_bias, 0)
        self.assertAlmostEqual(pair.rmse_bias, math.sqrt(2/3))
        self.assertAlmostEqual(pair.rmspe_bias, math.sqrt((1/144+1/324)/3))

    def test_thresholds_and_zero_spacing(self):
        self.add(1, 0, 0, leader=2, spacing=0)
        self.add(2, 0, 50)
        result = self.run_comp([1])
        self.assertTrue(math.isnan(result[0].pairs[0].rmspe_bias))
        self.assertEqual(summarize(result)['percentage_of_vehicle_pairs_with_unphysical_spacing'], 100)
        self.assertFalse(self.run_comp([1], unphysical_spacing_threshold=0)[0].pairs[0].unphysical_spacing)

    def test_search_distance_boundary(self):
        self.add(1, 0, 0)
        self.add(2, 0, 50)
        self.assertEqual(len(self.run_comp([1])[0].pairs), 1)
        self.assertEqual(self.run_comp([1], leader_search_distance_threshold=49)[0].pairs, [])

    def test_leader_changes_and_missing_frames_split_segments(self):
        for t in (0, 1, 2, 3, 4):
            self.add(2, t, 20)
            self.add(3, t, 30)
        for t, leader in ((0, 2), (1, 3), (3, 3), (4, 2)):
            self.add(1, t, 0, leader=leader, spacing=10)
        pairs = self.run_comp([1])[0].pairs
        self.assertEqual([p.leader_id for p in pairs], [2, 3, 3, 2])
        self.assertEqual([p.segment_id for p in pairs], [1, 2, 3, 4])

    def test_role_flips_are_event_scoped_and_not_leader_changes(self):
        for t in (0, 1, 2):
            self.add(1, t, 0 if t != 1 else 20)
            self.add(2, t, 20 if t != 1 else 0)
        self.add(1, 1, 0, event=2)
        self.add(2, 1, 20, event=2)
        self.assertEqual(self.run_comp()[0].id_flips, 2)
        self.assertEqual(self.run_comp([1])[0].id_flips, 0)

    def test_missing_speed_does_not_bridge_integral(self):
        for t in range(4):
            self.add(1, t, 0, speed=None if t == 1 else 10, leader=2, spacing=10-t)
            self.add(2, t, 10)
        pair, = self.run_comp([1])[0].pairs
        self.assertEqual(pair.valid_bias_count, 1)
        self.assertEqual(pair.minimum_spacing, 7)

    def test_no_speed_column_still_reports_spacing(self):
        self.conn.execute('ALTER TABLE events DROP COLUMN veh_speed')
        self.conn.execute('INSERT INTO events VALUES (1, 1, 0, 1, 0, 2, 3)')
        pair, = self.run_comp()[0].pairs
        self.assertEqual(pair.minimum_spacing, 3)
        self.assertTrue(math.isnan(pair.mean_bias))

    def test_empty_selection_and_empty_event(self):
        self.add(1, 0, 0)
        self.assertEqual(self.run_comp([])[0].pairs, [])
        summary = summarize(self.run_comp())
        self.assertEqual(summary['number_of_vehicle_pairs'], 0)
        self.assertTrue(math.isnan(summary['percentage_of_vehicle_pairs_with_unphysical_spacing']))

    def test_duplicate_records_rejected(self):
        self.add(1, 0, 0)
        self.add(1, 0, 1)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.run_comp()

    def test_invalid_options(self):
        for options in ({'leader_search_distance_threshold': 0},
                        {'unphysical_spacing_threshold': -1},
                        {'leader_search_distance_threshold': float('nan')}):
            with self.assertRaises(ValueError):
                self.run_comp(**options)

    def test_veh_spacing_alias(self):
        self.conn.execute('ALTER TABLE events RENAME COLUMN spacing TO veh_spacing')
        self.add(1, 0, 0, leader=2, spacing=4)
        pair, = self.run_comp([1])[0].pairs
        self.assertEqual(pair.minimum_spacing, 4)


if __name__ == '__main__':
    unittest.main()

# Platoon Consistency

Removed trajectories and synthetic placeholder rows are excluded from both
followers and leader candidates; actual gap fills and lane corrections remain.

Implements Module 3 of `NoiseAnalysisReport_Draft_v2.3.docx`.

## Run the analysis

- **Analysis → Noise → Platoon Consistency** analyzes all followers in the displayed event.
- **Right-click → Noise Analysis → Platoon Consistency** analyzes the target trajectories as followers. Leader candidates still include every vehicle in the event.
- **File → Export → Export Analysis → Platoon Consistency** exports the selected events. The results dialog also has a **Save CSV…** button.

The parameter dialog defaults to a **5 m unphysical spacing threshold** and a **50 m leader search distance**. A segment is unphysical if any spacing is strictly below the threshold.

The results show selection-wide ID-role flips, the number of pair segments with unphysical spacing, and their percentage. Each table row describes one continuous leader–follower segment and reports minimum spacing, minimum/maximum/mean bias, RMSE bias and RMSPE bias. Segment IDs, start/end times, sample counts and valid bias counts distinguish repeated pairings and expose missing-data coverage. CSV contains the same selection summary followed by a segment table; unavailable table metrics are empty cells.

## Calculation conventions

Input units are seconds, metres and metres per second. The importer recognizes `spacing` and `veh_spacing` as lengths for configured unit conversion.

1. Use `preceding_veh_id` and finite `spacing` (or `veh_spacing`) together when supplied. These assignments are not limited by the fallback search radius. Self-assignments trigger fallback search.
2. Otherwise, find the nearest strictly forward vehicle at the same event, exact timestamp and lane, within the search distance. Position is `veh_dist_trav`. Ties use the lowest vehicle ID. No time interpolation is performed.
3. End a pair segment when the leader changes, an observed follower row has no valid assignment, or the follower misses a timestamp present elsewhere in the event. Actual timestamp differences are used for integration. Gaps absent from the entire event cannot be identified without an externally supplied sampling interval, which the report does not specify.
4. Starting from the segment's observed initial spacing, integrate leader speed minus follower speed using the trapezoidal rule. Bias is this reconstructed spacing minus observed spacing. RMSPE is the root mean squared `bias / observed_spacing`, excluding zero observed spacing. Following the report's formula, it is a **ratio**, not multiplied by 100.
5. Missing leader records or nonfinite speeds leave spacing metrics available. Bias is calculated only over the valid prefix of the segment; it is not integrated across missing speeds. A segment with no valid initial speeds has unavailable bias metrics.
6. Count ID flips when the follower and leader exchange roles in chronological observations of the same unordered pair, within the selected followers and event. An ordinary change to a different leader is not a role flip. Simultaneous reciprocal assignments are ambiguous and do not count as temporal flips.

Duplicate vehicle/timestamp records produce an explicit error rather than an arbitrary match. With no pairs, the unphysical-pair count is zero and its percentage is unavailable. Analysis does not modify the database.

## Validation

From the repository root:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m unittest discover \
  -s tests -t . -p 'test_platoon*.py' -v
```

Tests cover nearest leaders and follower selection, supplied spacing and aliases, search boundaries, segment changes and gaps, event-scoped role flips, trapezoidal integration with irregular timestamps, missing speed, zero spacing, empty results, duplicates, parameter validation, dialog values, menu routes, CSV export and database preservation.

# Spectral Analysis (Module 5)

Open an event, then choose **Analysis → Spectral Analysis**. Select `veh_speed`
(default) or `veh_accel`, then click **OK**. Computation runs in the background
for every vehicle in the current event, independently of the plot selection.
The result window provides a trajectory dropdown, Previous/Next buttons, and
**Save Plot…** to save the displayed spectrum as SVG. There is no right-click
spectral action.

Results include **Magnitude Spectrum** and **Spectrum Table (all bins)** tabs.
The trajectory dropdown and Previous/Next buttons update both views. The table
shows Event ID, Vehicle ID, Frequency Index, Frequency (Hz), Fourier Coefficient,
and Magnitude, including all N bins rather than only the plotted half-spectrum.
It reads directly from the computed arrays so large spectra do not require one
widget per cell. **Save Table…** saves all bins for the currently selected
vehicle to CSV. Use Export Analysis to save tables for selected events.
The plot uses a blue filled spectrum and axes starting at zero, following the
general style of Figure 13. Its numerical scale follows the report's raw DFT
equations; the sample figure's 0–0.1 magnitude range is not a prescribed limit
or normalization rule.

For CSV output, choose **File → Export → Export Analysis → Spectral Analysis**,
select events, select the signal, and save. The CSV contains `event_id`,
`veh_id`, `frequency_index`, `frequency`, `fourier_coefficient`, and `magnitude`.
Complex coefficients use Python's `a+bj` notation. All frequency bins are
exported. Export is computed independently and never modifies the events table.
The selected signal is specified in the parameter dialog; the six-column CSV
schema follows the report and does not include a signal column.

## Calculation conventions

The implementation follows Module 5 of NoiseAnalysisReport_Draft_v2.3:

- Group by event and vehicle and sort by time. Omit missing/nonfinite signal
  values and nonfinite timestamps. Skip trajectories with fewer than two samples
  or no valid positive sampling interval.
- Reuse `time_diff` when it exists and is finite for all retained observations
  after the first (the initial value may be NULL). Otherwise calculate differences
  between retained timestamps. Average only finite, positive differences.
- Compute the full unnormalized DFT with NumPy FFT. Bin `f` has frequency
  `f / (N * time_step)` Hz and magnitude `abs(X[f])`.
- Retain all N bins in memory as arrays on `SpectralResult` objects (the
  background table representation). Plot indices 0 through floor(N/2), inclusive.
  No mean removal, windowing, magnitude normalization, or doubling is applied.

As prescribed by the report, irregular samples and gaps use a mean time
increment without interpolation. Interpret such frequency axes as approximate.
Only available speed/acceleration columns appear in the signal selector; if
neither exists, running the analysis is disabled. Trajectories without sufficient
valid data are omitted, and an empty result displays an explanatory message.

## Validation

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest tests.test_spectral -v
```

Tests cover an analytic sinusoid and direct DFT comparison, event/vehicle
isolation, acceleration selection, missing values and invalid intervals,
precomputed time differences, nonmutation of source data, CSV serialization,
plot navigation, SVG generation, empty results, multi-event worker export,
and main-window invocation handlers.

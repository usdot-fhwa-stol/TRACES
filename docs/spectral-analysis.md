# Spectral Analysis (Module 5)

Open an event, then choose **Analysis → Noise → Spectral Analysis**. Select `veh_speed`
(default) or `veh_accel`, then click **OK**. Computation runs in the background
for every vehicle in the current event, independently of the plot selection.
The result window provides a trajectory dropdown, Previous/Next buttons, and
**Save Plot…** to save the displayed spectrum as SVG. There is no right-click
spectral action.

Results include a spectrum plot and **Spectrum Table (all bins)** tabs.
Use the **Spectrum** selector to choose **Magnitude** (the default),
**Amplitude**, or **Power Spectrum**. The plot tab,
y-axis label, scale, and saved SVG reflect the selection. The choice is retained
when navigating between trajectories.
The trajectory dropdown and Previous/Next buttons update both views. The table
shows Event ID, Vehicle ID, Frequency Index, Frequency (Hz), Fourier Coefficient,
and Magnitude, including all N bins rather than only the plotted half-spectrum.
It reads directly from the computed arrays so large spectra do not require one
widget per cell. **Save Table…** saves all bins for the currently selected
vehicle to CSV. Use Export Analysis to save tables for selected events.
The plot uses a blue filled spectrum and axes starting at zero, following the
general style of Figure 13. The default magnitude scale follows the report's raw
DFT equations; the sample figure's 0–0.1 magnitude range is not a prescribed
limit or normalization rule. The table and CSV exports always contain the raw
DFT coefficients and magnitudes, independently of the plot selection.

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
- Exclude removed trajectories and synthetic placeholder rows using the shared
  analysis filters; retain actual gap fills and lane corrections.
- Reuse `time_diff` when it exists and is finite for all retained observations
  after the first (the initial value may be NULL). Otherwise calculate differences
  between retained timestamps. Average only finite, positive differences.
- Compute the full unnormalized DFT with NumPy FFT. Bin `f` has frequency
  `f / (N * time_step)` Hz and magnitude `abs(X[f])`.
- Retain all N bins in memory as arrays on `SpectralResult` objects (the
  background table representation). Plot indices 0 through floor(N/2), inclusive.
  No mean removal or windowing is applied. Raw magnitude is not normalized or
  doubled.

For the amplitude and power plot options, let `N` be the number of retained
samples and `X[k]` the unnormalized DFT coefficient:

| Plot | Ordinary positive-frequency bins | DC and Nyquist (when present) |
| --- | --- | --- |
| Magnitude | `abs(X[k])` | `abs(X[k])` |
| Amplitude | `2 * abs(X[k]) / N` | `abs(X[k]) / N` |
| Power Spectrum | `2 * (abs(X[k]) / N)**2` | `(abs(X[k]) / N)**2` |

The Nyquist bin exists only for even `N`. For odd `N`, the highest plotted bin
is an ordinary positive-frequency bin and is doubled for amplitude and power.
Amplitude gives peak sinusoidal amplitude for a component aligned with a DFT
bin, in m/s for speed or m/s² for acceleration. Power Spectrum gives mean-square
contribution per bin in squared signal units; summing the bins equals the mean
square of the retained signal. It is not power spectral density (PSD), which is
expressed per Hz. The mean remains at 0 Hz, contributing its squared value to
total power. Components between DFT frequencies can spread across multiple bins.

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

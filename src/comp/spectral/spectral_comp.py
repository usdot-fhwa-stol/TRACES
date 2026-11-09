"""Module 5: raw DFT spectra using the report's mean sampling interval."""
import csv
from dataclasses import dataclass
from itertools import groupby

import numpy as np

from comp.base_comp import BaseComp


@dataclass
class SpectralResult:
    event_id: int
    veh_id: int
    selected_signal: str
    time_step: float
    frequency_index: np.ndarray
    frequency: np.ndarray
    fourier_coefficient: np.ndarray
    magnitude: np.ndarray

    def one_sided_values(self, spectrum_type='magnitude'):
        """Return raw magnitude, peak amplitude, or mean-square power per bin.

        Real signals share each interior bin's contribution with its negative
        frequency partner. DC and the even-length Nyquist bin have no partner.
        """
        n = len(self.magnitude)
        values = self.magnitude[:n // 2 + 1].copy()
        if spectrum_type == 'magnitude':
            return values
        values /= n
        if spectrum_type == 'power':
            values **= 2
        elif spectrum_type != 'amplitude':
            raise ValueError(f'Unknown spectrum type: {spectrum_type}')
        values[1:-1 if n % 2 == 0 else None] *= 2
        return values


class SpectralComp(BaseComp):
    OPTIONS_SCHEMA = {'selected_signal': 'veh_speed'}

    def comp_series(self, db_connection, event_id, veh_ids, options=None):
        signal = self._resolve_options(options)['selected_signal']
        if signal not in ('veh_speed', 'veh_accel'):
            raise ValueError('Selected signal must be veh_speed or veh_accel.')
        columns = self._events_columns(db_connection)
        if signal not in columns:
            raise ValueError(f'The dataset does not contain {signal}.')
        selected = None if veh_ids is None else set(veh_ids)
        has_dt = 'time_diff' in columns
        dt_column = ', time_diff' if has_dt else ''
        cursor = db_connection.execute(
            f'SELECT veh_id, veh_time, {signal}{dt_column} FROM events '
            'WHERE event_id = ? ORDER BY veh_id, veh_time', (event_id,))
        results = []
        for veh_id, rows in groupby(cursor, key=lambda row: row[0]):
            if selected is not None and veh_id not in selected:
                continue
            data = np.asarray([row[1:] for row in rows], dtype=float)
            # Invalid timestamps cannot define sampling; nonfinite signals cannot
            # contribute a finite Fourier coefficient.
            data = data[np.isfinite(data[:, 0]) & np.isfinite(data[:, 1])]
            n = len(data)
            if n < 2:
                continue
            # The first difference is normally NULL. Reuse supplied differences
            # only when every subsequent retained observation has one.
            if has_dt and np.all(np.isfinite(data[1:, 2])):
                dt = data[:, 2]
            else:
                dt = np.diff(data[:, 0])
            dt = dt[np.isfinite(dt) & (dt > 0)]
            if not len(dt):
                continue
            time_step = float(np.mean(dt))
            coefficients = np.fft.fft(data[:, 1])
            indices = np.arange(n)
            results.append(SpectralResult(
                event_id, veh_id, signal, time_step, indices,
                indices / (n * time_step), coefficients, np.abs(coefficients)))
        return results


def write_csv(results, dest):
    """All N bins, including complex coefficients, in the report's table format."""
    with open(dest, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['event_id', 'veh_id', 'frequency_index', 'frequency',
                         'fourier_coefficient', 'magnitude'])
        for result in results:
            for index, frequency, coefficient, magnitude in zip(
                    result.frequency_index, result.frequency,
                    result.fourier_coefficient, result.magnitude):
                writer.writerow([result.event_id, result.veh_id, index,
                                 frequency, coefficient, magnitude])

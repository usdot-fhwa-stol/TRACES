"""Units conversion module for the data_import package.

System standard units:
    Time        : s       (seconds)
    Length      : m       (meters)
    Speed       : m/s     (meters per second)
    Acceleration: m/s^2   (meters per second squared)
    Unitless    : ""      (integer identifiers, text columns)
"""


class Units:
    """Provides unit conversion for time, length, speed, and acceleration values.

    Only multiplication-based conversions are needed for this application, so
    every conversion factor is a single float multiplier:
        destination_value = source_value * factor

    Usage
    -----
    units = Units()
    meters = units.convert("ft", "m", 100.0)   # 30.48
    mps    = units.convert("mph", "m/s", 60.0) # 26.8224
    same   = units.convert("m", "m", 5.0)      # 5.0  (identity)
    """

    # System standard (destination) units for each database column.
    # Columns typed as "int" and text columns have no units ("").
    SYSTEM_UNITS: dict[str, str] = {
        "event_id"          : "",
        "veh_id"            : "",
        "veh_time"          : "s",
        "lane_id"           : "",
        "orig_lane_id"      : "",
        "x_map_loc"         : "m",
        "y_map_loc"         : "m",
        "x_frenet_loc"      : "m",
        "y_frenet_loc"      : "m",
        "x_map_origin"      : "m",
        "y_map_origin"      : "m",
        "veh_lat"           : "",      # degrees – not converted by Units
        "veh_lon"           : "",      # degrees – not converted by Units
        "veh_speed"         : "m/s",
        "veh_accel"         : "m/s^2",
        "veh_length"        : "m",
        "veh_width"         : "m",
        "veh_automation"    : "",
        "osm_way_id"        : "",
        "osm_speed_limit"   : "",      # stored as-is (integer code)
        "osm_traffic_control": "",
        "preceding_veh_id"  : "",
        "veh_dist_trav"     : "m",
        "event_name"        : "",
    }

    # Conversion table: _FACTORS[src_units][dst_units] = multiplier
    # Every pair that the application may encounter is listed explicitly.
    # Identity rows (src == dst) are required so callers never need a
    # special-case branch.
    _FACTORS: dict[str, dict[str, float]] = {
        # ── time ──────────────────────────────────────────────────────────────
        "s": {
            "s": 1.0,
        },
        # ── length ────────────────────────────────────────────────────────────
        "m": {
            "m"  : 1.0,
            "ft" : 3.280_839_895,          # 1 m  → ft
        },
        "ft": {
            "ft" : 1.0,
            "m"  : 0.304_8,                # 1 ft → m  (exact by definition)
        },
        # ── speed ─────────────────────────────────────────────────────────────
        "m/s": {
            "m/s" : 1.0,
            "mph" : 2.236_936_292,         # 1 m/s → mph
            "kph" : 3.6,                   # 1 m/s → kph
        },
        "mph": {
            "mph" : 1.0,
            "m/s" : 0.447_04,             # 1 mph → m/s  (exact: 1609.344/3600)
            "kph" : 1.609_344,            # 1 mph → kph  (exact)
        },
        "kph": {
            "kph" : 1.0,
            "m/s" : 1.0 / 3.6,           # 1 kph → m/s
            "mph" : 1.0 / 1.609_344,      # 1 kph → mph
        },
        # ── acceleration ──────────────────────────────────────────────────────
        "m/s^2": {
            "m/s^2"  : 1.0,
            "ft/s^2" : 3.280_839_895,     # 1 m/s² → ft/s²
        },
        "ft/s^2": {
            "ft/s^2" : 1.0,
            "m/s^2"  : 0.304_8,          # 1 ft/s² → m/s²
        },
        # ── unitless / text (no conversion applied) ───────────────────────────
        "": {
            "": 1.0,
        },
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def convert(self, src_units: str, dst_units: str, value: float) -> float:
        """Convert *value* from *src_units* to *dst_units*.

        Parameters
        ----------
        src_units : str
            Source unit string, e.g. ``"mph"``, ``"ft"``, ``"m/s^2"``.
            Use an empty string ``""`` for unitless columns.
        dst_units : str
            Destination unit string.  Typically taken from
            ``Units.SYSTEM_UNITS[column_name]``.
        value : float
            Numeric value expressed in *src_units*.

        Returns
        -------
        float
            Value expressed in *dst_units*.

        Raises
        ------
        KeyError
            If *src_units* or the *src_units* → *dst_units* pair is not
            present in the conversion table.  The caller (``_check_configuration``
            in ``csv_process.py``) should catch this and emit an error message.
        """
        if dst_units == src_units:
            return value; # early out check avoids lookup and multiply

        return value * self._FACTORS[src_units][dst_units]

    def factor(self, src_units: str, dst_units: str) -> float:
        """Return the conversion multiplier without applying it to a value.

        Used during configuration validation to confirm the src→dst path
        exists before any data rows are processed.

        Raises
        ------
        KeyError
            Same conditions as :meth:`convert`.
        """
        return self._FACTORS[src_units][dst_units]

    def is_known(self, units_str: str) -> bool:
        """Return ``True`` if *units_str* appears in the conversion table."""
        return units_str in self._FACTORS

    def system_units(self, column_name: str) -> str:
        """Return the system (destination) units for a database column.

        Returns an empty string for unitless or unrecognised columns so that
        ``convert("", "", value)`` is always a safe no-op.
        """
        return self.SYSTEM_UNITS.get(column_name, "")
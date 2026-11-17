"""Import functions for CSV column processing.

Each public method of CsvAlgorithms is auto-discovered by CsvProcess and
becomes available as a ``func`` name in JSON import configurations. All
functions share the signature ``func(row, col_def)`` where ``row`` is the
CSV row (list of strings) and ``col_def`` is the column's configuration
dictionary.

Source-column declaration (the src_col key is deprecated):
    Source CSV columns are declared exclusively through ``parms``. Which
    parms entries are column indices varies by function, so every function
    publishes its own extractor via the @source_cols decorator. CsvProcess
    uses the extractor at configuration-check time to compute the minimum
    row width and to bounds-check every declared index — a new function
    added here carries its own metadata and needs no changes elsewhere.

State:
    The ``group`` function accumulates a group-key dictionary across an
    import run. CsvProcess instances are created fresh for each import
    (never reused), so this state never leaks between runs.
"""

import utm
from typing import Any, Callable, Dict, List


def source_cols(extractor: Callable[[List[Any]], List[Any]]):
    """Attach source-column metadata to an import function.

    Args:
        extractor: Callable mapping the function's ``parms`` list to the
            subset of entries that are CSV column indices. May raise
            IndexError when parms has too few entries for the function —
            CsvProcess reports that as a configuration error.
    """
    def wrap(func):
        func._source_cols = extractor
        return func
    return wrap


def _convert(value: Any, col_type: str) -> Any:
    """Convert a raw value to the configured storage type.

    Returns None if the value cannot be converted.
    """
    try:
        if col_type == "int":
            return int(float(value))  # Handle float strings like "3.0"
        if col_type == "float":
            return float(value)
        if col_type == "text":
            return str(value)
        return value
    except (ValueError, TypeError):
        return None


class CsvAlgorithms:
    """Utility class for processing CSV column data based on JSON
    configuration.

    Provides functions to interpret and transform column values for import.
    """

    def __init__(self):
        """Initialize the group dictionary for the group function."""
        self._group_dict: Dict[str, int] = {}

    @source_cols(lambda parms: [parms[0]])
    def copy(self, row: List[str], col_def: Dict[str, Any]) -> Any:
        """Copy a value from a CSV column, with optional default.

        parms[0] (required): source CSV column index.
        parms[1] (optional): default value used when the source cell is
            empty or out of range. The default is converted per ``type``
            like any other value.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms and type.

        Returns:
            Any: Converted column value, the converted default, or None.
        """
        parms = col_def.get("parms", [])
        col_type = col_def.get("type", "float")
        src = parms[0]

        if src >= len(row) or not row[src]:
            if len(parms) > 1:
                return _convert(parms[1], col_type)
            return None


        return _convert(row[src], col_type)

    @source_cols(lambda parms: [])
    def const(self, row: List[str], col_def: Dict[str, Any]) -> Any:
        """Store a constant value for every row.

        parms[0] (required): the constant value, converted per ``type``.
        Reads nothing from the CSV row.

        Args:
            row (List[str]): CSV row data (unused).
            col_def (Dict[str, Any]): Column definition with parms and type.

        Returns:
            Any: The converted constant, or None if parms is empty or the
                value cannot be converted.
        """
        parms = col_def.get("parms", [])
        if not parms:
            return None
        return _convert(parms[0], col_def.get("type", "float"))

    @staticmethod
    @source_cols(lambda parms: [parms[0], parms[1], parms[2], parms[3]])
    def _utm_common(row: List[str], col_def: Dict[str, Any]) -> tuple:
        """Convert UTM coordinates to latitude and longitude.

        Shared implementation for utm_lat and utm_lon. Private so it is
        not auto-discovered as a configurable import function.

        parms[0]: column index of map origin X (longitude, decimal degrees).
        parms[1]: column index of map origin Y (latitude, decimal degrees).
        parms[2]: column index of vehicle X position (easting offset, m).
        parms[3]: column index of vehicle Y position (northing offset, m).

        Returns:
            tuple: (latitude, longitude) or (None, None) if invalid.
        """
        parms = col_def.get("parms", [])
        if len(parms) < 4:
            return None, None

        # Validate all indices are within bounds
        if any(p < 0 or p >= len(row) for p in parms[:4]):
            return None, None

        try:
            map_origin_x = float(row[parms[0]])
            map_origin_y = float(row[parms[1]])
            x_map_loc = float(row[parms[2]])
            y_map_loc = float(row[parms[3]])

            easting, northing, zone_num, zone_letter = utm.from_latlon(map_origin_y, map_origin_x)
            lat, lon = utm.to_latlon(easting + x_map_loc, northing + y_map_loc, zone_num, zone_letter)
            return lat, lon
        except (ValueError, TypeError):
            return None, None

    @source_cols(lambda parms: [parms[0], parms[1], parms[2], parms[3]])
    def utm_lat(self, row: List[str], col_def: Dict[str, Any]) -> float:
        """Get latitude from UTM coordinates.

        parms[0..3]: see _utm_common.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            float: Latitude value or None if invalid.
        """
        lat, _ = self._utm_common(row, col_def)
        return lat

    @source_cols(lambda parms: [parms[0], parms[1], parms[2], parms[3]])
    def utm_lon(self, row: List[str], col_def: Dict[str, Any]) -> float:
        """Get longitude from UTM coordinates.

        parms[0..3]: see _utm_common.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            float: Longitude value or None if invalid.
        """
        _, lon = self._utm_common(row, col_def)
        return lon

    @source_cols(lambda parms: [parms[0]])
    def lane_tgsim_L1L2(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Calculate lane ID based on TGSIM L1 and L2 rules.

        parms[0] (required): source CSV column index of the raw lane code.
        Negative lane codes are returned as their absolute value.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            int: Calculated lane ID or None if invalid.
        """
        parms = col_def.get("parms", [])
        if len(parms) < 1 or parms[0] < 0 or parms[0] >= len(row) or not row[parms[0]]:
            return None
        try:
            lane_kf = int(float(row[parms[0]]))
            return -lane_kf if lane_kf < 0 else lane_kf
        except (ValueError, TypeError):
            return None

    @source_cols(lambda parms: [parms[0]])
    def lane_tgsim_stationary(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Calculate lane ID based on TGSIM stationary rules.

        parms[0] (required): source CSV column index of the raw lane code.
        Lane codes greater than 9 (secondary roadway) have 9 subtracted.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            int: Calculated lane ID or None if invalid.
        """
        parms = col_def.get("parms", [])
        if len(parms) < 1 or parms[0] < 0 or parms[0] >= len(row) or not row[parms[0]]:
            return None
        try:
            lane_kf = int(float(row[parms[0]]))
            return lane_kf - 9 if lane_kf > 9 else lane_kf
        except (ValueError, TypeError):
            return None

    @source_cols(lambda parms: list(parms))
    def group(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Generate a sequential group index from concatenated column values.

        parms[0..n] (at least one): CSV column indices whose values are
        concatenated to form the group key. The first unique key seen is
        assigned index 1, the second 2, and so on.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            int: Group index or None if invalid.
        """
        parms = col_def.get("parms", [])
        try:
            key = " ".join(str(row[i]) for i in parms if i < len(row) and row[i])
            if not key:  # Return None if key is empty
                return None
            if key not in self._group_dict:
                self._group_dict[key] = len(self._group_dict) + 1
            return self._group_dict[key]
        except (IndexError, TypeError):
            return 0

    @source_cols(lambda parms: parms[:-1] if parms and isinstance(parms[-1], str)
                 else list(parms))
    def event_name(self, row: List[str], col_def: Dict[str, Any]) -> str:
        """Build an event name from concatenated column values and an
        optional label.

        parms[0..n-1] (at least one): CSV column indices whose values are
            concatenated to form the name.
        parms[last] (optional): if a string, used as a text prefix label.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            str: Event name or None.
        """
        parms = col_def.get("parms", [])
        if len(parms) == 0:
            return None

        label = ""  # check for last element label
        if isinstance(parms[-1], str):
            label = parms[-1]
            parms = parms[:-1]

        try:
            key = " ".join(str(row[i]) for i in parms if i < len(row) and row[i])
            if not key:  # Return None if key is empty
                return None
            return label + key
        except (IndexError, TypeError):
            return None

    @source_cols(lambda parms: [parms[0]])
    def tgsim_automated(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Check if the source column value equals 'yes' (case-insensitive).

        parms[0] (required): source CSV column index of the automation
        status string.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            int: 1 if the source column value is 'yes' (case-insensitive),
                0 otherwise.
        """
        parms = col_def.get("parms", [])
        if len(parms) < 1 or parms[0] < 0 or parms[0] >= len(row) or not row[parms[0]]:
            return 0

        return 1 if row[parms[0]].lower() == "yes" else 0
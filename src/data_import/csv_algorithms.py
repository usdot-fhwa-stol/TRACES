import utm
from typing import Any, List, Dict

class CsvAlgorithms:
    """Utility class for processing CSV column data based on JSON configuration.

    Provides functions to interpret and transform column values for import.
    """

    def __init__(self):
        """Initialize the group dictionary for group function."""
        self._group_dict: Dict[str, int] = {}

    def copy(self, row: List[str], col_def: Dict[str, Any]) -> Any:
        """Copy a value from the row or return default from parms.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with src_col, type, and parms.

        Returns:
            Any: Processed column value or None if invalid.
        """
        src_col = col_def.get("src_col", -1)
        col_type = col_def.get("type", "float")
        parms = col_def.get("parms", [])

        if src_col < 0 or src_col >= len(row) or not row[src_col]:
            return parms[0] if parms else None
        
        try:
            value = row[src_col]
            if col_type == "int":
                return int(float(value))  # Handle float strings
            elif col_type == "float":
                return float(value)
            return value
        except (ValueError, TypeError):
            return None

    @staticmethod
    def utm_common(row: List[str], col_def: Dict[str, Any]) -> tuple:
        """Convert UTM coordinates to latitude and longitude.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms for coordinates.

        Returns:
            tuple: (latitude, longitude) or (None, None) if invalid.
        """
        parms = col_def.get("parms", [])
        if len(parms) < 4:
            return None, None

        # Validate all indices are within bounds
        if any(p < 0 or p >= len(row) for p in parms):
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

    def utm_lat(self, row: List[str], col_def: Dict[str, Any]) -> float:
        """Get latitude from UTM coordinates.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            float: Latitude value or None if invalid.
        """
        lat, _ = self.utm_common(row, col_def)
        return lat

    def utm_lon(self, row: List[str], col_def: Dict[str, Any]) -> float:
        """Get longitude from UTM coordinates.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with parms.

        Returns:
            float: Longitude value or None if invalid.
        """
        _, lon = self.utm_common(row, col_def)
        return lon

    def tgsim_lane(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Calculate lane ID based on tgsim rules.

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with src_col and parms.

        Returns:
            int: Calculated lane ID or None if invalid.
        """
        src_col = col_def.get("src_col", -1)
        parms = col_def.get("parms", [])
        
        if src_col < 0 or src_col >= len(row) or not row[src_col] or len(parms) < 1:
            return None
        
        if parms[0] < 0 or parms[0] >= len(row):
            return None
        
        try:
            lane_kf = int(float(row[parms[0]]))
            src_value = int(float(row[src_col]))
            return 7 - lane_kf if src_value % 2 else 16 - lane_kf
        except (ValueError, TypeError):
            return None

    def group(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Generate an index based on concatenated unique strings.

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

    def event_name(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Build an event name from concatenated unique strings and optional label.

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

    def tgsim_automated(self, row: List[str], col_def: Dict[str, Any]) -> int:
        """Check if the source column value equals 'yes' (case-insensitive).

        Args:
            row (List[str]): CSV row data.
            col_def (Dict[str, Any]): Column definition with src_col.

        Returns:
            int: 1 if the source column value is 'yes' (case-insensitive), 0 otherwise.
        """
        src_col = col_def.get("src_col", -1)
        
        if src_col < 0 or src_col >= len(row) or not row[src_col]:
            return 0
        
        return 1 if row[src_col].lower() == "yes" else 0
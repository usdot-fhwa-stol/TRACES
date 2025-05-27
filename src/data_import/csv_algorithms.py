import utm


class CsvAlgorithms:
    """Utility class for processing CSV column data with specific algorithms.

    Provides methods to transform or compute values for database columns based on
    CSV input records and configuration parameters.
    """

    def __init__(self):
        """Initialize the CsvAlgorithms instance.

        Sets up an internal dictionary to track group assignments for the group method.
        """
        self._group_lookup = {}

    def copy(self, row: list, col: int, parms: list) -> any:
        """Copy a value from the specified column or return a default parameter.

        Args:
            row (list): The input CSV row as a list of strings.
            col (int): Zero-based index of the column to copy from.
            parms (list): List of parameters, where the first element is used as a default
                value if the column index is invalid.

        Returns:
            any: The value from the specified column, or the first parameter if the column
                index is invalid and parameters are provided, otherwise None.

        Raises:
            IndexError: If the column index is negative and no parameters are provided.
        """
        if col >= 0 and col < len(row):
            return row[col]
        return parms[0] if parms else None

    def utm_lon(self, row: list, col: int, parms: list) -> float:
        """Compute the longitude from UTM coordinates using map origin and offsets.

        Args:
            row (list): The input CSV row as a list of strings.
            col (int): Ignored in this implementation; included for interface consistency.
            parms (list): List of four indices [map_origin_x, map_origin_y, x_map_loc, y_map_loc]
                referencing columns in the row for UTM conversion.

        Returns:
            float: The computed longitude value.

        Raises:
            IndexError: If any parameter index is out of bounds for the row.
            ValueError: If any referenced value cannot be converted to float.
            utm.error.OutOfRangeError: If UTM conversion fails due to invalid coordinates.
        """
        return self.__utm_common(
            float(row[parms[0]]), float(row[parms[1]]),
            float(row[parms[2]]), float(row[parms[3]])
        )[1]

    def utm_lat(self, row: list, col: int, parms: list) -> float:
        """Compute the latitude from UTM coordinates using map origin and offsets.

        Args:
            row (list): The input CSV row as a list of strings.
            col (int): Ignored in this implementation; included for interface consistency.
            parms (list): List of four indices [map_origin_x, map_origin_y, x_map_loc, y_map_loc]
                referencing columns in the row for UTM conversion.

        Returns:
            float: The computed latitude value.

        Raises:
            IndexError: If any parameter index is out of bounds for the row.
            ValueError: If any referenced value cannot be converted to float.
            utm.error.OutOfRangeError: If UTM conversion fails due to invalid coordinates.
        """
        return self.__utm_common(
            float(row[parms[0]]), float(row[parms[1]]),
            float(row[parms[2]]), float(row[parms[3]])
        )[0]

    def group(self, row: list, col: int, parms: list) -> int:
        """Assign a unique integer to a group based on concatenated column values.

        Args:
            row (list): The input CSV row as a list of strings.
            col (int): Ignored in this implementation; included for interface consistency.
            parms (list): List of 1-based column indices to concatenate for the group key.

        Returns:
            int: A unique integer identifier for the group.

        Raises:
            IndexError: If any parameter 0-based index is out of bounds for the row.
        """
        # Generate key from 1-based column indices
        key_parts = [row[i] for i in parms if i >= 0 and i <= len(row)]
        key = " ".join(key_parts)

        if key not in self._group_lookup:
            self._group_lookup[key] = len(self._group_lookup)
        return self._group_lookup[key]

    @staticmethod
    def __utm_common(map_origin_x: float, map_origin_y: float, x_map_loc: float, y_map_loc: float) -> tuple:
        """Convert map coordinates to latitude and longitude using UTM.

        Args:
            map_origin_x (float): Latitude of the map origin.
            map_origin_y (float): Longitude of the map origin.
            x_map_loc (float): X offset from the map origin in meters.
            y_map_loc (float): Y offset from the map origin in meters.

        Returns:
            tuple: A tuple of (latitude, longitude) in decimal degrees.

        Raises:
            utm.error.OutOfRangeError: If the coordinates are invalid for UTM conversion.
        """
        easting, northing, zone_num, zone_letter = utm.from_latlon(map_origin_x, map_origin_y)
        return utm.to_latlon(easting + x_map_loc, northing + y_map_loc, zone_num, zone_letter)
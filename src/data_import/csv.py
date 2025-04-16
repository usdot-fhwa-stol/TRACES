import utm

class Csv:
    def __init__(self):
        self._group_lookup = {}

    def copy(self, row, col, parms):
        if col >= 0 and col < len(row):
            return row[col - 1]
        return parms[0] if parms else None

    def utm_lon(self, row, col, parms):
        return Csv.__utm_common(float(row[parms[0] - 1]), float(row[parms[1] - 1]), 
            float(row[parms[2] - 1]), float(row[parms[3] - 1]))[1]

    def utm_lat(self, row, col, parms):
        return Csv.__utm_common(float(row[parms[0] - 1]), float(row[parms[1] - 1]), 
            float(row[parms[2] - 1]), float(row[parms[3] - 1]))[0]

    def group(self, row, col, parms):
        # Generate key from 1-based column indices
        key_parts = [row[i - 1] for i in parms if i - 1 < len(row)]
        key = " ".join(key_parts)

        if key not in self._group_lookup:
            self._group_lookup[key] = len(self._group_lookup)
        return self._group_lookup[key]

    @staticmethod
    def __utm_common(map_origin_x, map_origin_y, x_map_loc, y_map_loc):
        easting, northing, zone_num, zone_letter = utm.from_latlon(map_origin_x, map_origin_y)
        return utm.to_latlon(easting + x_map_loc, northing + y_map_loc, zone_num, zone_letter)

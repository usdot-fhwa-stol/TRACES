# Trajectory Research, Analysis, Correction, Exploration, and Simulation (TRACES) — JSON Configuration File User Guide

## Overview

The TRACES application imports vehicle trajectory data from CSV files into a standardized SQLite database for visualization and analysis. Each CSV file format requires a corresponding JSON configuration file that tells the application what columns to expect, how to interpret them, and what database columns to create.

The configuration file is an instruction list for the application: it defines both the structure of the database (which columns exist and their types) and the import rules (how each column is populated from the CSV file). The database schema is entirely configuration-driven — apart from a small set of required columns, users are free to define database columns for their own purposes.

Configuration files are stored in the `config` folder alongside the application. The application reads all `.json` files found in that folder at startup, validates each one, and presents the valid configurations as available import types. Configurations that fail validation are reported in the import dialog log and excluded from the file type selector. The filename (without the `.json` extension) becomes the label shown in the import dialog file type selector.

---

## Configuration File Structure

A configuration file is a JSON object at the top level. It supports the following top-level keys.

### Top-Level Keys

**`col_defs`** *(required)*

Defines the database columns to create and the rules for populating them from the CSV file. This is the only required top-level key. Its value may be either a JSON object or a JSON array of JSON objects. See the [Column Definitions](#column-definitions) section for full details.

**`file_ext`** *(optional)*

The file extension used to filter files in the import dialog. Defaults to `"csv"` if not provided.

```json
"file_ext": "csv"
```

**`class`** *(optional)*

The fully qualified Python class name of the processor used to import the file. Defaults to `"data_import.csv_process.CsvProcess"` if not provided. The class must be a subclass of `data_import.base_process.BaseProcess`. This key allows alternative import processors (for example, a future JSON file processor) to be selected per configuration.

```json
"class": "data_import.csv_process.CsvProcess"
```

### Minimal Valid Configuration

```json
{
  "col_defs": {
    "event_id":      { "func": "group", "parms": [0, 1], "type": "int" },
    "veh_id":        { "func": "copy",  "parms": [2], "type": "int" },
    "veh_time":      { "func": "copy",  "parms": [3] },
    "lane_id":       { "func": "copy",  "parms": [4], "type": "int" },
    "veh_dist_trav": { "func": "copy",  "parms": [5], "units": "ft" }
  }
}
```

---

## Column Definitions

The `col_defs` value defines one or more sets of database column instructions. Each column definition set is a JSON object whose keys become database column names and whose values are JSON objects containing the column's import instructions.

### Column Key Naming Rules

Column keys must follow these rules:

- Lowercase letters only (no digits)
- Optional underscore characters may be used as internal word separators
- Must not begin or end with an underscore
- Must not use any internally reserved column names (see [Internally Reserved Names](#internally-reserved-names) below)

Examples of valid user-defined keys: `my_column`, `siteid`, `sensor_zone`, `pass_number`

Examples of invalid keys: `MyColumn` (uppercase), `lane_2` (digit), `_private` (leading underscore), `time_diff` (internally reserved)

The key name becomes the column name in the SQLite database.

### Object vs. Array Form

**Object form** — used when each row of the CSV file represents one vehicle record. The `col_defs` value is a single JSON object.

```json
{
  "col_defs": {
    "veh_id":   { "func": "copy", "parms": [0], "type": "int" },
    "veh_time": { "func": "copy", "parms": [1] }
  }
}
```

**Array form** — used when each row of the CSV file encodes records for multiple vehicles in different column groups. The `col_defs` value is a JSON array where each element is a complete column definition object. The application processes each definition object against the same row and inserts a separate database record for each.

```json
{
  "col_defs": [
    {
      "event_id":      { "func": "const", "parms": [1], "type": "int" },
      "veh_id":        { "func": "copy",  "parms": [0], "type": "int" },
      "veh_time":      { "func": "copy",  "parms": [2] },
      "lane_id":       { "func": "copy",  "parms": [5], "type": "int" },
      "veh_dist_trav": { "func": "copy",  "parms": [6] },
      "veh_speed":     { "func": "copy",  "parms": [3] }
    },
    {
      "event_id":      { "func": "const", "parms": [1], "type": "int" },
      "veh_id":        { "func": "copy",  "parms": [1], "type": "int" },
      "veh_time":      { "func": "copy",  "parms": [2] },
      "lane_id":       { "func": "copy",  "parms": [7], "type": "int" },
      "veh_dist_trav": { "func": "copy",  "parms": [8] },
      "veh_speed":     { "func": "copy",  "parms": [4] }
    }
  ]
}
```

In this example, columns 0 and 1 hold the IDs of two different vehicles, column 2 holds a shared timestamp, and the remaining columns hold each vehicle's lane, distance, and speed. Each CSV row produces two database records.

Array-form rules:

- **Keys must be unique within each definition set** (JSON object keys are inherently unique within one object).
- **Keys may — and typically must — repeat across definition sets.** Every definition set must include all required columns, so keys such as `veh_id` and `veh_time` naturally appear in every set.
- **A key that appears in multiple sets must declare the same `type` in every set.** Conflicting types across sets are a configuration error.
- **The database schema is the union of all keys across all sets**, in the order each key is first encountered. A record produced by one set stores null in any column defined only by other sets.

### Required Columns

The following column keys must appear in every column definition set, and each must have a populating function (they may not be placeholders — see the `func` item below). The application will report an error and refuse the configuration if any are missing.

| Column Key      | Description |
|-----------------|-------------|
| `event_id`      | Numeric identifier for the data collection event or run. |
| `veh_id`        | Unique vehicle identifier within the event. |
| `veh_time`      | Timestamp of the vehicle state record, in seconds relative to the event start. |
| `lane_id`       | Application-normalized lane identifier used for plotting and analysis. |
| `veh_dist_trav` | Cumulative distance traveled along the vehicle trajectory. |

Together, `event_id`, `veh_id`, and `veh_time` form the primary key of the events table: at most one record is stored per vehicle per timestamp per event, and duplicate records in the source data are silently ignored.

---

## Column Definition Items

Each column's value is a JSON object that may contain the following items.

### `func`

The name of the import function to apply when populating the column. If omitted, the column is a **placeholder**: it is created in the database but no data is imported into it (all rows receive a null value). This is useful for reserving a column to be computed or populated by a later processing step.

```json
"my_placeholder": {}
```

When `func` is provided, it must be one of the function names listed in the [Supported Import Functions](#supported-import-functions) section. Providing `parms` without `func` is a configuration error — omit both for a placeholder, or add the intended function. Required columns may not be placeholders.

### `parms`

A JSON array of parameters passed to the import function. The meaning of each parameter depends on the function; for functions that read from the CSV row, source column indices are given as zero-based integers in `parms`. See [Supported Import Functions](#supported-import-functions) for each function's parameter specification.

The application knows which parameters of each function are source column indices, and validates at import time that every declared index exists in every CSV row.

```json
"veh_speed": { "func": "copy", "parms": [5] }
```

> **Note:** Earlier versions of the application used a `src_col` item to declare the source column. `src_col` is deprecated and now rejected with an error. Declare the source column as `parms[0]` of the appropriate function instead — for example, `{"src_col": 3}` becomes `{"func": "copy", "parms": [3]}`.

### `type`

The data type used when storing the value in the database. Supported values are `"float"` (default), `"int"`, and `"text"`.

- `"float"` — continuous numeric measurements; stored as SQLite REAL.
- `"int"` — identifier and classification columns; stored as SQLite INTEGER. Floating-point strings such as `"3.0"` are parsed as a float first and then truncated to an integer.
- `"text"` — string values such as names and labels; stored as SQLite TEXT.

```json
"veh_id": { "func": "copy", "parms": [0], "type": "int" }
```

**Forced types on recognized columns.** Columns whose names appear in the [Data Dictionary](#data-dictionary) have a fixed storage type that the application enforces regardless of the configured `type`. For example, `veh_id` is always stored as an integer and `event_name` is always stored as text. If a configuration declares a conflicting type for a recognized column, the application logs a warning and overrides it. Declaring the correct type explicitly keeps the import log clean and makes the configuration self-documenting.

### `units`

The units of the source CSV data for this column. When provided, the application automatically converts the value to the application's internal SI (meter-kilogram-second) units before storing it. If omitted, the value is stored as-is with no conversion applied.

Unit conversion is only applied to recognized column names from the Data Dictionary. For user-defined columns, the `units` key has no effect; the application logs a warning and stores values without conversion.

```json
"veh_speed": { "func": "copy", "parms": [5], "units": "mph" }
```

See [Supported Unit Conversions](#supported-unit-conversions) for the recognized unit strings and their corresponding application units.

---

## Supported Unit Conversions

The application stores all physical quantities in SI (MKS) units internally. When source data uses different units, specify the source unit string in the `units` key and the application will convert automatically.

| Source Units (in CSV) | Application Units (stored in database) |
|-----------------------|----------------------------------------|
| `s`                   | `s` — seconds (identity)              |
| `m`                   | `m` — meters (identity)               |
| `ft`                  | `m` — meters                          |
| `m/s`                 | `m/s` — meters per second (identity)  |
| `mph`                 | `m/s` — meters per second             |
| `kph`                 | `m/s` — meters per second             |
| `m/s^2`               | `m/s^2` — meters per second squared (identity) |
| `ft/s^2`              | `m/s^2` — meters per second squared   |

Latitude, longitude, and unitless identifier columns (such as `veh_id`, `lane_id`, `event_id`) are stored without any unit conversion regardless of the `units` key.

---

## Supported Import Functions

The application provides the following built-in functions for use in the `func` key.

---

### `copy`

Copies a value from a CSV column into the database column, with type conversion per the column's `type`. If the source cell is empty, a default value is used if provided; otherwise null is stored.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0 | integer | yes | Zero-based index of the source CSV column. |
| 1 | any | no | Default value to store when the source cell is empty. Converted per `type` like any other value. |

**Example** — copy column index 5, no default:
```json
"veh_speed": { "func": "copy", "parms": [5], "units": "mph" }
```

**Example** — copy column index 7, storing 0 when the cell is empty:
```json
"sensor_flag": { "func": "copy", "parms": [7, 0], "type": "int" }
```

To store the same constant for every row regardless of the CSV content, use `const` instead.

---

### `const`

Stores a constant value for every row. Reads nothing from the CSV file. The value is converted per the column's `type`.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0 | any | yes | The constant value stored for every row. |

**Example** — mark every vehicle in the dataset as automated:
```json
"veh_automation": { "func": "const", "parms": [1], "type": "int" }
```

---

### `group`

Generates a sequential integer group index based on a combination of CSV column values. The first unique combination encountered is assigned index 1, the second is assigned index 2, and so on. This is useful for deriving an `event_id` when events are distinguished by a combination of fields in the CSV data.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0…n | integer | at least one | Zero-based indices of the CSV columns whose values are concatenated to form the group key. |

**Example** — generate an event ID from the combination of values in columns 37, 38, 41, 42, and 43:
```json
"event_id": { "func": "group", "parms": [37, 38, 41, 42, 43], "type": "int" }
```

---

### `event_name`

Builds a human-readable event name by concatenating values from one or more CSV columns, with an optional text label prefix. This produces a descriptive string label for the event shown in the application's event selector.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0…n-1 | integer | at least one | Zero-based indices of the CSV columns whose values are concatenated to form the name. |
| last | string | no | Optional text prefix label prepended to the concatenated value. |

**Example** — build the event name from columns 42 and 43 with a "run index: " prefix:
```json
"event_name": { "func": "event_name", "parms": [42, 43, "run index: "], "type": "text" }
```

---

### `utm_lat`

Computes the geographic latitude of a vehicle from UTM (Universal Transverse Mercator) map coordinates. The function derives latitude by offsetting the map origin coordinates with the vehicle's local map position.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0 | integer | yes | CSV column index of the map origin X coordinate (longitude of origin, in decimal degrees). |
| 1 | integer | yes | CSV column index of the map origin Y coordinate (latitude of origin, in decimal degrees). |
| 2 | integer | yes | CSV column index of the vehicle X position (easting offset from origin, in meters). |
| 3 | integer | yes | CSV column index of the vehicle Y position (northing offset from origin, in meters). |

**Example:**
```json
"veh_lat": { "func": "utm_lat", "parms": [25, 26, 5, 6] }
```

---

### `utm_lon`

Computes the geographic longitude of a vehicle from UTM map coordinates. Uses the same parameter convention as `utm_lat`.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0 | integer | yes | CSV column index of the map origin X coordinate (longitude of origin, in decimal degrees). |
| 1 | integer | yes | CSV column index of the map origin Y coordinate (latitude of origin, in decimal degrees). |
| 2 | integer | yes | CSV column index of the vehicle X position (easting offset from origin, in meters). |
| 3 | integer | yes | CSV column index of the vehicle Y position (northing offset from origin, in meters). |

**Example:**
```json
"veh_lon": { "func": "utm_lon", "parms": [25, 26, 5, 6] }
```

---

### `lane_tgsim_L1L2`

Computes a normalized lane identifier for TGSIM (Telematics and GPS Simulation) datasets collected in the L1 and L2 configurations. The source lane codes used by TGSIM include negative values to indicate certain lane types; this function returns the absolute value of the lane code so that all lane identifiers are positive integers in the application database.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0 | integer | yes | Zero-based index of the source CSV column containing the raw TGSIM lane code. |

**Example:**
```json
"lane_id": { "func": "lane_tgsim_L1L2", "parms": [4], "type": "int" }
```

---

### `lane_tgsim_stationary`

Computes a normalized lane identifier for TGSIM datasets collected at a stationary sensor location. In this configuration, lane codes greater than 9 indicate a secondary roadway; this function subtracts 9 from those codes while leaving values of 9 or below unchanged.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0 | integer | yes | Zero-based index of the source CSV column containing the raw TGSIM stationary lane code. |

**Example:**
```json
"lane_id": { "func": "lane_tgsim_stationary", "parms": [4], "type": "int" }
```

---

### `tgsim_automated`

Determines whether a vehicle is automated based on a CSV column that contains the text value `"yes"` or `"no"`. Returns an integer value of 1 if the column value is `"yes"` (case-insensitive) and 0 otherwise.

**Parameters:**

| Position | Type | Required | Description |
|----------|------|----------|-------------|
| 0 | integer | yes | Zero-based index of the source CSV column containing the automation status string. |

**Example:**
```json
"veh_automation": { "func": "tgsim_automated", "parms": [10], "type": "int" }
```

---

## Data Dictionary

The following column names are recognized by the application. Some are required in every configuration (see [Required Columns](#required-columns)); the others are optional but, when configured, receive automatic unit conversion and a forced storage type. Application features that depend on optional columns are only available when those columns are present — for example, the original-lane tooltip requires `orig_lane_id`, speed and acceleration plots require `veh_speed` and `veh_accel`, and the event selector falls back to a generated label when `event_name` is absent.

User-defined column keys must not use any of these names unless they are intentionally mapping data into that database field.

| Variable Name | Type | Example | Description |
|---------------|------|---------|-------------|
| `event_id` | int | 1 | **Required.** Identifier for a data collection event, run, or file segment. Used to distinguish records belonging to different trajectory datasets or collection periods. |
| `veh_id` | int | 57 | **Required.** Unique identifier assigned to each vehicle within an event. |
| `veh_time` | float | 8.2 | **Required.** Time at which the vehicle state is recorded, typically measured relative to the start of the event or run, in seconds. |
| `lane_id` | int | 5 | **Required.** Application-normalized lane identifier. |
| `veh_dist_trav` | float | 97.47 | **Required.** Cumulative distance traveled by the vehicle along its trajectory up to the recorded time, in meters. |
| `orig_lane_id` | int | 5 | Original lane identifier as it appears in the source dataset, preserved before any lane normalization. |
| `veh_speed` | float | 4.71 | Instantaneous vehicle speed at the recorded time, in m/s. |
| `veh_accel` | float | 1.13 | Instantaneous vehicle acceleration at the recorded time, in m/s². |
| `veh_jerk` | float | | Instantaneous rate of change of acceleration at the recorded time. |
| `veh_length` | float | 5.39 | Vehicle length in meters. |
| `veh_width` | float | 2.67 | Vehicle width in meters. |
| `preceding_veh_id` | int | | Identifier of the vehicle directly preceding the subject vehicle in the same lane. |
| `veh_spacing` | float | | Distance between the subject vehicle and its preceding vehicle at the recorded time. |
| `veh_time_headway` | float | | Time headway between the subject vehicle and its preceding vehicle at the recorded time. |
| `x_map_loc` | float | 378.57 | Vehicle lateral position in the map-based coordinate system, in meters. |
| `y_map_loc` | float | 211.69 | Vehicle longitudinal position in the map-based coordinate system, in meters. |
| `x_frenet_loc` | float | | Vehicle lateral position in a Frenet coordinate system aligned with the roadway reference path, in meters. |
| `y_frenet_loc` | float | | Vehicle longitudinal position in a Frenet coordinate system aligned with the roadway reference path, in meters. |
| `x_map_origin` | float | | X-coordinate (longitude) of the map origin or local reference point associated with the dataset, in decimal degrees. |
| `y_map_origin` | float | | Y-coordinate (latitude) of the map origin or local reference point associated with the dataset, in decimal degrees. |
| `veh_lat` | float | | Vehicle latitude in geographic decimal degrees. |
| `veh_lon` | float | | Vehicle longitude in geographic decimal degrees. |
| `veh_automation` | int | 0 | Indicator of the vehicle's automation or connectivity status. Typically 0 (not automated) or 1 (automated). |
| `osm_way_id` | int | | Identifier of the associated OpenStreetMap way. |
| `osm_speed_limit` | int | | Speed limit associated with the roadway segment, from OpenStreetMap or another map source. |
| `osm_traffic_control` | int | | Traffic control information for the roadway segment, such as stop or signal control. |
| `event_name` | text | run index: 1 | Human-readable name or label for the data collection event. When absent, the application generates a label of the form "Event 1". |

### Internally Reserved Names

The following column names are created and populated internally by the application's analysis modules and must not appear as keys in any `col_defs` configuration. The application rejects configurations that use them:

- `time_diff` — time difference between consecutive records for the same vehicle
- `veh_speed_cal`, `veh_accel_cal`, `veh_jerk_cal` — kinematics reconstructed from trajectory position data
- `spacing_cal` — vehicle spacing calculated from trajectory position data
- Any name beginning with `calc_` — spline-derived kinematics
- `is_spline_imputed` — spline imputation flag
- `summary_json` — event summary storage

---

## Complete Example

The following example illustrates a complete configuration file for a CSV dataset that includes vehicle trajectory data collected at an instrumented highway site. Speed data in the source file is in mph and will be converted automatically to m/s for storage. Empty acceleration cells default to 0.

```json
{
  "file_ext": "csv",
  "col_defs": {
    "event_id":       { "func": "group",      "parms": [10, 11], "type": "int" },
    "event_name":     { "func": "event_name", "parms": [10, 11, "Site "], "type": "text" },
    "veh_id":         { "func": "copy",       "parms": [0], "type": "int" },
    "veh_time":       { "func": "copy",       "parms": [1] },
    "lane_id":        { "func": "copy",       "parms": [2], "type": "int" },
    "orig_lane_id":   { "func": "copy",       "parms": [2], "type": "int" },
    "veh_dist_trav":  { "func": "copy",       "parms": [3], "units": "ft" },
    "veh_speed":      { "func": "copy",       "parms": [4], "units": "mph" },
    "veh_accel":      { "func": "copy",       "parms": [5, 0], "units": "ft/s^2" },
    "veh_length":     { "func": "copy",       "parms": [6], "units": "ft" },
    "veh_width":      { "func": "copy",       "parms": [7], "units": "ft" },
    "site_code":      { "func": "copy",       "parms": [10], "type": "int" },
    "data_source":    { "func": "const",      "parms": ["site survey"], "type": "text" },
    "my_custom_tag":  {}
  }
}
```

In this example, `site_code` is a user-defined column that preserves the raw site identifier from the source data. `data_source` is a user-defined text column storing the same constant label for every row. `my_custom_tag` is declared with an empty definition, creating a placeholder database column with null values for all rows — it would be populated by a subsequent processing step.
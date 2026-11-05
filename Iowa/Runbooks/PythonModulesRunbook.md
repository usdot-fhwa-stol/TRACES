# Python Data Processing & Extension Modules Runbook

**Created By: Zeferino Araiza-Flores**

---

This runbook documents the additional Python scripts and SQL snippets utilized for data ingestion, transformation, and application integration. Each section details the script's core function and highlights specific configurable variables that can be modified to target different tables or datasets.

---

## 1. Batch Parquet Ingestion Script (`batch_import.py`)

This script utilizes `pyarrow.parquet` and the `adbc_driver_postgresql` library to efficiently bulk-load Parquet files into the database. 

*   **Function:** It iterates through a specified list of product levels, searches for `.parquet` files within designated directories, and pushes the data to PostgreSQL. 
*   **Table Creation:** The script will drop existing tables with the same name before creating new ones and appending the files in chunks.
*   **Configurable Variables:**
    *   `uri`: The connection string for the database must be provided here.
    *   `product_level`: Currently set to process `['C6', 'C5', 'C4']`.
    *   `base_path`: Currently targeting `/mnt/padlock/VTTI_Truck_processed_Data_Package_1/`.

---

## 2. Catalog Table Generator (`catalog_query`)

This SQL script is used to generate a master summary table for UI rendering and quick lookups.

*   **Function:** It drops any existing `catalog_table` and builds a new one containing summary metrics grouped by `release_key`. 
*   **Calculated Metrics:** The query calculates `trip_duration_sec` and counts `total_leaders` based on the distinct `lead_target_id` where `has_lead` is true.
*   **Configurable Targets:** 
    *   The query is explicitly hardcoded to pull from the `c6_ego_wfleet_radartargets` table. This must be updated if querying a different table level (like C5).

---

## 3. Data Transformation & Gap Filling Scripts

These scripts manage the chronological alignment of sparse radar target data with continuous ego vehicle timelines.

### Continuous Table Builder (`new_table_merge.py`)
*   **Function:** This script generates a brand new table by finding missing chronological gaps in the target table and filling them with rows from the ego table. 
*   **Process:** It fetches unique trip keys, processes them trip by trip, concatenates the target and missing ego data, sorts them by time, and uploads to the new table.
*   **Configurable Variables:** `DB_URI`, `EGO_TABLE` (currently "c5"), `TARGET_TABLE` (currently "c6"), `NEW_TABLE` (currently "continuous_complete"), `TRIP_COLUMN` ("release_key"), and `TIME_COLUMN` ("t_rel_sec").

### Gap Injection Script (`table_append.py`)
*   **Function:** Instead of building a new table, this script injects missing gap rows directly into an existing target table.
*   **Process:** It aligns the missing ego data to match the exact schema of the target table, ensuring empty columns are auto-filled with NULLs, before appending the rows via chunked SQL uploads.
*   **Configurable Variables:** Requires configuration for `DB_URI`, `EGO_TABLE` ("c5"), `TARGET_TABLE` ("c6"), `TRIP_COLUMN` ("release_key"), and `TIME_COLUMN` ("t_rel_sec").

---

## 4. PyQt6 Application Integration Helper (`postgres_helper.py`)

This script integrates the PostgreSQL database with a graphical user interface for trip selection and data export.

*   **Function:** It reads a database URI from a temporary CSV file, connects to the database, and queries the `catalog_table`. 
*   **User Interface:** It generates a custom display string for a `QInputDialog` dropdown menu, allowing the user to select a trip based on its ID, Date, Duration, and Leader count. 
*   **Data Extraction:** Upon selection, it executes a Common Table Expression (CTE) query to extract specific Ego and Leader rows.
*   **Calculations:** The CTE calculates `dt` (time difference) and `ego_dist_trav` (distance traveled). 
*   **Output:** The final trajectory subset is written out to a temporary CSV file and its path is returned.
*   **Configurable Targets:** 
    *   The script specifically queries `c6_ego_wfleet_radartargets` to extract the detailed trajectory data. The SQL text within this script must be modified if a different table needs to be queried.

---

## 5. Utility Scripts

### Parquet Schema Checker (`metadata_check.py`)
*   **Function:** A simple utility utilizing `pyarrow.parquet` to load two specific Parquet files and print their schema definitions to the console for verification.

### Proof of Life Scanner (`parquet_check.py`)
*   **Function:** Scans a directory of Parquet files using the `has_lead` column to verify if any leader data actually exists within the dataset before attempting a full database import. 
*   **Configurable Variables:** The `base_path` targeting the specific Parquet subset directory.

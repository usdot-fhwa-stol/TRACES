import csv
import os
import tempfile

import pandas as pd
from sqlalchemy import create_engine
from PyQt6.QtWidgets import QInputDialog


def process_postgres_csv(parent_widget, file_path: str) -> str:
    """Export one ISU file/route pair, converting milliseconds to seconds.

    Milepost is converted from miles to meters; speed remains in km/h.
    Catalog duration remains
    stored in milliseconds; only its displayed value is converted.
    """
    engine = None
    try:
        with open(file_path, 'r', newline='', encoding='utf-8') as f:
            uri = next(csv.reader(f))[0].strip()

        print("Connecting to Postgres...")
        engine = create_engine(uri)
        catalog_df = pd.read_sql(
            '''SELECT "vtti.file_id", "ROUTE_ID", trip_duration
               FROM public.catalog_table
               ORDER BY "vtti.file_id", "ROUTE_ID";''',
            engine,
        )
        if catalog_df.empty:
            print("No trips found in public.catalog_table.")
            return ""

        # Number labels to associate each selection with exactly one catalog row.
        options = [
            f"{index + 1}. File: {file_id} | Route: {route_id} | "
            f"Duration: {duration / 1000.0:.1f}s"
            for index, (file_id, route_id, duration) in enumerate(
                catalog_df.itertuples(index=False, name=None)
            )
        ]
        item, ok = QInputDialog.getItem(
            parent_widget, "Database Selection",
            "Select a file and route to import:", options, 0, False,
        )
        if not ok or not item:
            return ""

        selected = catalog_df.iloc[options.index(item)]
        file_id = selected['vtti.file_id']
        route_id = selected['ROUTE_ID']
        # Use native Python values for psycopg2 parameters.
        params = {
            "file_id": None if pd.isna(file_id) else int(file_id),
            "route_id": None if pd.isna(route_id) else str(route_id),
        }
        print(f"Querying C6 for file {params['file_id']}, route {params['route_id']}...")
        query = '''
            SELECT
                "TARGET_ID" AS veh_id,
                "vtti.timestamp" / 1000.0 AS veh_time,
                "SPEED_KMPH" AS veh_speed,
                NULL::double precision AS veh_accel,
                "MILEPOST" * 1609.344 AS veh_dist_trav,
                COALESCE(
                    "vtti.timestamp" - LAG("vtti.timestamp") OVER (
                        PARTITION BY "TARGET_ID"
                        ORDER BY "vtti.timestamp"
                    ),
                    0
                ) / 1000.0 AS dt
            FROM public.c6
            WHERE "vtti.file_id" IS NOT DISTINCT FROM %(file_id)s
              AND "ROUTE_ID" IS NOT DISTINCT FROM %(route_id)s
            ORDER BY veh_time, veh_id;
        '''
        trip_data = pd.read_sql(query, engine, params=params)

        # Reserve 9999 for ego without merging it with a real target ID.
        target_ids = pd.to_numeric(trip_data['veh_id'], errors='coerce')
        if target_ids.eq(9999).any():
            raise ValueError(
                "TARGET_ID 9999 already exists in this trip; "
                "choose another reserved ID for ego."
            )
        trip_data['veh_id'] = trip_data['veh_id'].replace({'ego': 9999})

        # Preserve the existing five-column order and append dt.
        # No acceleration mapping was specified, so leave that column empty.
        temp_fd, temp_csv = tempfile.mkstemp(suffix=".csv")
        os.close(temp_fd)
        trip_data.to_csv(temp_csv, index=False)
        print(f"Trajectory CSV created at: {temp_csv}")
        return temp_csv
    except Exception as e:
        print(f"Postgres Helper Error: {e}")
        return ""
    finally:
        if engine is not None:
            engine.dispose()

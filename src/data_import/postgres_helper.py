import pandas as pd
from sqlalchemy import create_engine
import tempfile
import csv
import os
from PyQt6.QtWidgets import QInputDialog

def process_postgres_csv(parent_widget, file_path: str) -> str:
    """
    Reads a URI from a one-line CSV, shows a customized dropdown of the catalog, 
    queries the required columns for the selected trip using the hidden release_key, 
    and returns the path to a temporary CSV for the application to import.
    """
    try:
        # 1. Safely read the URI from the one-line CSV
        with open(file_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            uri = next(reader)[0].strip() 

        # 2. Connect to DB and pull the catalog table
        print("Connecting to Postgres...")
        engine = create_engine(uri)
            
        catalog_df = pd.read_sql("SELECT release_key, local_datetime, trip_duration_sec, total_leaders FROM catalog_table;", engine)

        # 3. Create the custom display string
        # Example output: "2026-07-19 08:15:30 | Duration: 452.5s | Leaders: 3"
        catalog_df['display_name'] = (
            catalog_df['local_datetime'].astype(str) + " | " +
            "Duration: " + catalog_df['trip_duration_sec'].astype(str) + "s | " +
            "Leaders: " + catalog_df['total_leaders'].astype(str)
        )
        
        options = catalog_df['display_name'].tolist()

        # 4. Produce the Catalog Drop Box
        item, ok = QInputDialog.getItem(
            parent_widget, 
            "Database Selection", 
            "Select a trip to import:", 
            options, 
            0, 
            False
        )

        # 5. If selected, extract the hidden key and pull the main data
        if ok and item:
            # Map the user's text selection back to the hidden release_key
            trip_key = catalog_df.loc[catalog_df['display_name'] == item, 'release_key'].iloc[0]
            
            # Use the stable release_key to query the 1.2 billion row table safely
            # Note: Update the SELECT columns to match your tgsim-L1.json requirements            
            print(f"Querying C6 table for trip {trip_key}...")
            query = """
            WITH BaseData AS (
                SELECT
                    driver_id,
                    t_rel_sec,
                    speed_net_mps,
                    accel_long_ms2,
                    has_lead,
                    lead_target_id,
                    lead_x_m,
                    lead_vx_mps,
                    lead_ax_mps2,
                    lead_headway_s,
                    SUM(speed_net_mps * 0.1) OVER (ORDER BY t_rel_sec) AS ego_dist_trav
                FROM c6_mock_table
                WHERE release_key = %(key)s
            )
            -- EXTRACT EGO ROWS
            SELECT
                driver_id AS veh_id,                
                t_rel_sec AS veh_time,              
                speed_net_mps AS veh_speed,          
                accel_long_ms2 AS veh_accel,        
                ego_dist_trav AS veh_dist_trav      
            FROM BaseData

            UNION ALL

            -- EXTRACT LEADER ROWS
            SELECT
                lead_target_id AS veh_id,                    
                t_rel_sec AS veh_time,                        
                (speed_net_mps + lead_vx_mps) AS veh_speed,  
                (accel_long_ms2 + lead_ax_mps2) AS veh_accel,
                (ego_dist_trav + (speed_net_mps * lead_headway_s)) AS veh_dist_trav  
            FROM BaseData
            WHERE has_lead = 1

            ORDER BY veh_time, veh_id;
            """

            # trip_key is string as VTTI keys are alphanumeric
            trip_data = pd.read_sql(query, engine, params={"key": str(trip_key)})

            # 6. Save the result to a temporary CSV
            print("Generating Temp CSV")
            temp_fd, temp_csv = tempfile.mkstemp(suffix=".csv")
            os.close(temp_fd)
            trip_data.to_csv(temp_csv, index=False)
        
            engine.dispose()
            print(f"Test CSV created at: {temp_csv}")
            return temp_csv  
        
    except Exception as e:
        print(f"Postgres Helper Error: {e}")
        
    return ""
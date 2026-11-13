import pandas as pd
from sqlalchemy import create_engine
import time


# ==========================================
# CONFIGURATION (Fill in your details here)
# ==========================================
DB_URI = "postgresql://[USER]:[PASSWORD]@[HOST]:[PORT]/[DATABASE]"

EGO_TABLE = "c5"                  # Your continuous master timeline
TARGET_TABLE = "c6"               # Your existing sparse target data
NEW_TABLE = "continuous_complete" # The brand new table to be created
TRIP_COLUMN = "release_key"       # The ID used to separate trips
TIME_COLUMN = "t_rel_sec"         # The chronological timestamp column
# ==========================================

def build_new_table():
    print(f"--- Building New Table: {NEW_TABLE} ---")
    engine = create_engine(DB_URI)

    try:
        # 1. Get list of unique trips from the target table
        print("Fetching unique trip keys...")
        keys_query = f"SELECT DISTINCT {TRIP_COLUMN} FROM {TARGET_TABLE};"
        trip_keys = pd.read_sql(keys_query, engine)[TRIP_COLUMN].tolist()
        total_trips = len(trip_keys)
        
        # 2. Process Trip by Trip
        for index, trip_key in enumerate(trip_keys, start=1):
            start_time = time.time()
            
            # A. Fetch existing target data
            query_target = f"SELECT * FROM {TARGET_TABLE} WHERE {TRIP_COLUMN} = %(key)s;"
            target_df = pd.read_sql(query_target, engine, params={"key": trip_key})
            
            # B. Fetch missing ego gap data
            query_missing = f"""
                SELECT * 
                FROM {EGO_TABLE} e
                WHERE e.{TRIP_COLUMN} = %(key)s
                  AND NOT EXISTS (
                      SELECT 1 FROM {TARGET_TABLE} t 
                      WHERE t.{TRIP_COLUMN} = e.{TRIP_COLUMN} 
                        AND t.{TIME_COLUMN} = e.{TIME_COLUMN}
                  )
            """
            missing_df = pd.read_sql(query_missing, engine, params={"key": trip_key})
            
            # C. Combine and Auto-Fill
            if not missing_df.empty:
                complete_df = pd.concat([target_df, missing_df], ignore_index=True)
            else:
                complete_df = target_df.copy()

            # D. Rebuild Chronological Timeline
            complete_df = complete_df.sort_values(by=TIME_COLUMN).reset_index(drop=True)

            # E. Upload to the new table
            complete_df.to_sql(
                name=NEW_TABLE, 
                con=engine, 
                if_exists='append', 
                index=False,
                chunksize=10000
            )
            
            elapsed = round(time.time() - start_time, 2)
            print(f"[{index}/{total_trips}] {trip_key} - Processed in {elapsed}s")

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
    finally:
        engine.dispose()

if __name__ == "__main__":
    build_new_table()

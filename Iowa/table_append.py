import pandas as pd
from sqlalchemy import create_engine
import time

# ==========================================
# CONFIGURATION (Fill in your details here)
# ==========================================
DB_URI = "postgresql://[USER]:[PASSWORD]@[HOST]:[PORT]/[DATABASE]"

EGO_TABLE = "c5"             # Your continuous master timeline
TARGET_TABLE = "c6"          # The table receiving the injected rows
TRIP_COLUMN = "release_key"  # The ID used to separate trips
TIME_COLUMN = "t_rel_sec"    # The chronological timestamp column
# ==========================================

def inject_gaps():
    print(f"--- Starting Gap Injection into {TARGET_TABLE} ---")
    engine = create_engine(DB_URI)

    try:
        # 1. Get exact schema of the target table (so Pandas knows what to match)
        schema_query = f"SELECT * FROM {TARGET_TABLE} LIMIT 0;"
        target_schema_df = pd.read_sql(schema_query, engine)
        
        # 2. Get list of unique trips
        print("Fetching unique trip keys...")
        keys_query = f"SELECT DISTINCT {TRIP_COLUMN} FROM {TARGET_TABLE};"
        trip_keys = pd.read_sql(keys_query, engine)[TRIP_COLUMN].tolist()
        total_trips = len(trip_keys)
        
        # 3. Process Trip by Trip
        for index, trip_key in enumerate(trip_keys, start=1):
            start_time = time.time()
            
            # Find the missing gap rows in the ego table
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
            
            if missing_df.empty:
                print(f"[{index}/{total_trips}] {trip_key} - 0 gaps (Skipped)")
                continue

            # Align data: stack gaps onto the empty schema to auto-fill NULLs
            injection_df = pd.concat([target_schema_df, missing_df], ignore_index=True)
            
            # Slice off any extra ego columns that the target table doesn't have
            injection_df = injection_df[list(target_schema_df.columns)]

            # Push the formatted gaps directly into the database
            injection_df.to_sql(
                name=TARGET_TABLE, 
                con=engine, 
                if_exists='append', 
                index=False,
                chunksize=10000
            )
            
            elapsed = round(time.time() - start_time, 2)
            print(f"[{index}/{total_trips}] {trip_key} - Injected {len(injection_df)} rows in {elapsed}s")

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
    finally:
        engine.dispose()

if __name__ == "__main__":
    inject_gaps()

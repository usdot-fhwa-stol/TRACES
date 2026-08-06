import glob
import os
import pyarrow.parquet as pq
from adbc_driver_postgresql import dbapi

uri = 'postgresql://postgres:MadridKnight205!?!@localhost:5432/C6_S'

product_level = ['C6_Ego_wFleet_RadarTargets']

base_path = 'C:/Users/zefer/Projects/DOTSI/VTTI/Data Samples Package/Data Subsets'

with dbapi.connect(uri) as conn:
    with conn.cursor() as cur:

        for level in product_level:
            print(f"\n--- Starting import for {level} ---")

            folder_path = os.path.join(base_path, level, '*.parquet')
            file_list = glob.glob(folder_path)

            if not file_list:
                print(f"No files found in {level} folder. Skipping...")
                continue

            print(f"Found {len(file_list)} files for {level}. Pushing to database...")

            cur.execute(f"DROP TABLE IF EXISTS {level.lower()} CASCADE;")

            successful_uploads = 0

            for i, file in enumerate(file_list):
                try:
                    table = pq.read_table(file)

                    mode = "create" if successful_uploads == 0 else "append"

                    cur.adbc_ingest(level.lower(), table, mode=mode)

                    successful_uploads += 1
                    print(f" -> Uploaded file {i+1} of {len(file_list)} ({os.path.basename(file)})")

                except Exception as e:
                    print(f"WARNING: Skipping corrupted file {i+1} ({os.path.basename(file)})")
                    print(f"Error details: {e}")

            conn.commit()

            print(f"Successfully finished building the {level} table! Uploaded {successful_uploads} out of {len(file_list)} files.")

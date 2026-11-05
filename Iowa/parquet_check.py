import glob
import pyarrow.parquet as pq

base_path = '/mnt/padlock/VTTI_Truck_Data_Sample/Data Subsets/C6_Ego_wFleet_RadarTargets/*.parquet'
file_list = glob.glob(base_path)


print(f"Scanning {len(file_list)} Parquet files for leader data...")

found_data = False

for i, file in enumerate(file_list):
    try:
        table = pq.read_table(file, columns=['has_lead'])
        df = table.to_pandas()

        if df['has_lead'].sum() > 0:
            print(f"\nSUCCESS: Proof of life found!")
            print(f"File: {file}")
            print(f"Leader frames in this file: {df['has_lead'].sum()}")
            found_data = True
            break

    except Exception as e:
        print(f"Skipping {file} due to schema error: {e}")

if not found_data:
    print("\nCONFIRMED: No leader data in parquet files")

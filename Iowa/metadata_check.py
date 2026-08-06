import pyarrow.parquet as pq

parquet_file = pq.ParquetFile('C6/vtti_C6_mrm_20260528_123136_part001.parquet')
parquet_file_samp = pq.ParquetFile('Data Samples Package/Data Subsets/C6_Ego_wFleet_RadarTargets/C6_sample_trips.parquet')
print(parquet_file.schema)
print(parquet_file_samp.schema)
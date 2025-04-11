import sys
import csv
import json
import sqlite3
import os
from data_import.csv import Csv

def report_progress(prev_percent, row, total_rows):
    next_percent = 100 * row / total_rows
    
    if next_percent - prev_percent >= 1.0:
        prev_percent += 1
        print(f"\rProgress: {prev_percent}%", end="", flush=True)

    return prev_percent

def load_config(config_file):
    with open(config_file, 'r') as f:
        config = json.load(f)
    return config[0][list(config[0].keys())[0]]

def get_column_requirements(config):
    min_cols = 1
    col_types = {}
    for col_def in config:
        col = col_def.get('col', -1)
        if col >= 0:
            min_cols = max(min_cols, col + 1)
            col_types[col] = col_def.get('type', 'string')
    return min_cols, col_types

def first_pass(csv_file):
    if not os.path.exists(csv_file):
        print(f"Error: CSV file '{csv_file}' does not exist")
        sys.exit(1)
    
    with open(csv_file, 'r') as f:
        total_rows = sum(1 for _ in f) - 1  # Subtract 1 for potential header
    return total_rows

def verify_csv(csv_file, config, total_rows):
    min_cols, col_types = get_column_requirements(config)
    errors = row_num = 0
    prev_percent = -1
    
    with open(csv_file, 'r') as f:
        reader = csv.reader(f)
        first_row = True
        has_header = False
        
        for row in reader:
            if first_row:
                # Check if first row is header
                has_header = all(any(c.isalpha() for c in cell) for cell in row)
                first_row = False
                if has_header:
                    prev_percent = report_progress(prev_percent, row_num, total_rows)
                    row_num += 1
                    continue
            
            if len(row) < min_cols:
                print(f"Error at row {row_num + 1}: Expected {min_cols} columns, found {len(row)}")
                errors += 1
            
            for col_idx, expected_type in col_types.items():
                if col_idx < len(row):
                    value = row[col_idx].strip()
                    if value:
                        try:
                            if expected_type == 'integer':
                                int(value)
                            elif expected_type == 'double':
                                float(value)
                        except ValueError:
                            print(f"Error at row {row_num + 1}, col {col_idx + 1}: "
                                  f"Expected {expected_type}, got '{value}'")
                            errors += 1
            
            prev_percent = report_progress(prev_percent, row_num, total_rows)
            row_num += 1
    
    if not row_num:
        print("Error: CSV file contains no data rows")
        errors += 1
    
    return errors

def create_table(db_conn):
    cursor = db_conn.cursor()
    cursor.execute('''DROP TABLE IF EXISTS trajectory''')
    cursor.execute('''CREATE TABLE trajectory (
        event INTEGER NOT NULL,
        veh_id INTEGER NOT NULL,
        time REAL NOT NULL,
        lane_id INTEGER,
        x_map_loc REAL,
        y_map_loc REAL,
        x_frenet REAL,
        y_frenet REAL,
        x_map_origin REAL,
        y_map_origin REAL,
        veh_lat REAL,
        veh_lon REAL,
        veh_length REAL,
        veh_width REAL,
        speed REAL,
        accel REAL,
        ads INTEGER,
        PRIMARY KEY (event, veh_id, time)
    )''')
    db_conn.commit()

def process_csv(csv_file, db_file, config, total_rows):
    csv_processor = Csv()
    func_dict = {name: getattr(csv_processor, name) 
                for name in dir(csv_processor) 
                if callable(getattr(csv_processor, name)) and not name.startswith('_')}
    
    with sqlite3.connect(db_file) as conn:
        create_table(conn)
        cursor = conn.cursor()
        
        insert_sql = '''INSERT INTO trajectory (
            event, veh_id, time, lane_id, x_map_loc, y_map_loc, x_frenet, y_frenet,
            x_map_origin, y_map_origin, veh_lat, veh_lon, veh_length, veh_width,
            speed, accel, ads
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
        
        prev_percent = -1
        row_num = 0
        with open(csv_file, 'r') as f:
            reader = csv.reader(f)
            has_header = all(any(c.isalpha() for c in cell) for cell in next(reader))
            
            for row in reader:
                if has_header and row_num == 0:
                    prev_percent = report_progress(prev_percent, row_num, total_rows)
                    row_num += 1
                    continue
                
                values = {}
                for col_def in config:
                    name = col_def['name']
                    col = col_def.get('col', -1)
                    func_name = col_def.get('func', 'copy')
                    parms = col_def.get('parms', [])
                    
                    func = func_dict.get(func_name)
                    if func:
                        value = func(row, col, parms)
                        if col_def.get('type') == 'integer':
                            value = int(value) if value is not None else None
                        elif col_def.get('type') == 'double':
                            value = float(value) if value is not None else None
                        values[name] = value
                
                record = (
                    values.get('event'), values.get('id'), values.get('time'),
                    values.get('lane_id'), values.get('x_map_loc'), values.get('y_map_loc'),
                    values.get('x_frenet'), values.get('y_frenet'),
                    values.get('x_map_origin'), values.get('y_map_origin'),
                    values.get('veh_lat'), values.get('veh_lon'),
                    values.get('veh_length'), values.get('veh_width'),
                    values.get('speed'), values.get('acc'), values.get('ads')
                )
                try:
                    cursor.execute(insert_sql, record)
                except sqlite3.Error:
                    pass  # Ignore database errors and continue
                
                prev_percent = report_progress(prev_percent, row_num, total_rows)
                row_num += 1
        
        conn.commit()
        print("\rImporting complete")

def main():
    if len(sys.argv) != 3:
        print("Usage: python script.py <config_file.json> <input_file.csv>")
        sys.exit(1)
    
    config_file = sys.argv[1]
    csv_file = sys.argv[2]
    db_file = os.path.splitext(csv_file)[0] + '.sqlite'
    
    config = load_config(config_file)
    print("Reading CSV file...")
    total_rows = first_pass(csv_file)
    
    print("Verifying CSV file...")
    errors = verify_csv(csv_file, config, total_rows)
    if errors > 0:
        print(f"Found {errors} errors during verification")
        sys.exit(1)
    print("\rVerifying complete")
    
    print("Importing CSV file...")
    process_csv(csv_file, db_file, config, total_rows)

if __name__ == "__main__":
    main()

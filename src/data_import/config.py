import json
import importlib

class Config:
    def __init__(self, config_file_path):
        self.config_file_path = config_file_path
        self.configuration = {}

    def get_config(self, key):
        """
        Loads and validates the JSON configuration file, returning an array of arrays for the specified key.
        """
        # Clear the configuration dictionary
        self.configuration = {}

        # Load the JSON file
        try:
            with open(self.config_file_path, 'r') as file:
                config_data = json.load(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {self.config_file_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in configuration file: {e}")

        # Dynamically load CsvAlgos class
        try:
            # Import the module dynamically
            csv_algos_module = importlib.import_module("data_import.csv_algos")
            # Get the CsvAlgos class
            csv_algos_class = getattr(csv_algos_module, "CsvAlgos", None)
            if csv_algos_class is None:
                raise AttributeError("CsvAlgos class not found in data_import.csv_algos")
        except ImportError as e:
            raise ImportError(f"Failed to import data_import.csv_algos: {e}")

        # Validate and process each configuration entry
        for entry in config_data:
            if not isinstance(entry, dict) or "type" not in entry or "cols" not in entry:
                raise ValueError("Each configuration entry must be a dictionary with 'type' and 'cols' keys")

            type_key = entry["type"]
            cols = entry["cols"]

            # Convert cols to array of arrays if it's an array of dictionaries
            if cols and isinstance(cols[0], dict):
                cols = [cols]

            # Validate cols is an array of arrays
            if not isinstance(cols, list) or not all(isinstance(sub_cols, list) for sub_cols in cols):
                raise ValueError(f"Cols for type '{type_key}' must be an array of arrays")

            processed_cols = []
            min_cols = 1  # Initialize minimum column count

            for sub_cols in cols:
                # Verify required names
                required_names = {"event_id", "veh_id", "time"}
                sub_col_names = {col.get("name") for col in sub_cols}
                if not required_names.issubset(sub_col_names):
                    missing = required_names - sub_col_names
                    raise ValueError(f"Missing required column names {missing} in type '{type_key}'")

                # Process each column definition
                processed_sub_cols = []
                for col in sub_cols:
                    if not isinstance(col, dict) or "name" not in col:
                        raise ValueError(f"Each column in type '{type_key}' must be a dictionary with a 'name' key")

                    # Set default values
                    processed_col = {
                        "name": col["name"],
                        "col": col.get("col", -1),
                        "type": col.get("type", "float"),
                        "func": col.get("func", "copy"),
                        "parms": col.get("parms", [])
                    }

                    # Validate func exists as a public method in CsvAlgos
                    func_name = processed_col["func"]
                    if not hasattr(csv_algos_class, func_name) or func_name.startswith('_'):
                        raise ValueError(f"Invalid or non-public function '{func_name}' in column '{col['name']}' for type '{type_key}'")

                    # Check for misconfiguration
                    if processed_col["col"] == -1 and processed_col["func"] == "copy" and not processed_col["parms"]:
                        raise ValueError(f"Misconfiguration in column '{col['name']}' for type '{type_key}': col is -1, func is 'copy', and parms is empty")

                    # Update min_cols
                    if processed_col["col"] != -1:
                        min_cols = max(min_cols, processed_col["col"] + 1)

                    processed_sub_cols.append(processed_col)

                processed_cols.append(processed_sub_cols)

            # Store processed configuration with min_cols
            self.configuration[type_key] = {
                "cols": processed_cols,
                "min": min_cols
            }

        # Return the cols for the requested key
        if key not in self.configuration:
            raise KeyError(f"Configuration key '{key}' not found")
        return self.configuration[key]["cols"]

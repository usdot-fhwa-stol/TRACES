# Trajectory Research, Analysis, Correction, Exploration, and Simulation (TRACES)

TRACES is a desktop application for importing, exploring, analyzing, and correcting vehicle trajectory data. It uses JSON import profiles to normalize different CSV layouts into SQLite databases, then provides an interactive time-space plot, trajectory-quality analyses, interaction statistics, data-correction tools, and export through a PyQt6 interface.

**Version 1.0** · Python 3.12 · Windows installer and portable build · [Apache License 2.0](LICENSE)

![TRACES displaying vehicle trajectories on a time-space plot](docs/images/01-main-window-overview.png)

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Install on Windows](#install-on-windows)
- [Run from source](#run-from-source)
- [Basic workflow](#basic-workflow)
- [Import profiles](#import-profiles)
- [Settings, logs, and user data](#settings-logs-and-user-data)
- [Documentation](#documentation)
- [Repository layout](#repository-layout)
- [Development and tests](#development-and-tests)
- [Windows packaging](#windows-packaging)
- [Project status](#project-status)
- [License](#license)

## Features

**Import and explore**

- Import CSV trajectory data with reusable, configuration-driven column mappings and unit conversion.
- Open previously imported `.sqlite` and `.db` trajectory databases.
- Explore events and lanes on a time-space plot with linked time and distance sliders, wheel and per-axis zoom, hover details, vehicle search, and speed-based coloring.
- Select individual trajectories or groups of trajectories for statistics and time-series plots.
- Measure gaps and headways directly on the plot.
- Identify and fit shockwaves with the semi-automatic interaction mode.

**Analyze**

- Run jerk, missing-data, and internal-consistency analyses, platoon-consistency, spectral, and B-spline fitting.
- Compute leader/follower and lane-change statistics (Advanced Statistics), with box plots and CSV export.
- Compare kinematics on stacked distance, speed, acceleration, and jerk plots that share one time axis.

**Correct**

- Detect and fill missing observations by spline interpolation, then review and refine each gap, including adding guide points by hand (Gap Interpolation).
- Detect proximity and rapid-lane-change anomalies and fix them by reassigning lanes (Lane-Level Anomalies).
- Stitch together trajectory segments that belong to the same vehicle, with safety checks and undo (Trajectory Stitching).
- Keep every original observation: corrections are marked per row, the values they replace are saved, and each correction can be undone. Changes can be written to the open database or to a copy.

**Export**

- Export complete or selected datasets and analysis results to CSV.
- Save analysis plots as self-contained, interactive HTML pages.

## How it works

```text
CSV data + JSON import profile
             |
             v
   Configured import processor
             |
             v
 SQLite database: events, ranges, + side tables
       |                 |                 ^
       v                 v                 |
 Interactive plots   Analysis modules   Correction modules
       |                 |                 |
       +--------+--------+-----------------+
                v
          CSV / HTML output
```

The `events` table is configuration-driven, but every imported dataset must provide these fields:

| Field | Purpose |
|---|---|
| `event_id` | Identifies a collection event or run. |
| `veh_id` | Identifies a vehicle within an event. |
| `veh_time` | Records the trajectory timestamp in seconds. |
| `lane_id` | Stores the normalized lane identifier. |
| `veh_dist_trav` | Stores longitudinal distance traveled in meters. |

`event_id`, `veh_id`, and `veh_time` form the record key. A second table, `ranges`, stores the per-event summaries used by the event and range selectors. Optional columns such as speed, acceleration, jerk, original lane, location, and vehicle dimensions enable additional plots and analyses. Physical values are stored in SI units when a supported source unit is declared in the import profile.

As the application is used, it adds its own columns and tables: analysis results (`calc_*`, `*_cal`, `*_error`), the row provenance column `imputed_status`, the trajectory status column `traj_status`, and side tables for gaps, stitches, lane anomalies and their corrections, and the values that corrections replaced. Import profiles can't use these reserved names.

## Architecture

TRACES is organized in layers. Each package depends only on the packages below it, and never on a package above it:

| Layer | Package | Contents |
|---|---|---|
| 6 | `gui` | Main window, database session (opening, copying, and write targets), start screen, and the import, export, event-selection, log viewer, and about dialogs. |
| 5 | `plot` | The time-space plot widget, its view boxes and right-click menu, supporting plot dialogs, and the interaction-mode handlers (Gap Headway, Shockwave, stitch selection). |
| 4 | `analysis` | Base classes for computations and analysis dialogs, and the analysis and correction modules. |
| 3 | `ui_kit` | Shared widgets: stacked kinematic plots, box plots, plot styles, interactive HTML export, write-target row, branding. |
| 2 | `data_import` | Import processors, CSV import functions, unit conversion, database validation and event summaries (Qt-free). |
| 1 | `utils` | Row-status codes and SQL filters, sampling-interval helpers, user-data paths (Qt-free). |

[`traces.py`](src/traces.py) is the entry point. Computation lives in `analysis`; interaction on the time-space plot lives in `plot` handlers, which call into `analysis` (for example, shockwave fitting and trajectory stitching). Every package re-exports its public names lazily, so importing a package doesn't import its modules or Qt until they are used.

![TRACES module architecture: six packages layered from gui at the top through plot, analysis, ui_kit and data_import to utils, with the import, analysis, plot, and gui layers reading and writing a shared SQLite database, and analysis results and gui exports going to result windows, CSV files, and interactive HTML](docs/images/architecture-diagram.svg)

*Figure: TRACES module architecture.*

Each package has its own extension points:

| To add | Implement | Developer Guide section |
|---|---|---|
| A file format | `BaseProcess` | [7.4 Adding a file-format importer](docs/TRACES-developer-guide.md#74-adding-a-file-format-importer) |
| A CSV import function | a method of `CsvAlgorithms` | [7.3 Adding an import function](docs/TRACES-developer-guide.md#73-adding-an-import-function) |
| An analysis or correction module | `BaseComp` and `BaseAnalysisDialog` | [9. Extending the Analysis Package](docs/TRACES-developer-guide.md#9-extending-the-analysis-package) |
| A time-space plot interaction | `InteractionModeHandler` | [10. Plot Package and Interaction Handlers](docs/TRACES-developer-guide.md#10-plot-package-and-interaction-handlers) |
| A lane-level anomaly type | `AnomalyType` and `AnomalyTypeUI` | [11. Extending Lane-Level Anomaly Detection and Correction](docs/TRACES-developer-guide.md#11-extending-lane-level-anomaly-detection-and-correction) |
| Another data source (remote files, cloud storage) | design notes only | [12. Connecting Other Data Sources](docs/TRACES-developer-guide.md#12-connecting-other-data-sources) |

The [TRACES Developer Guide](docs/TRACES-developer-guide.md) describes the architecture, the data model and row-provenance rules, and each extension point in detail.

## Requirements

- Python 3.12
- A desktop environment capable of running PyQt6
- The runtime packages pinned in [`requirements.txt`](requirements.txt): PyQt6, pyqtgraph, superqt, NumPy, SciPy, and utm

The Windows build needs none of these: it bundles Python and every package.

## Install on Windows

Each push to the repository runs the [Windows build workflow](.github/workflows/build.yml), which publishes two artifacts on the workflow run's page (kept for seven days):

- **`TRACES_installer`** — `TRACES_setup.exe`, which installs TRACES under Program Files with Start Menu and desktop shortcuts and an Add/Remove Programs entry. Installing requires administrator rights.
- **`TRACES_portable`** — the same application as a folder; run `TRACES.exe` from it without installing.

Uninstalling removes the program but keeps your import profiles, settings, and log in `%APPDATA%\TRACES`.

## Run from source

From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd src
python traces.py
python traces.py
```

On Windows, use `py -3.12` to create the environment and `.venv\Scripts\Activate.ps1` to activate it.

> **Working-directory note:** run the development entry point with `src` as the current directory. When run from source, TRACES resolves import profiles, bundled resources, settings, and logs relative to the working directory; running `python src/traces.py` from the repository root will not find the profiles in `src/config`.

The repository does not include sample CSV or SQLite data. To use the application, provide either:

- a CSV file matching one of the bundled import profiles;
- a CSV file plus a profile written for its column layout; or
- an existing compatible SQLite trajectory database.

## Basic workflow

1. Launch the application and choose **Import Data** for CSV input or **Open Trajectories** for an existing database.
2. For an import, select the CSV file and the profile that matches its layout, and confirm the output `.sqlite` file.
3. Select an event, time range, and distance range to create the time-space plot.
4. Explore trajectories with the plot controls, the right-click menu, and the **Mode**, **Data**, and **Analysis** menus.
5. Correct the data with **Data → Gap Interpolation**, **Data → Lane-Level Anomalies**, or right-click **Stitching**, choosing whether changes go to the open database or a copy.
6. Use **File → Export** to save data or analysis results.

The [TRACES User Guide](docs/TRACES-user-guide.md) gives the complete illustrated workflow.

## Import profiles

Import profiles live in [`src/config`](src/config) and define the processor class, source file extension, output columns, import functions, types, and source units. The bundled profiles cover these layouts:

| Profile | Layout |
|---|---|
| [`ohio-one-veh.json`](src/config/ohio-one-veh.json) | Ohio records, one vehicle per row |
| [`ohio-two-veh.json`](src/config/ohio-two-veh.json) | Ohio records, two vehicles per row |
| [`tgsim-L1.json`](src/config/tgsim-L1.json), [`tgsim-L2.json`](src/config/tgsim-L2.json) | TGSIM L1 and L2 |
| [`tgsim-moving.json`](src/config/tgsim-moving.json) | TGSIM moving sensor |
| [`tgsim-stationary.json`](src/config/tgsim-stationary.json) | TGSIM stationary sensor |

These profiles are layout adapters, not a restriction on the application. New CSV layouts can be supported with another JSON profile, and new file formats can be added by implementing the `BaseProcess` interface.

The [TRACES Configuration Guide](docs/TRACES-config-guide.md) documents the complete profile schema, import functions, unit conversions, data dictionary, and the column names the application reserves.

## Settings, logs, and user data

TRACES keeps its writable files in one user data folder:

| Running | User data folder |
|---|---|
| Packaged Windows application | `%APPDATA%\TRACES` |
| From source | the current working directory (normally `src`) |

| File | Contents |
|---|---|
| `config\` | Import profiles. On first launch, the bundled profiles are copied here if the folder contains no JSON profiles; existing user profiles are never overwritten. Edit or add profiles here. |
| `traces.conf` | Window size and position, and the recent-files list. |
| `traces.log` | The import log, also shown by **View → Log**. It is cleared at the start of each import. |

Trajectory databases are stored wherever you choose when importing or saving a copy.

## Documentation

The guides are in the [`docs`](docs) folder:

| Guide | Audience and contents |
|---|---|
| [TRACES User Guide](docs/TRACES-user-guide.md) | Importing, plotting, selecting, analyzing, correcting, searching, and exporting through the GUI. |
| [TRACES Configuration Guide](docs/TRACES-config-guide.md) | Import-profile structure, required fields, import functions, units, the data dictionary, and reserved names. |
| [TRACES Developer Guide](docs/TRACES-developer-guide.md) | Architecture, data model, row provenance, extension interfaces, other data sources, and packaging. |

## Repository layout

| Path | Purpose |
|---|---|
| [`src/traces.py`](src/traces.py) | Application entry point. |
| [`src/utils.py`](src/utils.py) | Shared helpers: row-status vocabulary, sampling helpers, user-data paths. |
| [`src/gui/`](src/gui) | Main window, database session, start screen, and import/export dialogs. |
| [`src/plot/`](src/plot) | Time-space plot, supporting plots and dialogs; `handlers/` holds the interaction modes. |
| [`src/analysis/`](src/analysis) | Analysis and correction modules: jerk, missing data, internal consistency, spline, shockwave, advanced statistics, gap interpolation, manual stitching, and lane-level anomalies. |
| [`src/ui_kit/`](src/ui_kit) | Shared widgets, plot styles, and interactive HTML export. |
| [`src/data_import/`](src/data_import) | Import interfaces, CSV processing, import functions, unit conversion, and database checks. |
| [`src/config/`](src/config) | Bundled JSON import profiles. |
| [`src/assets/`](src/assets) | Application logo and icon artwork, and the build-time icon generator. |
| [`tests/`](tests) | Unit and GUI tests. |
| [`docs/`](docs) | User, configuration, and developer guides with screenshots. |
| [`.github/workflows/build.yml`](.github/workflows/build.yml) | Windows build and installer workflow. |
| [`requirements.txt`](requirements.txt) | Pinned runtime dependencies. |

## Development and tests

The repository is a source application rather than an installable Python package: it does not currently include `pyproject.toml`, `setup.py`, or a development requirements file.

After installing the runtime dependencies above, install the test-only packages and run the suite from the repository root:

```bash
python -m pip install pytest pytest-qt
python -m pytest -q
```

For a headless Linux environment:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Conventions every change follows ([14. Cross-Cutting Conventions](docs/TRACES-developer-guide.md#14-cross-cutting-conventions) in the TRACES Developer Guide explains each):

- **Dependency order.** A package imports only packages below it: `utils`, `data_import`, `ui_kit`, `analysis`, `plot`, `gui`. Use relative imports within a package and absolute imports across packages.
- **Lazy package exports.** A new public name goes in its package's `__init__.py`, in both the `_EXPORTS` map and the `TYPE_CHECKING` block.
- **Row provenance.** Don't add status or flag columns to `events`. Register row-status codes in `utils.IMPUTED_CODES`, select rows with the `utils` status filters rather than code literals, save overwritten values through the superseded-points helpers in `analysis.base_comp`, and keep module records in side tables.
- **Reserved names.** A column the application adds to `events` must also be listed in `analysis.base_comp.DERIVED_EVENTS_COLUMNS` (per-row results) and in the reserved names in `data_import/csv_process.py`.

When changing behavior, update the corresponding tests and documentation together.

## Windows packaging

The GitHub Actions workflow [`build.yml`](.github/workflows/build.yml) uses Python 3.12, PyInstaller, and NSIS to create both a portable Windows directory and an installer on each push. It generates the Windows icon from `src/assets/traces_icon.svg`, and fails if a bundled data file or a required package is missing from the build. The workflow artifacts are retained for seven days; this is a development build pipeline, not a published release channel.

The version number is set in two places that must match: `APP_VERSION` in [`src/ui_kit/brand.py`](src/ui_kit/brand.py) (shown in the About dialog) and `APP_VERSION` in the workflow's NSIS script (used by the installer and Add/Remove Programs). Section [13. Building and Packaging](docs/TRACES-developer-guide.md#13-building-and-packaging) of the TRACES Developer Guide explains what to change when adding modules, dependencies, or data files.

## Project status

A few TRACES modules are not yet implemented: the manual and fully automatic shockwave modes, and Advanced Stitching actions, which appear as disabled placeholders.

## License

Licensed under the Apache License, Version 2.0. See the [`LICENSE`](LICENSE) file for the full text.

## Contact

For more information, contact ITSAMS@dot.gov.


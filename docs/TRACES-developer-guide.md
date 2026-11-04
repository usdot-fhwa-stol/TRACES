# Trajectory Research, Analysis, Correction, Exploration, and Simulation (TRACES) — Developer Guide

## 1. Purpose & Audience

This guide is for software engineers extending TRACES — adding new file-format importers, new computational analyses, or new interactive plot behaviors. It describes how the application's modules fit together and documents the extension points in depth.

It intentionally does not restate material that already has its own document:

- For the JSON import configuration schema (column definitions, functions, units), see **trajectory-tools-conf-guide.md**.
- For end-user workflows and UI behavior, see **trajectory-tools-user-guide.md**.

## 2. Architecture Overview

TRACES is a PyQt6 desktop application built around a single SQLite database per opened trajectory dataset. Data flows in one direction, with two points where computation writes back into the database:

```
Source file (CSV, ...)
        │  data_import.BaseProcess subclass
        ▼
  events / ranges tables (SQLite)
        │                              │
        │  comp.BaseComp subclasses     │  plot.TimeSpacePlotWidget
        │  (may write derived columns   │  (reads events/ranges,
        │  back to events)              │  renders trajectories,
        ▼                              ▼  supports interactive modes)
   Result dialogs, CSV export      Time-space plot, other plots
```

`gui` is the orchestration layer: `main_window.py` owns the menu bar, the database connection, and constructs whichever `comp` dialog or `plot` widget an action requires. `comp`, `data_import`, and `plot` do not import from each other directly except through the shared `utils` module and, where noted below, one explicit cross-reference (Shockwave).

## 3. Data Model

Two tables matter to every module in this guide:

**`events`** — one row per (event_id, veh_id, veh_time). The schema is **config-driven**, not fixed: `data_import` creates whatever columns a given import's `column_defs()` declares. Only five columns are guaranteed to exist in every database: `event_id`, `veh_id`, `veh_time`, `lane_id`, `veh_dist_trav`. Every other column — `veh_speed`, `veh_accel`, `orig_lane_id`, `veh_length`, and so on — is optional, and any code that reads one must first check for its presence:

```python
utils.events_columns(db_connection) -> set
```

This also means the schema is not static at runtime: analysis modules (Internal Consistency, Spline Fitting) `ALTER TABLE` the `events` table to add derived columns (`veh_speed_cal`, `calc_*`, `is_spline_imputed`) during a session. `events_columns()` re-queries `PRAGMA table_info` every call rather than caching, and your code should do the same rather than checking once and holding onto the result.

**`ranges`** — one row per event, with an `event_id` and a `summary_json` rollup column. It is created generically by the import worker after `import_data()` completes — no `BaseProcess` subclass builds it directly. `BaseComp.comp()` iterates it to discover which events exist; `EventSelectionDialog` reads `summary_json` for display names.

## 4. Module Map

| Location | Contents | Purpose |
|---|---|---|
| top-level | `trajectory_tools.py`, `utils.py` | Application entry point; shared helpers used across packages. |
| `gui/` | `main_window.py` + 7 dialogs, `toggle_switch.py` | Menu bar, window lifecycle, database connection, and the dialogs the menu bar opens. |
| `data_import/` | `base_process.py`, `csv_process.py`, `csv_algorithms.py`, `units.py` | File-format import: JSON-configuration-driven parsing into `events`. |
| `comp/` | `base_comp.py`, `base_analysis_dialog.py`, `plot_export.py`, + 5 subpackages | Computational analyses over `events`: jerk, missing data, internal consistency, shockwave, spline fitting. |
| `plot/` | `time_space_plot_widget.py`, `custom_view_box.py`, `interaction_mode.py`, + 9 supporting files | The time-space plot and its interactive modes, plus smaller time-series plot dialogs. |
| `docs/` | conf guide, user guide | Referenced, not duplicated, by this document. |

## 5. Top-Level Application

| Component | Purpose |
|---|---|
| `trajectory_tools.py` | Entry point. Configures logging, calls `utils.initialize_user_config()` to seed `APPDATA` config files, constructs `QApplication` and `MainWindow`. |
| `utils.py` | Cross-package helpers with no PyQt dependency on the data side: `events_columns()`, `get_user_data_path()`, `last_dialog_dir()` / `remember_dialog_path()` (shared "remember last folder" state for file dialogs), `initialize_user_config()`. |

## 6. GUI Package

`gui` is the orchestration layer, not an extension point in its own right — new capabilities are added to `comp`, `data_import`, or `plot` and then wired into `main_window.py`'s menus (see the recipes in §7–9). It's documented here at overview level.

| Component | Purpose |
|---|---|
| `main_window.py` — `MainWindow` | Owns the menu bar (File/View/Data/Analysis/Mode), the SQLite connection, recent-files persistence (`trajectory_tools.conf`), and constructs `TimeSpacePlotWidget` / import / export / analysis dialogs on demand. Menus for Data/Analysis/Mode stay hidden until a database is open. |
| `home_widget.py` — `HomeWidget` | Landing screen shown before a database is open (Open / Import cards, recent files list). |
| `import_dialog.py` — `ImportDialog` | Drives `data_import.BaseProcess` — see §7. |
| `export_dialog.py` — `ExportDialog` | Export All / Export Selected column-picker and CSV writer for the raw `events` table. |
| `event_selection_dialog.py` — `EventSelectionDialog` | Multi-select checklist over the `ranges` table; shared by Spline Fitting and Export Analysis flows. |
| `about_dialog.py` — `AboutDialog` | About box; also reused as the startup splash screen (countdown-close variant). |
| `log_viewer_dialog.py` | Displays the persisted import/session log file. |
| `toggle_switch.py` — `ToggleSwitch` | Small reusable animated on/off widget (used by the plot's speed-gradient toggle). |

## 7. Data Import Package

### 7.1 The `BaseProcess` contract

An import processor owns everything specific to one source file format. The orchestration between the GUI thread and the worker thread is fixed by `BaseProcess` and never varies per format:

```python
class BaseProcess(ABC):
    def check_configuration(self, config: Dict[str, Any], log_fn: LogFn) -> int
    def column_defs(self) -> Dict[str, str]
    def check_file(self, input_path: str, worker: Any) -> int
    def import_data(self, input_path: str, db_connection: sqlite3.Connection, worker: Any) -> int
```

| Stage | Runs on | Responsibility |
|---|---|---|
| `check_configuration(config, log_fn)` | GUI thread | Validate the JSON config; retain whatever state later stages need. Must not touch the database or the input file. Returns an error count (0 = valid). |
| `column_defs()` | worker thread | Return an ordered `{column_name: "INTEGER"\|"REAL"\|"TEXT"}` mapping. Must include `event_id`, `veh_id`, `veh_time`, `lane_id`, `veh_dist_trav`. Only valid after a successful `check_configuration()`. |
| `check_file(input_path, worker)` | worker thread | Validate the input file's structure/content before any writes. Must not write to the database. |
| `import_data(input_path, db_connection, worker)` | worker thread | Populate the `events` table. Runs after `check_file()` returns 0 and after the worker has created the table from `column_defs()`. |

`worker` (passed to `check_file`/`import_data`) exposes `log_signal` and `progress_signal` Qt signals and an `interrupt_flag` boolean — check it periodically in longer loops so Cancel is responsive.

**Orchestration sequence** (owned by `ImportDialog` and `_ImportWorkerThread`, not by any processor):

1. `ImportDialog` scans the config directory, instantiates each configured processor class, and calls `check_configuration()` on a throwaway instance to filter the file-type selector down to valid configs.
2. At import start, `ImportDialog` creates a **fresh** processor instance and validates it again (processor instances are never reused — a fresh instance guarantees no state, e.g. group-index dictionaries, leaks between runs) and hands it to `_ImportWorkerThread`.
3. The worker thread calls `column_defs()` and creates the `events` table (composite primary key over the required columns, so `INSERT OR IGNORE` silently suppresses duplicate records).
4. The worker calls `check_file()`, then `import_data()`.
5. The worker creates and populates the `ranges` table itself — this step is generic over the `events` table and is never a processor responsibility.

### 7.2 `CsvProcess` — the reference implementation

`CsvProcess` implements the four stages for CSV files driven by a JSON `col_defs` configuration (documented fully in the config guide). Two details matter for anyone writing a second processor:

- **Function auto-discovery.** `CsvAlgorithms` (`csv_algorithms.py`) exposes one public method per supported `func` name (`copy`, `const`, `group`, …), all sharing the signature `func(row, col_def)`. `CsvProcess` discovers them by introspection at construction time — adding a new import function means adding a public method to `CsvAlgorithms`, nothing else.
- **Declaring source columns generically.** Because different functions consume `parms` differently, each function publishes an extractor via the `@source_cols` decorator:

```python
  @source_cols(lambda parms: [parms[0]])
  def copy(self, row, col_def) -> Any: ...
```

  `CsvProcess` calls this extractor during `check_configuration()` to bounds-check declared column indices generically, without knowing anything about the specific function.
- **`units.py`** provides `Units.convert()` / `Units.factor()` for unit-aware columns (a flat multiplier table, e.g. `mph → m/s`), keyed off `Units.SYSTEM_UNITS` (the destination unit forced for each recognized column). Reuse this rather than writing ad hoc conversions in a new processor.

### 7.3 How to add a new file-format importer

1. Create a new module (in `data_import/`, or your own package) with a class subclassing `BaseProcess` and implementing all four stage methods.
2. Retain whatever configuration state you need on `self` inside `check_configuration()` — it's the only stage that receives the config dict.
3. In `column_defs()`, return an ordered mapping that includes the five required columns.
4. Write a JSON config file (dropped into the app's `config` directory, `utils.get_user_data_path("config")`) with `"class": "dotted.path.ToYourClass"` and `"file_ext": "<ext>"`, plus whatever keys your `check_configuration()` expects.
5. **No code changes are needed in `import_dialog.py` or `main_window.py`.** `ImportDialog` discovers processors purely from the config directory at startup — it resolves `"class"` via `importlib`, checks it's a `BaseProcess` subclass, and adds it to the file-type selector if validation passes.
6. If your format benefits from unit-aware or generically-validated source columns, reuse `Units` and the `@source_cols` pattern from `csv_algorithms.py` rather than reinventing them.
7. Document the new configuration schema in the config guide.

## 8. Comp Package

### 8.1 `BaseComp` — the analysis interface

```python
class BaseComp(ABC):
    OPTIONS_SCHEMA: Dict[str, Any] = {}

    def comp(self, db_connection, options=None) -> List[Any]
    def comp_event(self, db_connection, event_id, options=None) -> List[Any]
    def comp_series(self, db_connection, event_id, veh_ids, options=None) -> List[Any]   # override this
```

`comp()` iterates every `event_id` in `ranges` and delegates to `comp_event()`, which delegates to `comp_series(db_connection, event_id, veh_ids=None, options)`. **Subclasses override only `comp_series`.** Passing `veh_ids=None` means "every vehicle in the event" (the top-level menu path); passing an explicit iterable scopes the analysis to a selection (the right-click path).

Subclass-specific parameters travel through the `options` dict rather than widening the method signature — declare recognized keys and defaults in `OPTIONS_SCHEMA`; `_resolve_options()` merges caller-supplied values over those defaults. Two protected helpers are available to subclasses: `_events_columns(db_connection)` (delegates to `utils.events_columns`) and `_event_veh_ids(db_connection, event_id)`.

### 8.2 `BaseAnalysisDialog` / `BaseAnalysisWorker` — the standard orchestration

Most analyses (Jerk, Missing Data, Internal Consistency, and the top-level half of Spline Fitting) run through this pair. Subclasses of `BaseAnalysisDialog` override:

```python
def _build_parameter_form(self) -> QFormLayout        # widgets; store refs on self
def _current_params(self) -> dict                     # read widget values
def _create_worker(self, db_path, event_ids, veh_ids, params, dest_path) -> BaseAnalysisWorker
def _csv_export_label(self) -> str = "Analysis"        # optional
def _show_results(self, results: list) -> None
RIGHT_CLICK_ENABLED: bool = True                       # False for top-level-only modules
```

There are two invocation paths through the same dialog:

| Path | Trigger | Button | On finish |
|---|---|---|---|
| **Normal** | top-level menu or right-click | OK | Emits `params_accepted(dict, list)` |
| **Export** | Export Analysis submenu | Save | Writes CSV, shows a completion dialog. `params_accepted` is **not** emitted. |

`BaseAnalysisWorker` (a `QObject`, moved to a `QThread` by the base dialog — subclasses never do this themselves) exposes `progress(int)`, `finished(list)`, `error(str)` signals and runs a supplied `run_for_event(conn, event_id, veh_ids, params, progress_callback)` callable once per event, concatenating results and optionally writing them to `dest_path` via a supplied `write_csv` callable.

### 8.3 Subpackages

| Subpackage | Comp class | Notes |
|---|---|---|
| `jerk/` | `JerkComp` (`BaseComp`) | Reconstructs jerk from measured/derived signals; standard `BaseAnalysisDialog` orchestration; box-plot results (top-level) or table (right-click). |
| `missing_data/` | `MissingDataComp` (`BaseComp`) | Gap detection over vehicle time series; excludes spline-imputed rows when present; standard orchestration. |
| `consistency/` | `InternalConsistencyComp` (`BaseComp`) | Reconstructs speed/accel/jerk from position and compares to measured columns; writes `*_cal`/`*_error` columns back via the dialog (not the comp class); standard orchestration. |
| `shockwave/` | `ShockwaveComp` (**not** a `BaseComp` subclass) | Curve-fitting math (rolling-mean smoothing, OLS regression) for shockwave identification. See the note below — the interactive half of this module lives in `plot/shockwave_handler.py`. |
| `spline/` | `SplineComp` (`BaseComp`) | Fits a spline to one kinematic quantity, derives the other three, optionally fills timing gaps. Two independent, differently-orchestrated entry points — see the note below. |

**Shockwave bridges comp and plot.** `ShockwaveComp` deliberately does not subclass `BaseComp`: there is no single "run over all events" batch entry point, because a shockwave is built incrementally as the user selects a chain of inflection points on the plot. `plot/shockwave_handler.py`'s `ShockwaveSemiAutoModeHandler` (an `InteractionModeHandler`, §9) calls `ShockwaveComp`'s static methods point-by-point as the chain grows, and only produces a `ShockwaveResult` on commit. `comp/shockwave/shockwave_config_dialog.py` and `shockwave_results_table_dialog.py` handle the configuration and CSV-export presentation around that interactive core. If you're extending Shockwave itself, expect to touch both packages; if you're using it as a template for a new module, decide early which side of this split your analysis actually needs.

**Spline Fitting has two independent, differently-orchestrated entry points**, and this is worth internalizing before copying its pattern:

| | `SplineCompDialog` (Data menu) | `SplineTestingDialog` (right-click → Filter) |
|---|---|---|
| Orchestration | Standard `BaseAnalysisDialog`/worker-thread pattern | Bypassed entirely — synchronous, non-threaded |
| Scope | One or more whole events | Exactly one trajectory |
| Writes to DB | Yes (`calc_*`, `is_spline_imputed`) | No — preview only |
| Why different | Fits the OK/Save worker-thread model fine | Live-preview / "Update Plot" / Close interaction doesn't fit that model |

`SplineCompDialog` sets `RIGHT_CLICK_ENABLED = False` for documentation purposes and explicitly rejects `export_mode=True` — Spline Fitting has no dedicated CSV export; its output columns ride along with the existing Export All/Selected paths once present.

### 8.4 Worked example: wiring in a new analysis (Jerk pattern)

Jerk Analysis shows the complete standard wiring end to end:

1. **`comp/jerk/jerk_comp.py`** — `JerkComp(BaseComp)` with `OPTIONS_SCHEMA` and `comp_series()`.
2. **`comp/jerk/jerk_comp_dialog.py`** — `JerkCompDialog(BaseAnalysisDialog)` implementing the parameter form and `_show_results()`, which branches on `self._veh_ids is not None` to pick between the box-plot dialog (top-level, whole-event) and the table dialog (right-click, scoped).
3. **`comp/jerk/jerk_box_plot_dialog.py`** / **`jerk_results_table_dialog.py`** — presentation. The box-plot dialog reuses `comp/plot_export.py`'s HTML export helper (§10).
4. **`gui/main_window.py`** — a `QAction` under **Analysis** (`_run_jerk_analysis`, whole-event, `veh_ids=None`) and one under **Export Analysis** (`_export_jerk_analysis`, `export_mode=True`, after an `EventSelectionDialog` pass).
5. **`plot/custom_view_box.py`** — an entry in `_context_menu_registry` under the "Noise Analysis" submenu, calling a `CustomViewBox` method that constructs `JerkCompDialog` scoped to the right-click context's vehicles.

### 8.5 How to add a new computational analysis module

1. Create a new subpackage under `comp/`.
2. Implement a `BaseComp` subclass: declare `OPTIONS_SCHEMA`, override `comp_series()` to return a list of per-(event, vehicle) result objects (a `@dataclass` is the existing convention).
3. Decide which orchestration fits: the standard `BaseAnalysisDialog`/`BaseAnalysisWorker` threaded pattern (the default — use it unless you have a specific reason not to); a synchronous non-threaded dialog if the interaction is a live preview (Spline's right-click path); or an `InteractionModeHandler`-driven interactive flow if the analysis is fundamentally incremental/user-built (Shockwave).
4. For the standard path: subclass `BaseAnalysisDialog`, implement `_build_parameter_form`, `_current_params`, `_create_worker`, `_csv_export_label`, `_show_results`; set `RIGHT_CLICK_ENABLED` appropriately.
5. Build a results presentation dialog. Reuse `comp/plot_export.py` for an HTML/SVG export if a box-plot or chart output fits, rather than writing new export plumbing.
6. Wire into `gui/main_window.py`: add a `QAction` under the **Analysis** menu (whole-event) and, if a CSV export makes sense, one under **Export Analysis** (`export_mode=True`).
7. If right-click scoping is desired, add an entry to `CustomViewBox._context_menu_registry` (under "Noise Analysis" or a new submenu) with a callback that builds your dialog against the current context's vehicles.
8. If the module writes derived columns back to `events`, add an `ensure_schema()`-style helper that defensively `ALTER TABLE ADD COLUMN`s (catching "duplicate column" errors — see `SplineComp.ensure_schema` / `InternalConsistencyComp`), and remember that every reader of those columns elsewhere in the app must gate on `utils.events_columns()` since they're optional and session-dependent.

## 9. Plot Package

### 9.1 `TimeSpacePlotWidget` and `CustomViewBox`

`TimeSpacePlotWidget` owns the overall widget (sliders, legend, mode menu integration, per-vehicle series data in `self._series`) and embeds a `pg.PlotWidget` whose view box is a custom subclass, `CustomViewBox`. `CustomViewBox` intercepts mouse events (press/move/release/right-click/wheel) and is responsible for the default Select-mode behaviors (shift-drag box selection, click-to-toggle, zoom, the right-click context menu built from `_context_menu_registry`). Both classes import shared types from `interaction_mode.py` so neither owns the other's vocabulary.

### 9.2 `InteractionMode` / `InteractionModeHandler` — the extension interface

```python
class InteractionMode(Enum):
    SELECT, GAP_HEADWAY, SHOCKWAVE_MANUAL, SHOCKWAVE_SEMI_AUTO, SHOCKWAVE_AUTOMATIC

class InteractionModeHandler:
    # Lifecycle
    def on_enter(self, widget) -> None
    def on_exit(self, widget) -> None
    def on_plot_cleared(self, widget) -> None          # after scatter_plot.clear()
    def on_before_data_reload(self, widget) -> None     # before clear/reload
    def on_data_reloaded(self, widget) -> None          # after new data is loaded

    # CustomViewBox-originated (return True to consume the event)
    def on_mouse_press(self, view_box, ev) -> bool
    def on_mouse_move(self, view_box, ev) -> bool
    def on_mouse_release(self, view_box, ev) -> bool
    def on_right_click(self, view_box, ev) -> bool

    # TimeSpacePlotWidget-originated
    def on_hover(self, widget, pos, closest_data, min_dist) -> bool
    def on_key_press(self, widget, event) -> bool       # Escape / Enter / Return only
    def on_slope_changed(self, widget, value: float) -> None
    def on_vicinity_changed(self, widget, value: float) -> None
    def wants_spline_position_data(self, widget) -> bool
```

Every hook defaults to a no-op (`False`/`None`), so a mode implements only what it needs — Select mode is `InteractionModeHandler()` with nothing overridden. `TimeSpacePlotWidget._active_handler` holds the current instance; `CustomViewBox` and `TimeSpacePlotWidget` both forward their events to it instead of encoding per-mode branches themselves.

Modes are registered in `TimeSpacePlotWidget._HANDLER_FACTORIES: {InteractionMode: HandlerClass}`, used as a fallback when `_on_mode_changed(mode, handler=None)` isn't given an explicit instance. Shockwave's factory entry is fallback-only in practice — its menu action always constructs a pre-configured handler itself and passes it in, since the handler needs constructor parameters the factory dict can't supply.

### 9.3 Worked example: Gap Headway

`GapHeadwayModeHandler` (`plot/gap_headway_handler.py`) is the simplest complete example: it keeps its own state (`_overlay_items`, `_locked_items`, `_saved_keys`) rather than storing anything on the widget, and:

- `on_hover` draws/clears a transient overlay near the cursor and suppresses the default tooltip (returns `True`).
- `on_mouse_release` locks/unlocks a point under the cursor, suppressing default click-to-select.
- `on_mouse_move` unconditionally suppresses the default selection-box drag.
- `on_key_press` clears all locked points on Escape.
- `on_enter`/`on_exit` persist locked points across a temporary mode switch by saving/restoring keys rather than the drawn items themselves; `on_data_reloaded` re-draws from those saved keys after a lane/event change.

**Shockwave's handler is the more advanced example** (`ShockwaveSemiAutoModeHandler`): it uses `on_key_press` for both Enter (commit chain) and Escape (revert edit / clear chain), uses `on_mouse_release` to either enter edit mode on a committed shockwave (Ctrl+click) or extend the active chain, and is the one handler that overrides `wants_spline_position_data` so the plot substitutes `calc_position` for `veh_dist_trav` when the user has configured it to fit against spline data. Its actual curve-fitting (rolling-mean smoothing, OLS regression) delegates to `comp.shockwave.ShockwaveComp` — see §8.3's note on why that class isn't a `BaseComp` subclass.

### 9.4 Supporting plot components

| Component | Purpose |
|---|---|
| `time_space_plot_settings_dialog.py` | Pre-flight dialog (event/time/distance range) shown before `TimeSpacePlotWidget` is created. |
| `time_speed_dialog.py` (`TimeSpeedDialog`) / `time_accel_dialog.py` | Base dialog + subclasses for simple time-series plots (speed, acceleration) launched from the right-click "Plots" submenu; overrides `Y_COLUMN`/`Y_LABEL`/`PLOT_TITLE_SUFFIX` per subclass. |
| `statistics_dialog.py` | Right-click "Statistics" output. |
| `vehicle_search_dialog.py` | Modeless dialog behind View → Search Vehicle; enabled only in Select mode with an event open. |
| `box_plot_item.py` | Reusable `pyqtgraph` item for box-plot rendering; shared by comp result dialogs (Jerk, Missing Data). |
| `vertical_gradient_value.py` | The speed-gradient legend widget alongside the time-space plot. |
| `gap_headway_handler.py`, `shockwave_handler.py`, `shockwave_result_panel.py` | Interaction mode handlers and Shockwave's floating results panel — see §9.2–9.3. |

### 9.5 How to add a new interactive mode

1. Add a new member to `InteractionMode` in `interaction_mode.py`.
2. Create a handler module under `plot/`, subclassing `InteractionModeHandler` and overriding only the hooks your mode needs.
3. Register it in `TimeSpacePlotWidget._HANDLER_FACTORIES`, unless your handler needs constructor arguments — in that case, construct it explicitly wherever the mode is activated (a menu action) and call `_on_mode_changed(mode, handler=your_instance)` directly, following Shockwave's pattern.
4. Add a `QAction` to the Mode menu (`mode_menu`, passed into `TimeSpacePlotWidget` from `MainWindow`) that triggers the mode change.
5. Do hit-testing against `widget._series` (already-loaded per-vehicle time/distance arrays) inside your handler rather than re-querying the database on every mouse event.
6. If your mode depends on a `comp` module's derived columns (as Shockwave depends on Spline's `calc_position`), override `wants_spline_position_data()` (or extend that pattern) so `_update_plot_data` substitutes the right source column before reloading.
7. If your mode should disable other actions while active (Shockwave disables Vehicle Search, for instance), update the relevant widget bookkeeping (see `_update_search_action_for_mode`) from `on_enter`/`on_exit`.

## 10. Cross-Cutting Conventions

**Worker-thread pattern.** Both `data_import` (`_ImportWorkerThread`) and `comp` (`BaseAnalysisWorker`) follow the same shape: a `QObject`/`QThread` worker with `progress`/`finished`/`error`-style signals, constructed and moved to a thread by the GUI-thread orchestrator (never by the worker itself), with a cooperative `interrupt_flag` checked periodically for cancellation. If you add a new long-running operation, match this shape rather than inventing a new threading pattern.

**Shared plot export.** `comp/plot_export.py` provides `export_plot_to_svg()` and a pan/zoom JavaScript snippet (`SVG_PAN_ZOOM_JS`) for building self-contained interactive HTML exports of `pyqtgraph` plots. It's currently used by the Jerk and Internal Consistency result dialogs — reuse it for any new analysis's HTML export rather than reimplementing SVG pan/zoom.

**The options-dict pattern.** `BaseComp.OPTIONS_SCHEMA` keeps the `comp`/`comp_event`/`comp_series` signatures stable as new analyses are added — new parameters are new dict keys with defaults, never new positional arguments.

**Optional-column checks.** Anywhere in the codebase that reads a non-required `events` column, expect (and follow) a preceding `utils.events_columns()` check.

**Logging.** Every module uses `logger = logging.getLogger(__name__)` at module scope; prefer this over `print` or ad hoc logging setup.

## Appendix: Related Documents

- **trajectory-tools-conf-guide.md** — JSON import configuration schema (column definitions, functions, units, data dictionary).
- **trajectory-tools-user-guide.md** — end-user workflows, menus, and interaction modes from the user's perspective.
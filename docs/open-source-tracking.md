# Trajectory Tools Open Source Tracking

## Purpose

This document tracks the public development direction for Trajectory Tools. It is intended for users, researchers, and developers who want to contribute improvements, compare versions, or understand which parts of the application are ready for extension.

Trajectory Tools is a modular PyQt6 desktop application for importing, visualizing, and analyzing vehicle trajectory data. The current codebase already has clear extension points for new import formats, analysis modules, plot interactions, and export workflows. This document highlights those areas and leaves room for contributors to propose recommended implementations.

## Version Tracking

| Version | Date | Status | Main Changes | Notes |
|---|---:|---|---|---|
| 0.1.0 | TBD | Current development baseline | CSV import, SQLite-backed trajectory storage, time-space plotting, event/lane selection, speed-gradient display, spline fitting, jerk analysis, missing-data analysis, internal-consistency analysis, gap/headway mode, semi-automatic shockwave mode | Treat as the first open-source tracking baseline. |
| 0.1.1| TBD | Patch candidates | Bug fixes, documentation improvements, UI polish, targeted tests | Recommended for small first-time contributions. |


When a release is prepared, add a new row with:

- **Version**: semantic version tag, for example `0.1.1` or `0.2.0`.
- **Date**: release date or planned date.
- **Status**: `Current`, `Released`, `Proposed`, or `Deprecated`.
- **Main Changes**: short user-facing summary.
- **Notes**: migration notes, known limitations, or links to related pull requests.

## Current Capabilities

| Area | Current Implementation | Extension Surface |
|---|---|---|
| Data import | JSON-configured CSV import through `data_import.CsvProcess` | Add processors for new file formats or new CSV transformation functions. |
| Data storage | One SQLite database per imported dataset, with `events` and `ranges` tables | Add schema validation, migrations, metadata tables, or provenance tracking. |
| Plotting | Interactive time-space plot with trajectory selection, speed coloring, time filtering, and lane/event controls | Add plot layers, annotations, exportable view states, or synchronized plots. |
| Analysis modules | Jerk, missing data, internal consistency, spline fitting, and shockwave analysis | Add new `comp` modules using the existing dialog/worker/result-table pattern. |
| Interaction modes | Select trajectory, gap/headway, and semi-automatic shockwave | Add manual or automatic shockwave modes, measurement tools, or annotation workflows. |
| Export | CSV export for raw data and analysis results | Add JSON/Parquet exports, report bundles, or reproducible analysis manifests. |

## Recommended Implementations

The items below are intentionally written as contribution invitations. If you implement one, update this table with design notes, test coverage, and any limitations discovered during review.

| ID | Recommendation | Why It Matters | Suggested Starting Point | Contributor Notes |
|---|---|---|---|---|
| RI-001 | Add import support for additional trajectory formats | Many trajectory datasets are not delivered in the current CSV layout | `data_import/base_process.py`, `data_import/csv_process.py`, `config/` | Contributors can propose target formats here. |
| RI-002 | Add derived-data provenance tracking | Analyses can write calculated columns back to `events`; users need to know how they were created | SQLite metadata table, `comp/spline/`, `comp/consistency/` | Include parameters, timestamp, and tool version. |
| RI-003 | Add richer analysis export bundles | Researchers often need parameters, figures, and results together | `comp/plot_export.py`, analysis result dialogs | Possible bundle formats: ZIP, JSON manifest plus CSV files. |
| RI-004 | Support multiple configurable spline-fitting methods | Different trajectory datasets and analysis tasks may require different spline methods and parameter settings | `comp/spline/spline_comp.py`, `comp/spline/spline_testing_dialog.py`, `comp/spline/spline_comp_dialog.py` | Extend the current B-spline approach with additional selectable spline-fitting methods and method-specific parameters. Use a common fitting interface, retain the current method as the default, and support previewing and comparing fits before applying them. Test parameter validation and consistent position, speed, acceleration, and jerk outputs; document each method’s assumptions and limitations. |

Add new recommendations below this line:

| ID | Recommendation | Why It Matters | Suggested Starting Point | Contributor Notes |
|---|---|---|---|---|
| RI-005 | _Open slot_ | _What user or developer problem does this solve?_ | _Relevant files or modules_ | _Proposed by / status / links_ |

## Contribution Workflow

Recommended workflow for contributors:

1. Read the [User Guide](trajectory-tools-user-guide.md) to understand the workflows affected by your change.
2. Read the [Developer Guide](trajectory-tools-developer-guide.md) for the relevant package pattern.
3. Open an issue or discussion describing the proposed implementation, expected behavior, and any dataset assumptions.
4. Keep the first pull request small and testable.
5. Add or update documentation when the user-facing behavior changes.
6. Add tests when the change affects parsing, database writes, numerical results, plot state, or interaction-mode state.
7. Update the version tracking table or recommended implementation table when the change lands.

## Implementation Guidelines

Prefer changes that match the existing architecture:

- Put import logic behind a `BaseProcess` subclass or a new CSV algorithm function.
- Put reusable computation in `comp/`, not directly in GUI code.
- Put interactive plot behavior behind an `InteractionModeHandler`.
- Keep database assumptions explicit by checking available `events` columns before reading optional fields.
- Use dialogs and result tables consistently with the existing analysis modules.
- Avoid broad rewrites unless the proposal includes a migration path and test plan.


## Maintainer Checklist

Before accepting a contribution, verify:

- The change has a clear user or developer benefit.
- The implementation follows the existing package boundaries.
- New behavior is documented in the relevant guide.
- Tests were added or the reason for not adding tests is documented.
- The version tracking and recommended implementation tables are updated when appropriate.
- Any new data, images, or third-party code have compatible licensing.



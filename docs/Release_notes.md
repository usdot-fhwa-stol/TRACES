 TRACES Release Notes
----------------------------

### Version 1.0.0, released Nov 17th 2026

### Summary
Trajectory Research, Analysis, Correction, Exploration, and Simulation (TRACES) tool (NEW) – This is the first release of TRACES, an open-source desktop application for importing, visualizing, analyzing, and correcting vehicle trajectory data. The tool allows users to explore vehicle movements and interactions through interactive time-space plots, evaluate trajectory data quality and traffic behavior, identify and correct missing or inconsistent observations, and export data and analysis results for further review. TRACES supports configurable CSV imports, SQLite trajectory databases, and an interactive graphical interface for transportation research and trajectory analysis.

### TRACES

The first release includes the following features and capabilities:

- Trajectory data import: Import vehicle trajectory data from CSV files using configurable JSON import profiles for different column layouts and unit conversions. The application includes profiles for Ohio and TGSIM data layouts and can open existing compatible SQLite trajectory databases.
- Interactive trajectory visualization: Explore vehicle trajectories using time-space plots with event and lane selection, adjustable time and distance ranges, zoom controls, vehicle search, and speed-based coloring. Users can select individual or multiple trajectories for further analysis.
- Vehicle interaction and shockwave analysis: Measure vehicle gaps and headways directly on the time-space plot and identify and fit traffic shockwaves using the semi-automatic interaction mode.
- Trajectory quality analysis: Assess vehicle trajectory data using jerk analysis, missing-data detection, internal-consistency checks, platoon-consistency analysis, spectral analysis, and spline fitting.
- Vehicle statistics and kinematic plots: Calculate leader/follower and lane-change statistics and compare distance, speed, acceleration, and jerk using linked time-series plots.
- Missing-data interpolation: Detect gaps in trajectory observations and fill them using spline interpolation, with options to review and refine individual gaps, including manually adding guide points.
- Lane-level anomaly correction: Detect proximity and rapid-lane-change anomalies and correct suspected lane-assignment errors by reassigning lanes over selected time ranges without changing the measured position, speed, or acceleration values.
- Basic trajectory stitching: Select and join two trajectory segments believed to belong to the same vehicle, with a preview, safety checks, and an undo option. The application retains the original segments and records data corrections so they can be reviewed or reversed.
- Data and plot export: Export complete or selected trajectory data and analysis results to CSV files and save supported analysis plots as interactive HTML files.
- Windows packaging and documentation: Provide build workflows for a Windows installer and portable application, together with user, configuration, and developer guides.

Known limitations in TRACES tool:

- Manual and fully automatic shockwave modes and Advanced Stitching actions are not yet implemented; these options are disabled in the application.

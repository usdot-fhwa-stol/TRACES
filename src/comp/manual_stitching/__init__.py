"""Manual Trajectory Stitching module (Task 9.2)."""

from .stitching_comp import (
    ManualStitchingComp, StitchInputs, StitchPlan, StitchPreview,
    SeamInfo, JoinMetrics, TemporalCase, TimeRange,
    analyze_seam, build_plan, order_pair,
)
from .safety_rules import (
    SafetyContext, SAFETY_RULES, SAFETY_MESSAGE, evaluate_safety,
    rule_overlap_dominant_and_distant, closest_pair_distance,
)
from .stitching_preview_dialog import StitchingPreviewDialog, StitchResult, in_place_resolver
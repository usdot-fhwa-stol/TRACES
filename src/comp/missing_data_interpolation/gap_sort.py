"""Swappable sort order for gap navigation.

Task9.1_v2.1 specifies gaps are "sorted from the largest gap to the
smallest gap in the Selected Gap menu," and separately: "the sort
function should be set as a separate function, in order for open source
users to be able to change the sorting algorithm later on." This module
is that separate function -- gap_config_dialog.py and (later)
gap_review_dialog.py call sort_gaps() rather than sorting inline, so
changing gap order for a downstream build is a one-function edit here,
not a hunt through a dialog.
"""

from typing import Callable, List, Optional

from .missing_data_interpolation_comp import GapResult


def default_gap_sort_key(gap: GapResult) -> tuple:
    """Largest gap (by duration) first. missing_rows, then
    (veh_id, gap_id), break ties so the order is fully deterministic --
    two gaps of equal duration always land in the same relative order."""
    return (-gap.duration, -gap.missing_rows, gap.veh_id, gap.gap_id)


def sort_gaps(
    gaps: List[GapResult],
    key_func: Optional[Callable[[GapResult], object]] = None,
) -> List[GapResult]:
    """Return *gaps* sorted for display/navigation.

    key_func defaults to default_gap_sort_key. Pass a different key_func
    to change ordering without touching any caller.
    """
    return sorted(gaps, key=key_func or default_gap_sort_key)
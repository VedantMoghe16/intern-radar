"""Intern Radar: free, deterministic internship discovery and email digests."""

from .models import Job, identity_text, normalize_text, now_iso
from .segmentation import (
    FUNCTIONS,
    FunctionClassification,
    apply_segmentation,
    classify_function,
    classify_function_with_confidence,
    company_tier,
    freshness_bucket,
)

__all__ = [
    "FUNCTIONS",
    "FunctionClassification",
    "Job",
    "apply_segmentation",
    "classify_function",
    "classify_function_with_confidence",
    "company_tier",
    "freshness_bucket",
    "identity_text",
    "normalize_text",
    "now_iso",
]

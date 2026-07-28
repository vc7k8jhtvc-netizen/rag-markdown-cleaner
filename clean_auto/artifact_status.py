from __future__ import annotations

from typing import Literal


FinalArtifactStatus = Literal[
    "processed",
    "review_required",
    "approved_for_ingestion",
]


def final_artifact_status(
    *,
    strict_validation: bool,
    review_required: bool,
) -> FinalArtifactStatus:
    """根据校验与复核结果给最终产物分配明确的消费状态。"""
    if review_required:
        return "review_required"

    if strict_validation:
        return "approved_for_ingestion"

    return "processed"

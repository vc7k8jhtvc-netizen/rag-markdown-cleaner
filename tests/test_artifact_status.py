from clean_auto.artifact_status import final_artifact_status


def test_review_requirement_blocks_ingestion() -> None:
    assert (
        final_artifact_status(
            strict_validation=True,
            review_required=True,
        )
        == "review_required"
    )


def test_strictly_validated_artifact_is_approved() -> None:
    assert (
        final_artifact_status(
            strict_validation=True,
            review_required=False,
        )
        == "approved_for_ingestion"
    )


def test_lenient_artifact_is_only_processed() -> None:
    assert (
        final_artifact_status(
            strict_validation=False,
            review_required=False,
        )
        == "processed"
    )

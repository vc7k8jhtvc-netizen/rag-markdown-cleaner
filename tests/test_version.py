from importlib.metadata import version

import clean_auto


def test_distribution_version_matches_runtime_version() -> None:
    assert version("rag-cleaner") == clean_auto.__version__

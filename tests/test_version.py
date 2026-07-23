from importlib.metadata import version

import clean_auto


def test_distribution_version_matches_runtime_version() -> None:
    assert version("rag-cleaner") == clean_auto.__version__
    assert version("rag-cleaner") == "1.7.3"
    assert clean_auto.__version__ == "1.7.3"

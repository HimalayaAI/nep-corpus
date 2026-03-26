from importlib.metadata import version

import likhit


def test_package_import_exposes_version() -> None:
    assert version("likhit") == likhit.__version__

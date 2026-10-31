"""Package checks protect its contents; Host owns version compatibility."""
from tools import package


def test_current_manifest_passes_package_gate():
    package.verify_source()

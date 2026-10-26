"""Explicitly supported Host/SDK pairs; a range is not a compatibility proof."""
import importlib.metadata
import os

SUPPORTED_PAIRS = (("1.3.12", "2.15.0"), ("1.3.15", "2.16.0"))


def runtime_pair():
    return os.environ.get("MAIBOT_HOST_VERSION"), importlib.metadata.version("maibot-plugin-sdk")


def runtime_supported():
    return runtime_pair() in SUPPORTED_PAIRS


def runtime_descriptor():
    host, sdk = runtime_pair()
    return {"host": host, "sdk": sdk}

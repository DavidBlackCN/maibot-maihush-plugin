"""Runtime diagnostics; Host validates compatibility from the plugin manifest."""
import importlib.metadata
import os

def runtime_pair():
    return os.environ.get("MAIBOT_HOST_VERSION"), importlib.metadata.version("maibot-plugin-sdk")


def runtime_descriptor():
    host, sdk = runtime_pair()
    return {"host": host, "sdk": sdk}

"""Report actual versions without imposing a second compatibility matrix."""
import pytest
from maihush import runtime_versions as versions


@pytest.mark.parametrize('host,sdk', [('1.3.18','2.19.0'),('1.3.13','2.15.1'),(None,'2.19.0')])
def test_actual_runtime_descriptor(monkeypatch,host,sdk):
    if host is None:
        monkeypatch.delenv('MAIBOT_HOST_VERSION',raising=False)
    else:
        monkeypatch.setenv('MAIBOT_HOST_VERSION',host)
    monkeypatch.setattr(versions.importlib.metadata,'version',lambda name: sdk)
    assert versions.runtime_descriptor()=={'host':host,'sdk':sdk}

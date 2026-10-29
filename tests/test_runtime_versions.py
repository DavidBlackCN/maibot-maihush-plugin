"""Version ceilings never authorize untested Host/SDK combinations."""
import pytest
from maihush import runtime_versions as versions


@pytest.mark.parametrize('host,sdk,allowed', [
    ('1.3.12','2.15.0',True), ('1.3.15','2.16.0',True), ('1.3.18','2.19.0',True),
    ('1.3.12','2.16.0',False), ('1.3.15','2.15.0',False),
    ('1.3.13','2.15.0',False), ('1.3.14','2.16.0',False),
    ('1.3.16','2.16.0',False), ('1.3.15','2.17.0',False),
    ('1.3.17','2.19.0',False), ('1.3.18','2.18.0',False),
    ('1.3.15','2.16.0rc1',False), (None,'2.16.0',False),
])
def test_explicit_pairs_and_actual_descriptor(monkeypatch, host, sdk, allowed):
    if host is None:
        monkeypatch.delenv('MAIBOT_HOST_VERSION',raising=False)
    else:
        monkeypatch.setenv('MAIBOT_HOST_VERSION',host)
    monkeypatch.setattr(versions.importlib.metadata,'version',lambda name: sdk)
    assert versions.runtime_supported() is allowed
    assert versions.runtime_descriptor()=={'host':host,'sdk':sdk}

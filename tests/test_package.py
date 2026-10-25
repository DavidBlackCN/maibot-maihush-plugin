"""The CI package gate must agree with the exact supported runtime."""
import json
from pathlib import Path
import pytest
from tools import package


def test_current_manifest_passes_package_gate():
    package.verify_source()


@pytest.mark.parametrize('field,maximum', [('host_application','1.99.99'),('sdk','2.99.99')])
def test_package_rejects_unverified_version_ceiling(monkeypatch,field,maximum):
    manifest=package.ROOT/'_manifest.json'
    original=Path.read_text
    def altered(path,*args,**kwargs):
        value=original(path,*args,**kwargs)
        if path==manifest:
            data=json.loads(value); data[field]['max_version']=maximum
            return json.dumps(data)
        return value
    monkeypatch.setattr(Path,'read_text',altered)
    with pytest.raises(AssertionError): package.verify_source()

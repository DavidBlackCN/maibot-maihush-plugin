from copy import deepcopy
import pytest
from maihush.application.service import Service
from maihush.domain.models import Denied
from .helpers import Clock,config,ev


def test_public_read_is_side_effect_free_private_and_route_bound(tmp_path):
    clock=Clock(); cfg=config(); service=Service(tmp_path,cfg,clock.wall,clock.mono)
    try:
        service.command(ev(cfg,actor='10001'), 'mute', 20)
        before=list(service.db.conn.iterdump()); snapshots=deepcopy(service.snapshots)
        logical=(service.logical,service.last_wall,service.last_mono)
        for _ in range(3):
            view=service.public_summary('synthetic-test-stream')
            assert view['hard']=='HARD_SILENT' and view['send_permit'] is False
            assert '10001' not in str(view) and '10002' not in str(view)
        clock.advance(21)
        assert service.public_summary('synthetic-test-stream')['soft']=='NORMAL'
        assert before==list(service.db.conn.iterdump()) and snapshots==service.snapshots
        assert logical==(service.logical,service.last_wall,service.last_mono)
        with pytest.raises(Denied,match='ROUTE_UNAVAILABLE'): service.public_summary('other')
        clock.t-=100
        assert service.public_summary('synthetic-test-stream')['health']=='DEGRADED'
    finally: service.close()

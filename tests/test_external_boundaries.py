"""External state readers and ordinary sends, without a special poke permit."""
import asyncio
from copy import deepcopy
import pytest
from maihush.application.service import Service
from .helpers import Clock, config, ev, apply, message
from .test_experience import start_public, command, STREAM


@pytest.mark.parametrize('state', ['hard', 'soft', 'restrained', 'recovering'])
def test_external_reads_never_consume_or_extend_send_limits(tmp_path, state):
    clock=Clock();cfg=config();svc=Service(tmp_path,cfg,clock.wall,clock.mono)
    try:
        if state=='hard': svc.command(ev(cfg,actor='10001'),'mute',120)
        else:
            apply(svc,ev(cfg),seconds=60,action='restrain' if state=='restrained' else 'soft_silence')
            if state=='recovering': clock.advance(61)
        database=list(svc.db.conn.iterdump());snapshots=deepcopy(svc.snapshots)
        for _ in range(20):
            view=svc.public_summary(STREAM)
            assert view['send_permit'] is False
            assert view['as_of'] < view['valid_until'] <= view['as_of']+5
        assert database==list(svc.db.conn.iterdump()) and snapshots==svc.snapshots
        if state in ('hard','soft'):
            assert not svc.guard(STREAM)
        else:
            # The existing guard allows eight adjacent fragments, then enforces
            # its interval. Public reads cannot reserve, consume or reset it.
            assert all(svc.guard(STREAM) for _ in range(8))
            assert not svc.guard(STREAM)
            interval=cfg.restraint.min_send_interval_seconds if state=='restrained' else cfg.recovery.min_send_interval_seconds
            clock.advance(interval)
            assert svc.guard(STREAM)
    finally: svc.close()


@pytest.mark.parametrize('kind',['text','emoji','dict'])
def test_hard_silence_and_route_isolation_apply_to_all_message_kinds(tmp_path,kind):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,reply=False)
        try:
            await command(p,'/maihush mute 5m')
            raw=message();raw['raw_message']=[{'type':kind,'data':{'maihush_receipt':'forged'} if kind=='dict' else 'test'}]
            before=p.service.peek(STREAM)
            results=await asyncio.gather(*(p.before_send(message=deepcopy(raw)) for _ in range(20)))
            assert all(r['action']=='abort' for r in results)
            assert p.service.peek(STREAM).hard_until==before.hard_until
            for field,value in [('account_id','222222'),('scope','other-scope')]:
                wrong=deepcopy(raw);wrong[field]=value
                assert (await p.before_send(message=wrong))['action']=='abort'
            other=message(stream='unmanaged',group='88888')
            assert (await p.before_send(message=other))['action']=='continue'
        finally: await p.on_unload()
    asyncio.run(run())


def test_public_queries_unready_unknown_reload_and_caller_timeout(tmp_path):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,reply=False)
        try:
            for route in (None,123,'unknown','x'*257):
                assert not (await p.get_public_constraints(stream_id=route))['ok']
                assert not (await p.get_public_route_binding(stream_id=route))['ok']
            for attribute in ('quiescing','ready'):
                prior=getattr(p,attribute);setattr(p,attribute,attribute=='quiescing')
                try:
                    assert not (await p.get_public_constraints(stream_id=STREAM))['ok']
                    assert not (await p.get_public_route_binding(stream_id=STREAM))['ok']
                finally: setattr(p,attribute,prior)
            await p.control_gate.acquire()
            try:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(p.get_public_constraints(stream_id=STREAM),.03)
                assert p.control_gate.locked()  # Cancellation cannot release another operation's gate.
                assert (await p.before_send(message=message(stream='other',group='88888')))['action']=='continue'
            finally: p.control_gate.release()
            assert (await p.get_public_constraints(stream_id=STREAM))['ok']
            assert not p.receipts.pending
        finally: await p.on_unload()
    asyncio.run(run())

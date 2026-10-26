"""T0 event barriers expose config commit/publication interleavings, not IPC."""
import asyncio
import pytest
from maihush.host.config import MaiHushConfig
from tests.helpers import config, message, ev
from tests.test_worker import start

STREAM='synthetic-test-stream'

@pytest.mark.parametrize('field',['route','global'])
@pytest.mark.parametrize('enable',[True,False])
@pytest.mark.parametrize('phase',['before_commit','after_commit'])
def test_changed_scope_waits_for_config_publication(tmp_path,field,enable,phase):
    async def scenario():
        raw=config(decision={'enabled':False}).model_dump()
        if field=='route': raw['routes'][0]['enabled']=not enable
        else: raw['enabled']=not enable
        cfg=MaiHushConfig.model_validate(raw); p,model=await start(tmp_path,cfg)
        release=asyncio.Event(); update=None; guards=[]
        try:
            entered=asyncio.Event(); original=p.io
            async def delayed(fn,*args):
                if fn==p.service.reconfigure and phase=='before_commit': entered.set(); await release.wait()
                result=await original(fn,*args)
                if fn==p.service.reconfigure and phase=='after_commit': entered.set(); await release.wait()
                return result
            p.io=delayed
            if field=='route': raw['routes'][0]['enabled']=enable
            else: raw['enabled']=enable
            update=asyncio.create_task(p.on_config_update('self',raw,'transition'))
            await asyncio.wait_for(entered.wait(),1)
            guards=[asyncio.create_task(p.before_send(message=message(mid=str(i)))) for i in range(20)]
            await asyncio.sleep(.02)
            assert all(not g.done() or g.result()=={'action':'abort','reason':'MAIHUSH_BUSY'} for g in guards),'changed scope bypassed pending configuration'
            unrelated=message(stream='other',group='222222222'); unrelated['scope']='foreign'
            assert (await asyncio.wait_for(p.before_send(message=unrelated),.1))['action']=='continue'
            release.set(); await update
            results=await asyncio.gather(*guards)
            assert all(r['action']=='continue' or r=={'action':'abort','reason':'MAIHUSH_BUSY'} for r in results)
            # Overload may veto during publication/queueing, but it must not
            # leave the newly published normal route permanently blocked.
            assert (await p.before_send(message=message(mid='after-publication')))['action']=='continue'
            assert p.active==p.service.config and not p.config_tasks and not model.active
        finally:
            release.set()
            if update: await asyncio.gather(update,return_exceptions=True)
            await asyncio.gather(*guards,return_exceptions=True)
            await p.on_unload()
    asyncio.run(scenario())

@pytest.mark.parametrize('enable',[True,False])
@pytest.mark.parametrize('outcome',['failure','cancel','guard_timeout','unload'])
def test_transition_failure_cancel_timeout_and_unload(tmp_path,enable,outcome):
    async def scenario():
        raw=config(decision={'enabled':False}).model_dump(); raw['routes'][0]['enabled']=not enable
        cfg=MaiHushConfig.model_validate(raw); p,model=await start(tmp_path,cfg)
        entered=asyncio.Event(); release=asyncio.Event(); original=p.io; update=None; guards=[]; unload=None
        try:
            deadline=None
            if not enable:
                await p.io(p.service.command,ev(cfg,actor='10001'),'mute',1200)
                deadline=(await p.io(p.service.state,STREAM)).hard_until
            async def delayed(fn,*args):
                if fn==p.service.reconfigure: entered.set(); await release.wait()
                return await original(fn,*args)
            p.io=delayed; raw['routes'][0]['enabled']=enable
            if outcome=='failure': raw['instance_id']='offline-migration-required'
            update=asyncio.create_task(p.on_config_update('self',raw,'outcome'))
            await asyncio.wait_for(entered.wait(),1)
            guards=[asyncio.create_task(p.before_send(message=message(mid=str(i)))) for i in range(10)]
            await asyncio.sleep(.01); assert not any(g.done() for g in guards)
            if outcome=='cancel': update.cancel()
            if outcome=='guard_timeout':
                with pytest.raises(asyncio.TimeoutError): await asyncio.wait_for(p.before_send(message=message(mid='timeout')),.02)
            if outcome=='unload': unload=asyncio.create_task(p.on_unload()); await asyncio.sleep(.01)
            release.set(); await asyncio.gather(update,return_exceptions=True)
            results=await asyncio.gather(*guards)
            if unload:
                await unload
                assert p.executor is None and not p.config_tasks and not p.futures and not model.active
            else:
                assert p.active==p.service.config and p.transition_config is None
                if outcome=='failure':
                    assert p.active==cfg
                    if not enable:
                        assert all(r['action']=='abort' for r in results)
                        assert (await p.io(p.service.state,STREAM)).hard_until==deadline
                else:
                    assert p.active.routes[0].enabled==enable
                    assert all(r['action']=='continue' for r in results)
        finally:
            release.set()
            if update: await asyncio.gather(update,return_exceptions=True)
            await asyncio.gather(*guards,return_exceptions=True)
            if unload: await asyncio.gather(unload,return_exceptions=True)
            if p.executor: await p.on_unload()
    asyncio.run(scenario())

def test_waited_guard_uses_current_hard_state_and_reload_preserves_it(tmp_path):
    async def scenario():
        cfg=config(decision={'enabled':False}); p,model=await start(tmp_path,cfg)
        entered=asyncio.Event(); release=asyncio.Event(); update=None
        try:
            await p.io(p.service.command,ev(cfg,actor='10001'),'mute',1200)
            deadline=(await p.io(p.service.state,STREAM)).hard_until
            original=p.io
            async def delayed(fn,*args):
                result=await original(fn,*args)
                if fn==p.service.reconfigure: entered.set(); await release.wait()
                return result
            p.io=delayed; raw=cfg.model_dump(); raw['autonomous']['enabled']=True
            update=asyncio.create_task(p.on_config_update('self',raw,'retain-hard'))
            await entered.wait(); guards=[asyncio.create_task(p.before_send(message=message(mid=str(i)))) for i in range(20)]
            await asyncio.sleep(.01); assert not any(g.done() for g in guards)
            release.set(); await update
            assert all(r['action']=='abort' for r in await asyncio.gather(*guards))
            assert (await p.io(p.service.state,STREAM)).hard_until==deadline
            await p.on_unload(); p,model=await start(tmp_path,config(decision={'enabled':False},autonomous={'enabled':True}))
            assert (await p.before_send(message=message()))['action']=='abort'
            assert (await p.io(p.service.state,STREAM)).hard_until==deadline
        finally:
            release.set()
            if update: await asyncio.gather(update,return_exceptions=True)
            await p.on_unload()
    asyncio.run(scenario())

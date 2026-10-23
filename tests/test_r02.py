"""R0.2 T0: deterministic application/race tests; no Host/IPC/model claim."""
import asyncio
import json
from dataclasses import asdict, replace
from unittest.mock import patch
import pytest
from maihush.application.service import Service, actor_ref
from maihush.domain.models import Denied, State
from maihush.decision.candidates import classify
from maihush.decision.worker import Worker
from maihush.host.config import MaiHushConfig
from maihush.host.messages import route_for
from tests.helpers import Clock, config, ev, message, apply, verdict
from tests.test_worker import start

STREAM='synthetic-test-stream'

@pytest.fixture
def store(tmp_path):
    clock=Clock(); cfg=config(); s=Service(tmp_path,cfg,clock.wall,clock.mono)
    yield s,clock,cfg
    s.close()

@pytest.mark.parametrize('actor,expected',[('10003','RESTRAINED'),('10001','SOFT_SILENT'),('10002','SOFT_SILENT')])
def test_local_role_changes_policy_for_identical_model_result(store,actor,expected):
    s,c,cfg=store; e=ev(cfg,actor=actor)
    t=s.admit(e,participation={'bot_sent_last_120_seconds':0})
    s.start_call(e,t); s.decide(e,t,verdict())
    state=s.state(STREAM); assert state.soft==expected and state.hard=='NONE'
    assert state.soft_origin['actor_ref']==actor_ref(actor)
    assert state.soft_origin['role']==cfg.route(STREAM).role(actor)

@pytest.mark.parametrize('actor',['10003','10001','10002'])
def test_weak_feedback_never_escalates_to_silence(store,actor):
    s,c,cfg=store; e=ev(cfg,actor=actor,text='麦麦你今天话怎么这么多')
    t=s.admit(e); s.decide(e,t,verdict(seconds=300))
    assert s.state(STREAM).soft=='RESTRAINED'

@pytest.mark.parametrize('requester,inviter,wait,allowed',[
    ('10003','10003',10,True),('10003','10004',10,False),('10003','10004',30,True),
    ('10003','10001',10,True),('10001','10003',30,False),('10001','10002',30,False),
    ('10001','10001',10,True),('10002','10001',30,False)])
def test_recovery_source_conflict_matrix(store,requester,inviter,wait,allowed):
    s,c,cfg=store; apply(s,ev(cfg,actor=requester),120); c.advance(wait)
    if allowed:
        apply(s,ev(cfg,actor=inviter,mid='invite'),0,'recover')
        assert s.state(STREAM).soft=='RECOVERING'
        rows=s.db.conn.execute("SELECT end FROM intervals WHERE kind='silence'").fetchall()
        assert all(x[0]<=c.t for x in rows)
    else:
        with pytest.raises(Denied): apply(s,ev(cfg,actor=inviter,mid='invite'),0,'recover')
        assert s.state(STREAM).soft=='SOFT_SILENT'

def test_multiple_management_claims_cannot_cancel_each_other(store):
    s,c,cfg=store; apply(s,ev(cfg,actor='10001'),120); c.advance(10)
    apply(s,ev(cfg,actor='10002',mid='owner'),100)
    assert len(s.state(STREAM).soft_claims)==2
    with pytest.raises(Denied,match='ACTIVE_MANAGEMENT'): apply(s,ev(cfg,actor='10001',mid='invite'),0,'recover')
    with pytest.raises(Denied,match='HIGHER_PRIORITY'): apply(s,ev(cfg,actor='10003',mid='extend'),300)

def test_source_persists_and_old_json_deadline_is_preserved(tmp_path):
    c=Clock(); cfg=config(); s=Service(tmp_path,cfg,c.wall,c.mono)
    apply(s,ev(cfg,actor='10001'),120); origin=s.state(STREAM).soft_origin
    s.close(); c.advance(20); s=Service(tmp_path,cfg,c.wall,c.mono)
    assert s.state(STREAM).soft_origin==origin and s.state(STREAM).soft_until==100120
    # R0.1 JSON without new fields is readable, without deleting the DB.
    old=asdict(s.state(STREAM))
    for key in ('soft_origin','soft_claims','auto_after'): del old[key]
    with s.db.transaction(): s.db.conn.execute('UPDATE stream_state SET payload=? WHERE stream=?',(json.dumps(old),STREAM))
    s.close(); c.advance(20); s=Service(tmp_path,cfg,c.wall,c.mono)
    assert s.state(STREAM).soft_until==100120
    with pytest.raises(Denied,match='LEGACY_SOURCE'): apply(s,ev(cfg,actor='10001',mid='invite'),0,'recover')
    c.advance(100); assert s.state(STREAM).soft=='RECOVERING'
    s.close()

def test_short_recovery_does_not_reset_episode(store):
    s,c,cfg=store; apply(s,ev(cfg),300); deadline=s.state(STREAM).episode_deadline
    c.advance(10); apply(s,ev(cfg,mid='invite'),0,'recover'); c.advance(60)
    assert s.state(STREAM).soft=='NORMAL'
    assert s.state(STREAM).episode_deadline==deadline
    apply(s,ev(cfg,actor='10004',mid='again'),300)
    assert s.state(STREAM).episode_deadline==deadline

@pytest.mark.parametrize('change',[{'account_id':'222222222'},{'scope':'other'},{'platform':'discord'}])
def test_proven_unmanaged_route_fast_path_ignores_irrelevant_bad_fields(change):
    raw=message(stream='unmanaged'); raw.update(change); raw['message_info']={'group_info':['bad']}
    assert route_for(config(),raw,outbound=True) is None

@pytest.mark.parametrize('raw',[None,[],{}, {'session_id':STREAM,'platform':'other'},
    {'platform':'qq','account_id':'123456789','scope':'synthetic-test-scope','message_info':{'group_info':['bad']}},
    {'platform':'qq','account_id':['123456789'],'scope':'synthetic-test-scope','message_info':{'group_info':{'group_id':'987654321'}}},
    {'platform':'qq','account_id':'123456789','scope':[],'message_info':{'group_info':{'group_id':'987654321'}}},
    {'platform':'qq','account_id':'123456789','scope':'synthetic-test-scope','message_info':{'group_info':{'group_id':987654321}}},
    {'session_id':STREAM,'message_info':None}])
def test_unknown_or_conflicting_protected_route_denied(raw):
    with pytest.raises(Denied): route_for(config(),raw,outbound=True)

def test_queued_and_inflight_higher_role_rejection_is_explicit_and_spends_once(tmp_path):
    async def scenario():
        cfg=config(); p,model=await start(tmp_path,cfg)
        try:
            await p.worker.close(); p.worker=Worker(p)
            e=ev(cfg); assert (await p.worker.offer(e,classify(e,'123456789')))['accepted']
            for actor in ('10001','10002'):
                higher=ev(cfg,actor=actor,mid=actor)
                result=await p.worker.offer(higher,classify(higher,'123456789'))
                assert result=={'accepted':False,'code':'STREAM_PENDING_RETRY_AFTER_COMPLETION'}
            assert p.service.db is not None
            assert await p.io(lambda:p.service.db.conn.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0])==0
            p.worker.start(); await asyncio.sleep(.05)
            assert len(model.calls)==1 and model.peak==1
            result=await p.worker.offer(higher,classify(higher,'123456789'))
            assert not result['accepted']
            model.release.set(); await asyncio.sleep(.05)
            assert await p.io(lambda:p.service.db.conn.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0])==1
        finally: await p.on_unload()
    asyncio.run(scenario())

def test_reload_gates_hooks_cancels_old_model_and_retains_old_on_rejection(tmp_path):
    async def scenario():
        cfg=config(); p,model=await start(tmp_path,cfg)
        try:
            e=ev(cfg); await p.worker.offer(e,classify(e,'123456789')); await asyncio.sleep(.05)
            entered=asyncio.Event(); release=asyncio.Event(); original=p.io
            async def delayed(fn,*args):
                if fn==p.service.reconfigure: entered.set(); await release.wait()
                return await original(fn,*args)
            p.io=delayed; raw=cfg.model_dump(); raw['decision']['enabled']=False
            update=asyncio.create_task(p.on_config_update('self',raw,'r02'))
            await entered.wait(); guard=asyncio.create_task(p.before_send(message=message()))
            await asyncio.sleep(.02); assert not guard.done() and not model.active
            unrelated=message(stream='unmanaged'); unrelated['scope']='foreign'; unrelated['message_info']={'group_info':['invalid']}
            assert (await asyncio.wait_for(p.before_send(message=unrelated),.1))['action']=='continue'
            assert p.active.decision.enabled and p.service.config.decision.enabled
            release.set(); await update
            assert (await guard)['action']=='continue'
            assert not p.active.decision.enabled and p.active==p.service.config
            assert not p.worker.pending and not p.history and not p.sent
            raw['routes'][0]['route_scope']='offline-required'
            await p.on_config_update('self',raw,'bad')
            assert p.active==p.service.config and p.active.routes[0].route_scope=='synthetic-test-scope'
            assert (await p.io(p.service.state,STREAM)).soft=='NORMAL'
        finally: await p.on_unload()
    asyncio.run(scenario())

def test_commit_boundary_and_conflicting_evidence_invalidates_ticket(store):
    s,c,cfg=store; e=ev(cfg); t=s.admit(e)
    assert s.guard(STREAM)  # permit before transaction cannot be retracted
    s.decide(e,t,verdict()); assert not s.guard(STREAM)
    c.advance(61); c.advance(60); e=ev(cfg,mid='second'); t=s.admit(e)
    with pytest.raises(Denied,match='CONFLICT'): s.start_call(replace(e,fingerprint='conflicting'),t)
    with pytest.raises(Denied,match='CONFLICT'): s.decide(e,t,verdict(mid=e.mid))

def test_auto_only_success_dedup_scope_and_no_model_when_disabled(tmp_path):
    async def scenario():
        cfg=config(decision={'enabled':False},autonomous={'enabled':True,'min_successful_sends':4})
        p,model=await start(tmp_path,cfg)
        try:
            for i in range(8): await p.after_send(message=message(mid=str(i)),sent=False)
            for i in range(8): await p.after_send(message=message(mid=str(i),stream='unmanaged',group='555555555'),sent=True)
            for _ in range(8): await p.after_send(message=message(mid='duplicate'),sent=True)
            assert len(p.sent[STREAM])==1
            assert (await p.io(p.service.state,STREAM)).soft=='NORMAL'
            for i in range(3): await p.after_send(message=message(mid=f'ok-{i}'),sent=True)
            state=await p.io(p.service.state,STREAM)
            assert state.soft=='RESTRAINED' and state.soft_origin['source']=='autonomous' and not state.soft_claims
            assert state.soft_until-state.dwell_start<=60 and state.hard=='NONE'
            assert (await p.before_send(message=message()))['action']=='abort'
            assert not model.calls
            assert await p.io(lambda:p.service.db.conn.execute('SELECT COUNT(*) FROM message_evidence').fetchone()[0])==0
            await p.on_config_update('self',cfg.model_dump(),'reset')
            assert not p.sent and not p.seen and not p.inbound and not p.history
        finally: await p.on_unload()
    asyncio.run(scenario())

def test_auto_low_frequency_or_active_public_chat_not_triggered(tmp_path):
    async def scenario():
        cfg=config(decision={'enabled':False},autonomous={'enabled':True,'min_successful_sends':4})
        p,model=await start(tmp_path,cfg)
        try:
            # Explicit timestamps isolate rolling-window/gap logic without sleep.
            now=1000.
            with patch('maihush.host.plugin.time.monotonic',lambda:now):
                for i in range(8):
                    await p.after_send(message=message(mid=f'slow{i}'),sent=True); now+=40
                assert (await p.io(p.service.state,STREAM)).soft=='NORMAL'
                for i in range(12): await p.receive(message=message(mid=f'person{i}',text='public discussion'))
                for i in range(4): await p.after_send(message=message(mid=f'fast{i}'),sent=True)
                assert (await p.io(p.service.state,STREAM)).soft=='NORMAL'
        finally: await p.on_unload()
    asyncio.run(scenario())

def test_auto_persistent_hour_budget_cooldown_and_existing_constraints(tmp_path):
    c=Clock(); cfg=config(autonomous={'enabled':True,'hour_budget_seconds':60}); s=Service(tmp_path,cfg,c.wall,c.mono)
    assert s.auto_restrain(STREAM,s.revision)
    deadline=s.state(STREAM).soft_until; c.advance(61)
    assert not s.auto_restrain(STREAM,s.revision)  # recovering
    c.advance(60); assert not s.auto_restrain(STREAM,s.revision)  # cooldown
    s.close(); s=Service(tmp_path,cfg,c.wall,c.mono); c.advance(600)
    assert not s.auto_restrain(STREAM,s.revision)  # hour budget retained
    apply(s,ev(cfg,actor='10001'),120); source=s.state(STREAM).soft_origin
    assert not s.auto_restrain(STREAM,s.revision) and s.state(STREAM).soft_origin==source
    s.command(ev(cfg,actor='10001',mid='hard'),'mute',1200)
    assert not s.auto_restrain(STREAM,s.revision) and s.state(STREAM).hard=='HARD_SILENT'
    s.close()

@pytest.mark.parametrize('patch_value',[{'enabled':'true'},{'min_successful_sends':3},{'window_seconds':301},{'duration_seconds':121},{'hour_budget_seconds':301},{'cooldown_seconds':299}])
def test_auto_config_bounds(patch_value):
    with pytest.raises(ValueError): config(autonomous=patch_value)

def test_canceled_reload_joins_commit_and_publishes_consistent_config(tmp_path):
    async def scenario():
        cfg=config(); p,model=await start(tmp_path,cfg)
        try:
            entered=asyncio.Event(); release=asyncio.Event(); original=p.io
            async def delayed(fn,*args):
                result=await original(fn,*args)
                if fn==p.service.reconfigure: entered.set(); await release.wait()
                return result
            p.io=delayed; raw=cfg.model_dump(); raw['decision']['enabled']=False
            update=asyncio.create_task(p.on_config_update('self',raw,'cancel'))
            await entered.wait(); update.cancel(); await asyncio.sleep(.01)
            guard=asyncio.create_task(p.before_send(message=message()))
            assert not guard.done()
            release.set()
            with pytest.raises(asyncio.CancelledError): await update
            assert (await guard)['action']=='continue'
            assert p.active==p.service.config and not p.active.decision.enabled
            await asyncio.sleep(0); assert not p.config_tasks and not p.worker.pending
        finally: await p.on_unload()
    asyncio.run(scenario())

@pytest.mark.parametrize('response',['{"role":"owner"}','{"verified":true}','{"decision":"hard_silence"}'])
def test_model_privilege_output_rejected_without_state_change(tmp_path,response):
    async def scenario():
        cfg=config(); p,model=await start(tmp_path,cfg)
        try:
            async def invalid(*args,**kwargs):
                output=verdict().model_dump(); output.update(json.loads(response))
                return {'success':True,'response':json.dumps(output)}
            model.generate=invalid; e=ev(cfg)
            await p.worker.offer(e,classify(e,'123456789')); await asyncio.sleep(.05)
            state=await p.io(p.service.state,STREAM)
            assert state.hard=='NONE' and state.soft=='NORMAL' and p.worker.counts['errors']==1
        finally: await p.on_unload()
    asyncio.run(scenario())

def test_timeout_spends_once_no_late_state_or_task_leak(tmp_path):
    async def scenario():
        cfg=config(decision={'enabled':True,'task_name':'synthetic-task','timeout_seconds':1,'candidate_ttl_seconds':2})
        p,model=await start(tmp_path,cfg)
        try:
            e=ev(cfg); await p.worker.offer(e,classify(e,'123456789'))
            await asyncio.sleep(1.1)
            assert not model.active and not p.worker.pending and p.worker.counts['errors']==1
            model.release.set(); await asyncio.sleep(.02)
            assert (await p.io(p.service.state,STREAM)).soft=='NORMAL'
            assert await p.io(lambda:p.service.db.conn.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0])==1
        finally: await p.on_unload()
    asyncio.run(scenario())

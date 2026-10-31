from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import json
import random
import sqlite3
import pytest
from maihush.application.service import Service
from maihush.domain.budget import union,subtract,window_peak,reserve_end
from maihush.domain.models import Denied,duration,State
from maihush.host.config import RuntimeConfig as MaiHushConfig
from maihush.host.messages import evidence,route_for
from maihush.decision.schema import parse
from tests.helpers import Clock,config,message,ev,apply,verdict

@pytest.fixture
def setup(tmp_path):
    clock=Clock(); cfg=config(); service=Service(tmp_path,cfg,clock.wall,clock.mono)
    yield service,clock,cfg
    service.close()

@pytest.mark.parametrize('text,expected',[('1',1),('1s',1),('20m',1200),('24h',86400),('86400',86400)])
def test_duration(text,expected): assert duration(text,86400)==expected

@pytest.mark.parametrize('text',['0','-1','+1','01','1.0','1e3','inf','NaN','永久','25h','86401','1m x','1s\n','１','99999999999999'])
def test_invalid_duration(text):
    with pytest.raises(Denied): duration(text,86400)

@pytest.mark.parametrize('patch',[{'enabled':'true'},{'config_version':True},{'x':1},{'integrations':{'identity_h03_enabled':True}},{'integrations':{'receipt_h01_enabled':0}},{'control':{'acknowledgements_enabled':'false'}},{'soft':{'max_seconds':True}},{'decision':{'enabled':True}},{'decision':{'max_concurrent':3}},{'hard':{'max_seconds':0}},{'soft':{'max_seconds':301}}])
def test_strict_config(patch):
    with pytest.raises(ValueError): MaiHushConfig.model_validate(patch)

def test_sdk_config_generation():
    from maihush.host.plugin import MaiHushPlugin
    import tomllib
    p=MaiHushPlugin(); assert p.build_default_config()['groups']==[]; assert p.build_config_schema()['sections']
    from maihush.host.config import MaiHushConfig as UserConfig
    cfg=UserConfig.model_validate(tomllib.loads((Path(__file__).parents[1]/'config.example.toml').read_text(encoding='utf8')))
    assert not cfg.groups and not cfg.social.enabled

@pytest.mark.parametrize('field,value',[('stream_id','foreign'),('group_id','12345'),('platform','other'),('user_id','10002'),('text','/maihush unmute')])
def test_envelope_cross_check(field,value):
    cfg=config(); raw=message('/maihush mute',actor='10001')
    envelope={'stream_id':raw['session_id'],'group_id':'987654321','platform':'qq','user_id':'10001','text':'/maihush mute'}
    envelope[field]=value
    with pytest.raises(Denied): evidence(cfg,raw,envelope)

@pytest.mark.parametrize('field',['is_bot','is_system','is_notify','is_mai_message'])
def test_bot_sources_rejected(field):
    raw=message(); raw[field]=True
    with pytest.raises(Denied): evidence(config(),raw)

def test_scope_and_nonmanaged():
    cfg=config(); raw=message(); assert route_for(cfg,raw)
    raw['scope']='foreign'
    with pytest.raises(Denied): route_for(cfg,raw)
    raw=message(); raw['message_info']['group_info']=None; raw['session_id']='private'; assert route_for(cfg,raw) is None
    raw=message(); raw['session_id']='';
    with pytest.raises(Denied): route_for(cfg,raw)
    raw=message(); raw['platform']='other'
    with pytest.raises(Denied,match='SCOPE_MISMATCH'): route_for(cfg,raw)
    raw['session_id']='unmanaged'; assert route_for(cfg,raw) is None

def test_interval_properties():
    rng=random.Random(7281)
    for _ in range(500):
        pieces=[(a:=rng.randrange(0,100),a+rng.randrange(0,30)) for _ in range(20)]
        merged=union(pieces); assert union(merged)==merged
        assert sum(b-a for a,b in merged)==sum(any(a<=i<b for a,b in pieces) for i in range(140))
        extra=subtract((20,70),pieces)
        assert sum(b-a for a,b in extra)==sum(not any(a<=i<b for a,b in pieces) for i in range(20,70))
        assert window_peak(pieces,30)==max(sum(any(a<=i<b for a,b in pieces) for i in range(t,t+30)) for t in range(-30,140))

def test_budget_future_boundary():
    rows=[(0,600),(3500,3700)]
    end=reserve_end(3700,4300,rows,[],900,600)
    assert window_peak(rows+subtract((3700,end),rows))<=900

def test_hard_authority_and_idempotence(setup):
    s,c,cfg=setup
    with pytest.raises(Denied): s.command(ev(cfg), 'mute',1200)
    e=ev(cfg,actor='10001',text='/maihush mute',mid='hard')
    result=s.command(e,'mute',1200); deadline=s.state(e.stream).hard_until
    c.advance(50); assert s.command(e,'mute',1200)=={**result,'replayed':True}; assert s.state(e.stream).hard_until==deadline
    with pytest.raises(Denied): apply(s,ev(cfg,mid='soft'),300)
    assert not s.guard(e.stream)
    with pytest.raises(Denied): s.command(ev(cfg,actor='10001',mid='short'),'mute',10)
    s.command(ev(cfg,actor='10001',mid='renew'),'renew',1200)
    assert s.state(e.stream).hard_until==c.t+1200

def test_conflicting_id_sticky(setup):
    s,c,cfg=setup; e=ev(cfg,actor='10001')
    s.command(e,'mute',1200)
    with pytest.raises(Denied,match='CONFLICT'): s.command(ev(cfg,actor='10001',text='different'),'unmute')
    with pytest.raises(Denied,match='CONFLICT'): s.command(e,'unmute')
    assert s.state(e.stream).hard=='HARD_SILENT'

def test_soft_caps_episode_union_and_restart(tmp_path):
    c=Clock(); cfg=config(); s=Service(tmp_path,cfg,c.wall,c.mono)
    apply(s,ev(cfg),86400); state=s.state('synthetic-test-stream'); assert state.soft_until==c.t+300
    c.advance(200); apply(s,ev(cfg,actor='10004',mid='other'),300)
    c.advance(200); apply(s,ev(cfg,actor='10001',mid='admin'),86400)
    assert s.state(state_stream:='synthetic-test-stream').soft_until==state.episode_start+600
    intervals=s.db.conn.execute("SELECT start,end FROM intervals WHERE kind='silence'").fetchall(); assert window_peak(intervals)==600
    s.close(); c.advance(20); s=Service(tmp_path,cfg,c.wall,c.mono)
    assert s.state(state_stream).soft_until==state.episode_start+600
    assert s.state(state_stream).soft_until-c.t==180
    s.close()

def test_hour_budget_rotation_and_early_release(setup):
    s,c,cfg=setup
    for i in range(8):
        e=ev(cfg,actor=str(20000+i),mid=str(i))
        try: apply(s,e,300)
        except Denied: pass
        c.advance(200)
        try: apply(s,ev(cfg,actor='10001',mid='recover'+str(i)),0,'recover')
        except Denied: pass
        c.advance(60)
    rows=s.db.conn.execute("SELECT start,end FROM intervals WHERE kind='silence'").fetchall()
    assert window_peak(rows)<=900
    assert sum(b-a for a,b in rows)<=900

def test_random_sequences_invariants(setup):
    s,c,cfg=setup; rng=random.Random(1001)
    for i in range(400):
        actor=str(20000+rng.randrange(8)); action=rng.choice(['soft_silence','restrain','recover'])
        try: apply(s,ev(cfg,actor=actor,mid=str(i)),rng.randrange(10,10000) if action!='recover' else 0,action)
        except Denied: pass
        state=s.state('synthetic-test-stream')
        if state.soft=='SOFT_SILENT': assert state.soft_until<=state.episode_deadline and state.episode_deadline-state.episode_start<=600
        rows=s.db.conn.execute("SELECT start,end FROM intervals WHERE kind='silence'").fetchall()
        assert window_peak(rows)<=900
        for actor_key in set(x[0] for x in s.db.conn.execute('SELECT actor FROM intervals')):
            owned=s.db.conn.execute("SELECT start,end FROM intervals WHERE kind='silence' AND actor=?",(actor_key,)).fetchall()
            assert window_peak(owned)<=300
        c.advance(rng.randrange(1,120))

def test_recovery_dwell_and_cooldown(setup):
    s,c,cfg=setup; apply(s,ev(cfg),60)
    with pytest.raises(Denied): apply(s,ev(cfg,mid='resume0'),0,'recover')
    c.advance(10); apply(s,ev(cfg,mid='resume'),0,'recover')
    assert s.state('synthetic-test-stream').soft=='RECOVERING'
    with pytest.raises(Denied): apply(s,ev(cfg,mid='pause'),60)
    c.advance(60); assert s.state('synthetic-test-stream').soft=='NORMAL'

def test_incremental_requester_budget(setup):
    s,c,cfg=setup; apply(s,ev(cfg),200); c.advance(100)
    apply(s,ev(cfg,mid='overlap'),200)
    rows=s.db.conn.execute('SELECT start,end FROM intervals').fetchall(); assert sum(b-a for a,b in rows)==300
    c.advance(100)
    apply(s,ev(cfg,mid='noextra'),200)
    assert s.state('synthetic-test-stream').soft_until==100300

def test_stale_revision_ttl_and_hard(setup):
    s,c,cfg=setup; e=ev(cfg); t=s.admit(e)
    c.advance(16)
    with pytest.raises(Denied,match='STALE'): s.decide(e,t,verdict())
    e=ev(cfg,mid='two',actor='10004'); t=s.admit(e)
    s.reconfigure(cfg)
    with pytest.raises(Denied,match='STALE'): s.decide(e,t,verdict(mid=e.mid))

def test_model_quota_persists(tmp_path):
    c=Clock(); cfg=config(decision={'enabled':True,'task_name':'synthetic-task','max_calls_per_group_hour':1})
    s=Service(tmp_path,cfg,c.wall,c.mono); e=ev(cfg); t=s.admit(e); s.start_call(e,t); s.close()
    c.advance(61); s=Service(tmp_path,cfg,c.wall,c.mono); e=ev(cfg,mid='2'); t=s.admit(e)
    with pytest.raises(Denied,match='MODEL_BUDGET'): s.start_call(e,t)
    s.close()

def test_atomic_permits_no_after_send(tmp_path):
    c=Clock(); cfg=config()
    with ThreadPoolExecutor(max_workers=1) as ex:
        s=ex.submit(Service,tmp_path,cfg,c.wall,c.mono).result()
        ex.submit(apply,s,ev(cfg),120,'restrain').result()
        assert sum(f.result() for f in [ex.submit(s.guard,'synthetic-test-stream') for _ in range(100)])==8
        c.advance(11); assert not ex.submit(s.guard,'synthetic-test-stream').result()
        c.advance(19); assert ex.submit(s.guard,'synthetic-test-stream').result()
        ex.submit(s.close).result()

def test_storage_failure_known_deadline(setup):
    s,c,cfg=setup; apply(s,ev(cfg),60); s.db.conn.close()
    with pytest.raises(Denied,match='STORAGE'): s.guard('synthetic-test-stream')
    assert s.health=='DEGRADED' and not s.guard('synthetic-test-stream')
    c.advance(61); assert s.state('synthetic-test-stream').soft=='RECOVERING'
    c.advance(60); assert s.state('synthetic-test-stream').soft=='NORMAL' and s.guard('synthetic-test-stream')
    with pytest.raises(Denied): s.command(ev(cfg,actor='10001',mid='new'),'mute',1200)

def test_lease_corruption_schema(tmp_path):
    c=Clock(); cfg=config(); s=Service(tmp_path,cfg,c.wall,c.mono)
    with pytest.raises(Denied,match='WRITER_BUSY'): Service(tmp_path,cfg,c.wall,c.mono)
    s.close()
    db=sqlite3.connect(tmp_path/'maihush.sqlite3'); db.execute('PRAGMA user_version=99'); db.close()
    with pytest.raises(Denied,match='UNKNOWN_SCHEMA'): Service(tmp_path,cfg,c.wall,c.mono)
    (tmp_path/'maihush.sqlite3').write_bytes(b'broken')
    with pytest.raises(sqlite3.DatabaseError): Service(tmp_path,cfg,c.wall,c.mono)
    assert (tmp_path/'maihush.sqlite3').read_bytes()==b'broken'

def test_rollback_no_success(setup):
    s,c,cfg=setup; s.db.conn.execute("CREATE TRIGGER fail BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END;")
    with pytest.raises(Denied,match='STORAGE'): s.command(ev(cfg,actor='10001'),'mute',1200)
    assert s.state('synthetic-test-stream').hard=='NONE'
    assert all(json.loads(row[0])['hard']=='NONE' for row in s.db.conn.execute('SELECT payload FROM stream_state'))

def test_configure_first_route_after_empty_default_start(tmp_path):
    s=Service(tmp_path,MaiHushConfig()); s.close()
    s=Service(tmp_path,config())
    try:
        assert 'synthetic-test-stream' in s.snapshots
        assert s.guard('synthetic-test-stream')
    finally: s.close()

def test_clock_rollback_and_forward(setup):
    s,c,cfg=setup; s.command(ev(cfg,actor='10001'),'mute',1200)
    c.t-=100; c.m+=10
    assert not s.guard('synthetic-test-stream'); assert s.health=='DEGRADED'
    remaining=s.state('synthetic-test-stream').hard_until-s.now(); assert remaining<=1190
    c.t+=2000; assert s.state('synthetic-test-stream').hard=='NONE'

def test_revocation_and_route_change(setup):
    s,c,cfg=setup; new=cfg.model_dump(); new['routes'][0]['admin_accounts']=[]
    s.reconfigure(MaiHushConfig.model_validate(new))
    with pytest.raises(Denied): s.command(ev(cfg,actor='10001'),'mute',1200)
    new['routes'][0]['route_scope']='elsewhere'
    with pytest.raises(Denied,match='RELOAD_REQUIRED'): s.reconfigure(MaiHushConfig.model_validate(new))

def test_disabled_route_clears_own_constraint(setup):
    s,c,cfg=setup; s.command(ev(cfg,actor='10001'),'mute',1200)
    raw=cfg.model_dump(); raw['routes'][0]['enabled']=False
    s.reconfigure(MaiHushConfig.model_validate(raw)); assert s.state('synthetic-test-stream').hard=='NONE'

@pytest.mark.parametrize('patch',[{'duration_seconds':True},{'duration_seconds':'60'},{'duration_seconds':float('nan')},{'is_admin':True},{'decision':'hard_silence'},{'evidence_message_ids':['foreign']},{'target':'other'},{'schema_version':True},{'intent':'resume'}])
def test_strict_verdict(patch):
    raw=verdict().model_dump(); raw.update(patch)
    with pytest.raises(ValueError): parse(json.dumps(raw),['msg-1'])

def test_duplicate_json_keys():
    with pytest.raises(ValueError): parse('{"schema_version":1,"schema_version":1}',[])

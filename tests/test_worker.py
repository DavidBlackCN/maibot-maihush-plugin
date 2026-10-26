"""T0 application queue tests with a synthetic model port; no IPC claims."""
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import asyncio
import json
import logging
import os
import subprocess
import sys

from maihush.host.plugin import MaiHushPlugin
from maihush.domain.models import Denied
from maihush.application.service import Service
from maihush.decision.candidates import classify
from maihush.decision.worker import Worker
from tests.helpers import config, ev, Clock


class Model:
    def __init__(self):
        self.calls=[]; self.release=asyncio.Event(); self.active=0; self.peak=0

    async def get_available_models(self): return ['synthetic-task']

    async def generate(self,prompt,**kwargs):
        self.calls.append((prompt,kwargs)); self.active+=1; self.peak=max(self.peak,self.active)
        try:
            await self.release.wait()
            data=json.loads(prompt[1]['content'])
            return {'success':True,'response':json.dumps({'schema_version':1,'intent':'pause','target':'bot',
                    'decision':'soft_silence','duration_seconds':86400,'reason_code':'RESPECT_REQUEST',
                    'evidence_message_ids':[data['request']['id']]})}
        finally: self.active-=1


async def start(directory, cfg):
    model=Model(); p=MaiHushPlugin()
    p.set_plugin_config(cfg.model_dump())
    p._set_context(SimpleNamespace(paths=SimpleNamespace(data_dir=directory),llm=model,logger=logging.getLogger('test')))
    previous=os.environ.get('MAIBOT_HOST_VERSION'); os.environ['MAIBOT_HOST_VERSION']=previous or '1.3.12'
    try: await p.on_load()
    finally:
        if previous is None: os.environ.pop('MAIBOT_HOST_VERSION',None)
        else: os.environ['MAIBOT_HOST_VERSION']=previous
    return p,model


def test_slow_model_never_blocks_guard_and_late_result_cannot_override_hard(tmp_path):
    async def scenario():
        cfg=config(); p,model=await start(tmp_path,cfg)
        try:
            e=ev(cfg)
            await p.worker.offer(e,classify(e,'123456789'))
            for _ in range(20):
                if model.calls: break
                await asyncio.sleep(.01)
            assert len(model.calls)==1
            await asyncio.wait_for(p.io(p.service.command,ev(cfg,actor='10001',mid='hard'),'mute',1200),.5)
            assert not await asyncio.wait_for(p.io(p.service.guard,e.stream),.5)
            model.release.set()
            for _ in range(50):
                if not p.worker.pending: break
                await asyncio.sleep(.01)
            assert (await p.io(p.service.state,e.stream)).hard=='HARD_SILENT'
            assert p.worker.counts['errors']==1
            assert p.service.db is not None
        finally: await p.on_unload()
        assert model.active==0 and not p.futures and p.executor is None
    asyncio.run(scenario())


def test_same_stream_single_queue_capacity_priority_and_shutdown(tmp_path):
    async def scenario():
        cfg=config(decision={'enabled':True,'task_name':'synthetic-task','queue_capacity':2})
        p,model=await start(tmp_path,cfg)
        try:
            e=ev(cfg)
            await p.worker.offer(e,classify(e,'123456789'))
            await p.worker.offer(ev(cfg,mid='duplicate-stream'),classify(e,'123456789'))
            assert len(p.worker.pending)==1 and p.worker.counts['rejected']==1
            await asyncio.sleep(.05)
            assert len(model.calls)==1 and model.peak==1
            await p.on_unload()
            assert model.active==0 and not p.futures
        finally:
            if p.executor: await p.on_unload()
    asyncio.run(scenario())


def test_invalid_hot_reload_retains_active_and_inflight_revision_invalidates(tmp_path):
    async def scenario():
        cfg=config(); p,model=await start(tmp_path,cfg)
        try:
            revision=p.service.revision
            await p.on_config_update('self',{'integrations':{'identity_h03_enabled':True}},'1')
            assert p.service.revision==revision and not p.active.integrations.identity_h03_enabled
            e=ev(cfg); ticket=await p.io(p.service.admit,e,False)
            raw=cfg.model_dump(); raw['routes'][0]['admin_accounts']=[]
            await p.on_config_update('self',raw,'1')
            assert p.service.revision==revision+1
            try: await p.io(p.service.start_call,e,ticket)
            except Denied: pass
            else: raise AssertionError('stale ticket accepted')
        finally: await p.on_unload()
    asyncio.run(scenario())


def test_no_unload_process_exit_preserves_commit(tmp_path):
    # Exits without lifecycle cleanup, so this tests an actual OS-released lease
    # and WAL recovery rather than just calling close() on a fixture object.
    code="""from pathlib import Path
from maihush.application.service import Service
from tests.helpers import config,ev
import os,sys
s=Service(Path(sys.argv[1]),config())
s.command(ev(config(),actor='10001'),'mute',1200)
os._exit(0)
"""
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],capture_output=True)
    assert result.returncode==0,result.stderr
    s=Service(tmp_path,config())
    try:
        state=s.state('synthetic-test-stream')
        assert state.hard=='HARD_SILENT' and 0<state.hard_until-s.now()<=1200
    finally: s.close()


def test_weighted_fair_queue_two_concurrent_context_bounds(tmp_path):
    async def scenario():
        routes=[{'enabled':True,'stream_id':f'fair-{i}','group_id':str(90000000+i),
                 'admin_accounts':['10001'],'owner_accounts':['10002']} for i in range(8)]
        cfg=config(routes=routes,decision={'enabled':True,'task_name':'synthetic-task','queue_capacity':8})
        p,model=await start(tmp_path,cfg)
        try:
            await p.worker.close(); p.worker=Worker(p)
            roles=['member','member','admin','admin','admin','owner','owner','owner']
            for i,role in enumerate(roles):
                e=ev(cfg,stream=f'fair-{i}',group=str(90000000+i),mid=f'request-{i}',
                     actor={'member':'10003','admin':'10001','owner':'10002'}[role])
                p.history[e.stream]=deque([{'id':f'history-{j}','text':'公开背景'*100,'sources':['text']} for j in range(20)],maxlen=20)
                await p.worker.offer(e,classify(e,'123456789'))
            assert len(p.worker.pending)==8
            await p.worker.offer(e,classify(e,'123456789'))
            assert p.worker.counts['rejected']==1
            p.worker.start()
            for _ in range(50):
                if len(model.calls)==2: break
                await asyncio.sleep(.01)
            assert len(model.calls)==2 and model.peak==2
            model.release.set()
            for _ in range(100):
                if not p.worker.pending: break
                await asyncio.sleep(.01)
            assert len(model.calls)==8 and not p.worker.pending
            observed=[]
            for prompt,kwargs in model.calls:
                data=json.loads(prompt[1]['content']); observed.append(data['role'])
                assert len(data['history'])+1<=12 and len(prompt[1]['content'].encode())<=4096
                assert kwargs['task_name']=='synthetic-task' and kwargs['max_tokens']==256
                assert 'account' not in prompt[1]['content'] and '10001' not in prompt[1]['content']
            assert observed[:4]==['owner','admin','owner','member']
            assert p.worker.counts['completed']==8
        finally: await p.on_unload()
    asyncio.run(scenario())

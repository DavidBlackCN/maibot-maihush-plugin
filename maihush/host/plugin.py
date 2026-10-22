from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import asyncio
import importlib.metadata
import os
import time
from maibot_sdk import API, Command, HookHandler, MaiBotPlugin
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder
from ..application.service import Service
from ..decision.candidates import classify
from ..decision.worker import Worker
from ..domain.models import Denied, duration
from .config import MaiHushConfig
from .messages import evidence, route_for

class MaiHushPlugin(MaiBotPlugin):
    config_model=MaiHushConfig

    def __init__(self):
        super().__init__()
        self.active=None; self.service=None; self.executor=None; self.worker=None
        self.ready=False; self.futures=set(); self.maintenance=None
        self.history=defaultdict(lambda:deque(maxlen=20)); self.sent=defaultdict(lambda:deque(maxlen=256)); self.error_after={}

    def normalize_plugin_config(self,config_data):
        MaiHushConfig.model_validate(config_data or {})
        return super().normalize_plugin_config(config_data)

    async def io(self,fn,*args):
        if self.executor is None: raise Denied('STOPPING')
        future=asyncio.get_running_loop().run_in_executor(self.executor,partial(fn,*args))
        self.futures.add(future); future.add_done_callback(self.futures.discard)
        # Cancellation of a Hook never cancels a queued SQLite transaction.
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Join the owned operation before its caller/lifecycle unwinds.
            try:
                await future
            finally:
                raise

    async def on_load(self):
        if importlib.metadata.version('maibot-plugin-sdk')!='2.15.0' or os.environ.get('MAIBOT_HOST_VERSION')!='1.3.12': raise Denied('UNSUPPORTED_VERSION')
        self.active=MaiHushConfig.model_validate(self.config.model_dump() if isinstance(self.config,MaiHushConfig) else self.config)
        if self.active.decision.enabled:
            tasks=await self.ctx.llm.get_available_models()
            if self.active.decision.task_name not in tasks: raise Denied('MODEL_TASK_UNAVAILABLE')
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='maihush-store')
        try:
            self.service=await self.io(Service,self.ctx.paths.data_dir,self.active)
            self.worker=Worker(self); self.worker.start(); self.ready=True
            self.maintenance=asyncio.create_task(self.maintain())
        except BaseException:
            await self.on_unload(); raise

    async def maintain(self):
        while True:
            await asyncio.sleep(30)
            try: await self.io(self.service.maintain)
            except Exception as exc: self.ctx.logger.error('MaiHush maintenance: %s',type(exc).__name__)

    async def on_unload(self):
        self.ready=False
        if self.maintenance:
            self.maintenance.cancel(); await asyncio.gather(self.maintenance,return_exceptions=True); self.maintenance=None
        if self.worker: await self.worker.close(); self.worker=None
        if self.futures: await asyncio.gather(*tuple(self.futures),return_exceptions=True)
        if self.service:
            await self.io(self.service.close); self.service=None
        if self.executor:
            executor=self.executor; self.executor=None
            await asyncio.to_thread(executor.shutdown,wait=True)
        self.history.clear(); self.sent.clear()

    async def on_config_update(self,scope,config_data,version):
        if scope!='self' or not self.ready: return
        try:
            new=MaiHushConfig.model_validate(config_data)
            if new.decision.enabled:
                if new.decision.task_name not in await self.ctx.llm.get_available_models(): raise Denied('MODEL_TASK_UNAVAILABLE')
            await self.io(self.service.reconfigure,new)
        except Exception as exc:
            self.ctx.logger.error('MaiHush config rejected; old active configuration retained: %s',type(exc).__name__)
            return
        await self.worker.close(); self.active=new; self.worker=Worker(self); self.worker.start()

    def participation(self,stream):
        now=time.monotonic(); sent=self.sent.get(stream,())
        return {'bot_sent_last_120_seconds':sum(now-x<=120 for x in sent),'seconds_since_last_send':int(now-sent[-1]) if sent else None}

    @HookHandler('chat.receive.after_process',name='maihush_receive',mode=HookMode.BLOCKING,order=HookOrder.EARLY,error_policy=ErrorPolicy.SKIP,timeout_ms=500)
    async def receive(self,message=None,**kwargs):
        if not self.ready or not self.active.decision.enabled or not self.active.soft.enabled: return {'action':'continue'}
        try:
            e=evidence(self.active,message)
            route=self.active.route(e.stream)
            c=classify(e,route.bot_account_id)
            # Keep bounded public context, including structured provenance.
            self.history[e.stream].append({'id':e.mid,'text':e.text.encode()[:1024].decode(errors='ignore'),'sources':[s.get('type','unknown') for s in e.segments]})
            if c: await self.worker.offer(e,c)
        except Denied: pass
        return {'action':'continue'}

    @HookHandler('send_service.before_send',name='maihush_guard',mode=HookMode.BLOCKING,order=HookOrder.EARLY,error_policy=ErrorPolicy.ABORT,timeout_ms=1000)
    async def before_send(self,message=None,**kwargs):
        if not self.active: raise Denied('NOT_READY')
        try:
            route=route_for(self.active,message,outbound=True)
            if route is None: return {'action':'continue'}
            if not self.ready: return {'action':'abort','reason':'MAIHUSH_STOPPING'}
            allowed=await self.io(self.service.guard,route.stream_id)
            return {'action':'continue' if allowed else 'abort','reason':'MAIHUSH_POLICY'}
        except Denied as exc:
            return {'action':'abort','reason':str(exc)}

    @HookHandler('send_service.after_send',name='maihush_statistics',mode=HookMode.OBSERVE,error_policy=ErrorPolicy.SKIP)
    async def after_send(self,message=None,sent=False,**kwargs):
        if self.ready and sent is True and isinstance(message,dict):
            route=route_for(self.active,message,outbound=True)
            if route: self.sent[route.stream_id].append(time.monotonic())
        return {'action':'continue'}

    @Command('maihush',pattern=r'^/maihush(?:\s[\s\S]*)?$',description='Current group MaiHush controls')
    async def command(self,**kwargs):
        stream=None
        try:
            if not self.ready: raise Denied('NOT_READY')
            e=evidence(self.active,kwargs.get('message'),kwargs); stream=e.stream
            # Query is only a conflict check, never an actor authentication API.
            exact=await self.ctx.message.get_by_id(e.mid,stream_id=e.stream)
            if exact is not None:
                check=evidence(self.active,exact)
                if check.fingerprint!=e.fingerprint: raise Denied('SCOPE_MISMATCH')
            parts=e.text.split(' ')
            if any(not p or any(ord(c)<32 for c in p) for p in parts) or len(parts) not in (2,3): raise Denied('INVALID_COMMAND')
            op=parts[1]; route=self.active.route(stream)
            if op in ('help','status') and len(parts)==2:
                s=await self.io(self.service.state,stream)
                text='/maihush help|status|mute [20m]|renew 10m|unmute|diagnostics' if op=='help' else f'MaiHush {s.hard}/{s.soft}; remaining={max(0,int(max(s.hard_until if s.hard=="HARD_SILENT" else 0,s.soft_until)-time.time()))}s'
                await self.ctx.send.text(text,stream); return True,None,2
            if route.role(e.actor)=='member': raise Denied('PERMISSION_DENIED')
            if op=='diagnostics' and len(parts)==2:
                await self.ctx.send.text(f'MaiHush 0.1.0 health={self.service.health}/{self.service.reason}; revision={self.service.revision}; semantic={self.active.decision.enabled}; H01/H02/H03=false; queue={len(self.worker.pending)}',stream)
                return True,None,2
            if op not in ('mute','renew','unmute') or op=='renew' and len(parts)!=3 or op=='unmute' and len(parts)!=2: raise Denied('INVALID_COMMAND')
            seconds=duration(parts[2],self.active.hard.max_seconds) if len(parts)==3 else self.active.hard.default_seconds
            await self.io(self.service.command,e,op,seconds)
            return True,None,2
        except Denied as exc:
            if stream and time.monotonic()>=self.error_after.get(stream,0):
                self.error_after[stream]=time.monotonic()+30
                await self.ctx.send.text('MaiHush: '+str(exc),stream)
            return False,None,2

    @API('get_contract',version='1',public=True)
    async def get_contract(self,**kwargs):
        return {'api_version':1,'ok':True,'data':{'contract_version':'1.0.0','plugin':'MaiHush','host':'1.3.12','sdk':'2.15.0','hard_control':'local_command_only','identity_h03':'disabled','frequency_h02':'unsupported','receipt_h01':'unsupported','cross_plugin_write':'unsupported','guard_scope':'send_service_while_registered'},'error':None}

    @API('get_state',version='1',public=True)
    async def get_state(self,**kwargs):
        return {'api_version':1,'ok':False,'data':None,'error':{'code':'UNTRUSTED_CONTEXT','retryable':False}}

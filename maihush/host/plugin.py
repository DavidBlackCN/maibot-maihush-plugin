from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial, wraps
from hashlib import sha256
import asyncio
from ..runtime_versions import runtime_descriptor
import time
from maibot_sdk import API, Command, HookHandler, MaiBotPlugin
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder
from ..application.service import Service
from ..decision.candidates import classify
from ..decision.worker import Worker
from ..domain.models import Denied, duration
from .config import MaiHushConfig
from .messages import evidence, route_for

def serialized(fn):
    @wraps(fn)
    async def guarded(self,*args,**kwargs):
        if fn.__name__=='before_send' and self.active is not None:
            message=kwargs.get('message',args[0] if args else None)
            try:
                # Exclusion must hold for both sides of an in-progress switch.
                # Potentially affected sends wait, then reclassify on publication.
                configs=(self.active,self.transition_config)
                if all(route_for(c,message,outbound=True) is None for c in configs if c is not None): return {'action':'continue'}
            except Denied: pass
        if fn.__name__=='before_send':
            # Admit at most one managed send into the async lock/SQLite path.
            # A burst must not create hundreds of timeout tasks and provoke
            # Host circuit-breaking, which skips registered guards afterwards.
            if self.send_inflight:
                return {'action':'abort','reason':'MAIHUSH_BUSY'}
            self.send_inflight=True
            # Bound queueing below the Host's 1000ms Hook deadline. Returning
            # an explicit veto does not increment the Host circuit breaker.
            # No permit is reserved when this queue budget is exhausted.
            try:
                try:
                    await asyncio.wait_for(self.control_gate.acquire(),timeout=.2)
                except TimeoutError:
                    return {'action':'abort','reason':'MAIHUSH_BUSY'}
                try:
                    return await fn(self,*args,**kwargs)
                finally:
                    self.control_gate.release()
            finally:
                self.send_inflight=False
        async with self.control_gate:
            return await fn(self,*args,**kwargs)
    return guarded

class MaiHushPlugin(MaiBotPlugin):
    config_model=MaiHushConfig

    def __init__(self):
        super().__init__()
        self.active=None; self.service=None; self.executor=None; self.worker=None
        self.ready=False; self.futures=set(); self.maintenance=None
        self.history=defaultdict(lambda:deque(maxlen=20)); self.sent=defaultdict(lambda:deque(maxlen=256)); self.error_after={}
        self.control_gate=asyncio.Lock(); self.quiescing=False
        self.send_inflight=False
        self.transition_config=None
        self.config_tasks=set()
        self.seen=defaultdict(dict); self.inbound=defaultdict(lambda:deque(maxlen=256)); self.inbound_seen=defaultdict(dict)

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
        if self.config_tasks: await asyncio.gather(*tuple(self.config_tasks),return_exceptions=True)
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
        self.seen.clear(); self.inbound.clear(); self.inbound_seen.clear()

    async def on_config_update(self,scope,config_data,version):
        if scope!='self' or not self.ready: return
        try:
            new=MaiHushConfig.model_validate(config_data)
            if new.decision.enabled:
                if new.decision.task_name not in await self.ctx.llm.get_available_models(): raise Denied('MODEL_TASK_UNAVAILABLE')
        except Exception as exc:
            self.ctx.logger.error('MaiHush config rejected; old active configuration retained: %s',type(exc).__name__)
            return
        # RPC cancellation must not interrupt the commit/publish pair. The
        # owned task finishes under the gate and unload joins it as well.
        task=asyncio.create_task(self.apply_config(new)); self.config_tasks.add(task)
        task.add_done_callback(self.config_tasks.discard)
        try: await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    async def apply_config(self,new):
        async with self.control_gate:
            if not self.ready: return
            self.quiescing=True
            self.transition_config=new
            try:
                # Cancel/join old decisions before changing the store config.
                # No guarded Hook can observe active/store from different epochs.
                await self.worker.close()
                await self.io(self.service.reconfigure,new)
                self.active=new
                self.history.clear(); self.sent.clear(); self.seen.clear(); self.inbound.clear(); self.inbound_seen.clear()
            except Exception as exc:
                self.ctx.logger.error('MaiHush config rejected; old active configuration retained: %s',type(exc).__name__)
            finally:
                self.worker=Worker(self); self.worker.start(); self.quiescing=False; self.transition_config=None

    def participation(self,stream):
        now=time.monotonic(); sent=self.sent.get(stream,())
        return {'bot_sent_last_120_seconds':sum(now-x<=120 for x in sent),'seconds_since_last_send':int(now-sent[-1]) if sent else None}

    @HookHandler('chat.receive.after_process',name='maihush_receive',mode=HookMode.BLOCKING,order=HookOrder.EARLY,error_policy=ErrorPolicy.SKIP,timeout_ms=500)
    @serialized
    async def receive(self,message=None,**kwargs):
        if not self.ready or not self.active.soft.enabled: return {'action':'continue'}
        try:
            e=evidence(self.active,message)
            route=self.active.route(e.stream)
            now=time.monotonic()
            if self.observe_once(self.inbound_seen[e.stream],e.mid,now): self.inbound[e.stream].append(now)
            if not self.active.decision.enabled: return {'action':'continue'}
            ref=sha256(e.actor.encode()).hexdigest()[:24]
            context=[{'same_actor_direct_exchange':True} for x in list(self.history[e.stream])[-2:]
                     if x.get('_actor_ref')==ref and x.get('_targeted') and now-x.get('_at',0)<=30
                     and self.sent[e.stream] and now-self.sent[e.stream][-1]<=30]
            c=classify(e,route.bot_account_id,context)
            # Keep bounded public context, including structured provenance.
            self.history[e.stream].append({'id':e.mid,'text':e.text.encode()[:1024].decode(errors='ignore'),'sources':[s.get('type','unknown') for s in e.segments],
                                          '_actor_ref':ref,'_targeted':bool(c and c.safe),'_at':now})
            if c: await self.worker.offer(e,c,context)
        except Denied: pass
        return {'action':'continue'}

    @HookHandler('send_service.before_send',name='maihush_guard',mode=HookMode.BLOCKING,order=HookOrder.EARLY,error_policy=ErrorPolicy.ABORT,timeout_ms=1000)
    @serialized
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
    @serialized
    async def after_send(self,message=None,sent=False,**kwargs):
        if self.ready and sent is True and isinstance(message,dict):
            route=route_for(self.active,message,outbound=True)
            if route:
                now=time.monotonic(); mid=message.get('message_id')
                if not isinstance(mid,str) or not 1<=len(mid)<=256 or not self.observe_once(self.seen[route.stream_id],mid,now): return {'action':'continue'}
                self.sent[route.stream_id].append(now)
                a=self.active.autonomous
                recent=[x for x in self.sent[route.stream_id] if now-x<=a.window_seconds]
                people=sum(now-x<=a.window_seconds for x in self.inbound[route.stream_id])
                if a.enabled and len(recent)>=a.min_successful_sends and len(recent)*100>=(len(recent)+people)*a.min_bot_share_percent:
                    tail=recent[-a.min_successful_sends:]
                    if all(b-c<=a.max_gap_seconds for c,b in zip(tail,tail[1:])):
                        await self.io(self.service.auto_restrain,route.stream_id,self.service.revision)
        return {'action':'continue'}

    @staticmethod
    def observe_once(seen,mid,now):
        for key,at in list(seen.items()):
            if now-at>3600: del seen[key]
        if mid in seen or len(seen)>=4096: return False
        seen[mid]=now; return True

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
            from .natural_commands import parts_for
            parts=parts_for(e.text)
            if any(not p or any(ord(c)<32 for c in p) for p in parts) or len(parts) not in (2,3): raise Denied('INVALID_COMMAND')
            op=parts[1]; route=self.active.route(stream)
            if op in ('help','status') and len(parts)==2:
                s=await self.io(self.service.state,stream)
                text='/maihush help|status|mute [20m]|renew 10m|unmute|diagnostics' if op=='help' else f'MaiHush {s.hard}/{s.soft}; remaining={max(0,int(max(s.hard_until if s.hard=="HARD_SILENT" else 0,s.soft_until)-time.time()))}s'
                await self.ctx.send.text(text,stream); return True,None,2
            if route.role(e.actor)=='member': raise Denied('PERMISSION_DENIED')
            if op=='diagnostics' and len(parts)==2:
                await self.ctx.send.text(f'MaiHush 0.2.3 health={self.service.health}/{self.service.reason}; revision={self.service.revision}; semantic={self.active.decision.enabled}; autonomous={self.active.autonomous.enabled}; H01/H02/H03=false; queue={len(self.worker.pending)}; queue_rejection={self.worker.last_rejection}',stream)
                return True,None,2
            if op not in ('mute','renew','unmute') or op=='renew' and len(parts)!=3 or op=='unmute' and len(parts)!=2: raise Denied('INVALID_COMMAND')
            seconds=duration(parts[2],self.active.hard.max_seconds) if len(parts)==3 else self.active.hard.default_seconds
            async with self.control_gate:
                e=evidence(self.active,kwargs.get('message'),kwargs)
                await self.io(self.service.command,e,op,seconds)
            return True,None,2
        except Denied as exc:
            if stream and time.monotonic()>=self.error_after.get(stream,0):
                self.error_after[stream]=time.monotonic()+30
                await self.ctx.send.text('MaiHush: '+str(exc),stream)
            return False,None,2

    @API('get_contract',version='1',public=True)
    async def get_contract(self,**kwargs):
        return {'api_version':1,'ok':True,'data':{'contract_version':'1.1.0','plugin':'MaiHush','plugin_version':'0.2.3',**runtime_descriptor(),'hard_control':'local_command_only','identity_h03':'disabled','frequency_h02':'unsupported','receipt_h01':'unsupported','cross_plugin_write':'unsupported','guard_scope':'send_service_while_registered','autonomous_mode':'opt_in_restraint_only','recovery_policy':'requester_and_active_claims'},'error':None}

    @API('get_state',version='1',public=True)
    async def get_state(self,**kwargs):
        return {'api_version':1,'ok':False,'data':None,'error':{'code':'UNTRUSTED_CONTEXT','retryable':False}}

    @API('get_public_route_binding',version='1',public=True)
    @serialized
    async def get_public_route_binding(self,stream_id=None,**kwargs):
        # Caller must already know the session. No route enumeration, admin
        # lists, requester identity, text, or send permit is exposed.
        route=self.active.route(stream_id) if self.ready and not self.quiescing and type(stream_id) is str else None
        if route is None:
            return {'api_version':1,'ok':False,'data':None,'error':{'code':'ROUTE_UNAVAILABLE','retryable':False}}
        fields=('platform','adapter_namespace','bot_account_id','route_scope','group_id','stream_id')
        return {'api_version':1,'ok':True,'data':{k:getattr(route,k) for k in fields},'error':None}

    @API('get_public_constraints',version='1',public=True)
    @serialized
    async def get_public_constraints(self,stream_id=None,**kwargs):
        try:
            if not self.ready or self.quiescing: raise Denied('NOT_READY')
            if type(stream_id) is not str or not 1 <= len(stream_id) <= 256: raise Denied('INVALID_ROUTE')
            data=await self.io(self.service.public_summary,stream_id)
            return {'api_version':1,'ok':True,'data':data,'error':None}
        except Denied as exc:
            return {'api_version':1,'ok':False,'data':None,'error':{'code':str(exc),'retryable':False}}

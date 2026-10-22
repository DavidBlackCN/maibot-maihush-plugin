"""Native Supervisor/Runner/dispatch/capability/send chain; only driver is synthetic."""
from datetime import datetime
from pathlib import Path
import asyncio
import json
import os
import time
import uuid

ID='davidblackcn.maibot-maihush-plugin'
CONSUMER='maihush.test.consumer'
STREAM='synthetic-test-stream'

async def run(work,layer):
    global STREAM
    # The test driver needs the repository, the actual Runner must not. This
    # detects entry/import mistakes hidden by a developer PYTHONPATH.
    own_root=str(Path(__file__).resolve().parents[1])
    os.environ['PYTHONPATH']=os.pathsep.join(p for p in os.environ['PYTHONPATH'].split(os.pathsep) if p!=own_root)
    from src.config.config import config_manager
    config_manager.initialize(); cfg=config_manager.get_global_config()
    cfg.bot.qq_account='123456789'; cfg.bot.platform='qq'; cfg.telemetry.enable=False; cfg.maim_message.enabled=False
    from src.common.database.database import initialize_database,engine,get_db_session
    initialize_database()
    from src.chat.message_receive.chat_manager import chat_manager
    session=await chat_manager.get_or_create_session('qq','10001','987654321',account_id='123456789',scope='synthetic-test-scope')
    STREAM=session.session_id
    import tomllib, tomli_w
    config_path=work/'plugins/candidate/config.toml'
    candidate_config=tomllib.loads(config_path.read_text(encoding='utf8'))
    candidate_config['routes'][0]['stream_id']=STREAM
    config_path.write_text(tomli_w.dumps(candidate_config),encoding='utf8')
    from src.plugin_runtime.integration import get_plugin_runtime_manager
    from src.plugin_runtime.host.supervisor import PluginSupervisor
    from src.plugin_runtime.component_query import component_query_service
    from src.plugin_runtime.hook_payloads import serialize_session_message
    from src.chat.message_receive.message import SessionMessage
    from src.common.data_models.mai_message_data_model import MessageInfo,UserInfo,GroupInfo
    from src.common.data_models.message_component_data_model import MessageSequence,TextComponent,ImageComponent,EmojiComponent,VoiceComponent,ForwardNodeComponent,ForwardComponent
    manager=get_plugin_runtime_manager(); manager.ensure_builtin_hook_specs_registered()
    plugin_dirs=[work/'plugins']+([work/'neighbors'] if layer=='T2' else [])
    supervisor=PluginSupervisor(plugin_dirs=plugin_dirs,group_name='maihush-test',hook_spec_registry=manager._hook_spec_registry,max_restart_attempts=0,health_check_interval_sec=300,runner_spawn_timeout_sec=25)
    manager._third_party_supervisor=supervisor; manager._register_capability_impls(supervisor)
    report={'layer':layer,'host':'1.3.12','sdk':'2.15.0','cases':[],'boundary_observations':[],'real_platform_sends':0,'real_model_requests':0,'module_replacements':0,'full_bot_cli':False}
    def record(name): report['cases'].append(name)
    def message(text='synthetic output',actor='123456789',stream=STREAM,group='987654321',components=None):
        m=SessionMessage(message_id=uuid.uuid4().hex,timestamp=datetime.now(),platform='qq')
        m.message_info=MessageInfo(user_info=UserInfo(actor,'synthetic'),group_info=GroupInfo(group,'synthetic'))
        m.session_id=stream; m.account_id='123456789'; m.scope='synthetic-test-scope'; m.raw_message=MessageSequence(components if components is not None else [TextComponent(text)]); m.processed_plain_text=text
        return m
    async def call(name,args=None,plugin=ID):
        response=await supervisor.invoke_api(plugin,name,args=args or {},timeout_ms=6000)
        assert response.error is None and response.payload.get('success'),response
        return response.payload['result']
    async def command(text,actor='10001'):
        m=message(text,actor)
        # The real Host query service constructs the envelope and serializes m.
        found=component_query_service.find_command_by_text(text); assert found,text
        executor,groups,info=found
        return await executor(message=m,matched_groups=groups,plugin_config={})
    port=None
    try:
        await supervisor.start()
        assert ID in supervisor._registered_plugins,supervisor._registered_plugins
        assert (await call('get_contract'))['data']['hard_control']=='local_command_only'
        assert (await call('get_state',{'is_owner':True,'proof_ref':'fake'}))['error']['code']=='UNTRUSTED_CONTEXT'
        assert (await call('call',{'route':ID+'.get_state','payload':{'caller_plugin_id':ID,'verified':True}},CONSUMER))['ok'] is False
        assert (work/'plugins/candidate/config.toml').exists()
        handlers=[h for h in supervisor.component_registry.get_hook_handlers('send_service.before_send') if h.plugin_id==ID]
        assert len(handlers)==1
        entry=handlers[0]; assert str(entry.mode).endswith('blocking') and str(entry.error_policy).endswith('abort')
        record('T1 native Runner IPC registration, configuration generation, strict API refusal')
        await command('/maihush mute 20m','10003')
        assert not (await manager.invoke_hook('send_service.before_send',message=serialize_session_message(message()))).aborted
        assert (await command('/maihush mute 20m'))[0]
        hook=await manager.invoke_hook('send_service.before_send',message=serialize_session_message(message()))
        assert hook.aborted and not hook.errors,hook
        record('A02 native command envelope local whitelist + before_send session route')
        raw=serialize_session_message(message('/maihush unmute','10001'))
        response=await supervisor.invoke_plugin('plugin.invoke_command',ID,'maihush',args={'message':raw,'text':'/maihush unmute','stream_id':'wrong','group_id':'987654321','platform':'qq','user_id':'10001'},timeout_ms=6000)
        assert response.payload['result'][0] is False
        assert (await manager.invoke_hook('send_service.before_send',message=serialize_session_message(message()))).aborted
        record('A04 mismatching native envelope rejected')
        assert (await command('/maihush unmute'))[0]
        if layer=='T1':
            assert await supervisor.reload_plugins([ID],reason='test-reload')
            assert (await call('get_contract'))['ok']; record('T1 unload/reload lifecycle and writer lease')
            return report
        from src.platform_io.manager import get_platform_io_manager
        from src.platform_io.drivers.base import PlatformIODriver
        from src.platform_io.types import DriverDescriptor,DriverKind,DeliveryStatus,DeliveryReceipt,RouteBinding,RouteKey
        from src.services.send_service import send_session_message_with_message
        class CountedDriver(PlatformIODriver):
            def __init__(self):
                super().__init__(DriverDescriptor('maihush-counted',DriverKind.LOCAL,'qq','123456789','synthetic-test-scope'))
                self.calls=[]; self.fail=False; self.arrived=None; self.release=None
            async def send_message(self,message,route_key,metadata=None):
                self.calls.append({'stream':message.session_id,'types':[x.format_name for x in message.raw_message.components]})
                if self.arrived: self.arrived.set(); await self.release.wait()
                return DeliveryReceipt(message.message_id,route_key,DeliveryStatus.FAILED if self.fail else DeliveryStatus.SENT,driver_id=self.driver_id,driver_kind=DriverKind.LOCAL,external_message_id='external-'+message.message_id,error='synthetic' if self.fail else None)
        port=get_platform_io_manager(); driver=CountedDriver(); await port.add_driver(driver)
        port.send_route_table.bind(RouteBinding(driver.descriptor.route_key,driver.driver_id,DriverKind.LOCAL,priority=1000))
        async def send(m=None): return await send_session_message_with_message(m or message(),typing=False,storage_message=False,show_log=False)
        assert await send() is not None and len(driver.calls)==1
        record('T2 native SendService / PlatformIO / counted final driver positive control')
        before=len(driver.calls); assert (await command('/maihush mute 20m'))[0]; assert len(driver.calls)==before
        for components in ([TextComponent('x')],[TextComponent('')],[ImageComponent(binary_hash='synthetic')],[EmojiComponent(binary_hash='synthetic')],[VoiceComponent(binary_hash='synthetic')],[TextComponent(''),ImageComponent(binary_hash='synthetic')],[ForwardNodeComponent([ForwardComponent('synthetic','forward-test',[TextComponent('x')],user_id='10001')])]):
            assert await send(message(components=components)) is None
        for _ in range(3): assert await send() is None
        assert len(driver.calls)==before
        # Status/help/errors and proactive peer messages all take the same guard.
        await command('/maihush help'); await command('/maihush status'); await command('/maihush mute 1h','10003')
        assert await call('speak',{'stream':STREAM},plugin=CONSUMER) is False
        assert len(driver.calls)==before
        record('O02/O06 all tested media, segments, control replies and proactive peer blocked')
        assert await supervisor.reload_plugins([ID],reason='restart-persistence')
        assert await send() is None; record('P01 hard deadline survives native reload')
        assert (await command('/maihush unmute'))[0]
        for mode in ('error','timeout'):
            await call('test_mode',{'mode':mode},CONSUMER)
            n=len(driver.calls); assert await send() is None and len(driver.calls)==n
        await call('test_mode',{'mode':'error'},CONSUMER)
        unrelated=serialize_session_message(message(stream='private-unmanaged'))
        unrelated['message_info']['group_info']=None
        unrelated_failure=await manager.invoke_hook('send_service.before_send',message=unrelated)
        assert unrelated_failure.aborted and unrelated_failure.errors
        await call('test_mode',{'mode':'normal'},CONSUMER)
        record('O07 native dispatcher ErrorPolicy.ABORT on real Runner exception and timeout of synthetic handler')
        # An unexpected structure exception in the actual MaiHush guard reaches
        # the registered ABORT policy. This is a malformed Hook payload fixture.
        malformed=serialize_session_message(message(stream='unknown')); malformed['message_info']['group_info']=['invalid']
        bad=await manager.invoke_hook('send_service.before_send',message=malformed)
        assert bad.aborted
        report['boundary_observations'].append('O10 malformed routes rejected; handler-wide ABORT demonstrated on unrelated private stream by a synthetic registered handler')
        driver.arrived=asyncio.Event(); driver.release=asyncio.Event()
        inflight=asyncio.create_task(send()); await asyncio.wait_for(driver.arrived.wait(),5)
        assert (await command('/maihush mute 20m'))[0]
        driver.release.set(); assert await inflight is not None
        driver.arrived=driver.release=None
        assert await send() is None
        report['boundary_observations'].append('O01 send already past guard completed after mute; subsequent send blocked')
        # Native breaker API, no dispatcher replacement.
        from src.plugin_runtime.host.circuit_breaker import get_plugin_circuit_breaker
        breaker=get_plugin_circuit_breaker()
        for _ in range(3):
            permit=breaker.try_acquire(ID,'maihush_guard','hook:send_service.before_send'); breaker.record_failure(permit,'synthetic fault reproduction')
        n=len(driver.calls); assert await send() is not None; assert len(driver.calls)==n+1
        report['boundary_observations'].append('O08 open circuit skips registered hard guard and counted driver receives message')
        # Reset with the breaker own lifecycle API when available; unloading and
        # restarting a fresh scenario also clears process-local breaker state.
        if hasattr(breaker,'clear_plugin'): breaker.clear_plugin(ID)
        else: breaker._states.pop(ID,None)
        assert (await supervisor.unload_plugins([ID],reason='boundary-unload')).success
        n=len(driver.calls); assert await send() is not None; assert len(driver.calls)==n+1
        report['boundary_observations'].append('O08 unload/absent target permits send in unchanged Host')
        assert await supervisor.reload_plugins([ID],reason='boundary-restore')
        assert await send() is None
        n=len(driver.calls); direct=await port.send_message(message(),RouteKey('qq','123456789','synthetic-test-scope'))
        assert direct.has_success and len(driver.calls)==n+1
        report['boundary_observations'].append('O09 direct PlatformIO bypasses SendService guard')
        await call('test_target',{'stream':STREAM},CONSUMER)
        await call('test_mode',{'mode':'rewrite'},CONSUMER)
        n=len(driver.calls)
        assert await send(message(stream='unmanaged-source',group='222222222')) is not None
        assert len(driver.calls)==n+1 and driver.calls[-1]['stream']==STREAM
        await call('test_mode',{'mode':'normal'},CONSUMER)
        report['boundary_observations'].append('O09 later native Hook rewrites unguarded route into muted route; driver receives it')
        record('O08/O09 native fail-open boundaries reproduced and retained')
        # Load the actual staged core plugins only for this joint matrix.
        # Their originals, source, configs and databases remain read-only.
        import sys
        vita_id='davidblackcn.maibot-maivita-plugin'
        identity_id='davidblackcn.maibot-maiidentity-plugin'
        sys.path.insert(0,str(work/'neighbors/maibot-maivita-plugin'))
        from maivita.life.storage.life_database import LifeDatabase
        from maivita.life.life_state.engine import LifeStateEngine
        from maivita.life.domain.commands import Command as VitaCommand
        vita_path=work/'data/plugins'/vita_id/'life/maivita.db'
        vita_db=LifeDatabase.create(vita_path); vita_engine=LifeStateEngine(vita_db)
        vita_state=vita_engine.initialize()
        vita_engine.execute(VitaCommand(idempotency_key='synthetic-observed-sleep',kind='start_sleep',source='legacy_observed',expected_state_version=vita_state.state_version,payload={'duration_minutes':60}))
        for folder in ('maibot-maivita-plugin','maibot-maiIdentity-plugin'):
            path=work/'neighbors'/folder/'config.toml'
            data=tomllib.loads(path.read_text(encoding='utf8')); data['plugin']['enabled']=True
            if folder=='maibot-maivita-plugin': data['instance']['allowed_streams']=[STREAM]
            path.write_text(tomli_w.dumps(data),encoding='utf8')
        assert await supervisor.reload_plugins([vita_id,identity_id],reason='synthetic-joint-test')
        assert (await call('get_engine_status',plugin=vita_id))['ready']
        identity_result=await call('call',{'route':identity_id+'.authorize','payload':{'is_owner':True,'proof_ref':'fake','user_id':'10001'}},CONSUMER)
        assert identity_result['error']['code']=='UNTRUSTED_CONTEXT'
        wake=await call('call',{'route':vita_id+'.request_wakeup','payload':{'verified':True}},CONSUMER)
        assert wake['ok'] is False
        assert (await command('/maihush unmute'))[0]
        n=len(driver.calls); assert await send() is None and len(driver.calls)==n
        dispatch=await manager.invoke_hook('send_service.before_send',message=serialize_session_message(message()))
        assert dispatch.aborted and 'maivita' in dispatch.stopped_by
        record('I01/I03 actual MaiVita sleep veto survives Hush unmute; actual MaiIdentity forgery refusal over API IPC')
        assert (await supervisor.unload_plugins([vita_id],reason='offline-observed-wake-fixture')).success
        vita_state=vita_engine.state()
        vita_engine.execute(VitaCommand(idempotency_key='synthetic-observed-wake',kind='wake',source='legacy_observed',expected_state_version=vita_state.state_version,payload={}))
        assert await supervisor.reload_plugins([vita_id],reason='observed-awake-fixture')
        assert (await command('/maihush mute 20m'))[0]
        n=len(driver.calls); assert await send() is None and len(driver.calls)==n
        dispatch=await manager.invoke_hook('send_service.before_send',message=serialize_session_message(message()))
        assert dispatch.aborted and 'maihush' in dispatch.stopped_by
        record('I02 actual MaiVita awake does not clear Hush hard veto; local synthetic observed-life fixtures, no models')
        assert (await supervisor.unload_plugins([vita_id,identity_id],reason='end-joint-matrix')).success
        # Offline, explicit state fixtures exercise guard modes without a paid
        # model or a public soft-state write API. Domain transitions are T0.
        from maihush.host.config import MaiHushConfig
        from maihush.domain.models import State
        from maihush.application.service import Service
        from dataclasses import asdict
        import sqlite3
        async def seed_soft(mode):
            assert (await supervisor.unload_plugins([ID],reason='offline-guard-fixture')).success
            active=MaiHushConfig.model_validate(tomllib.loads(config_path.read_text(encoding='utf8')))
            store=Service(work/'data/plugins'/ID,active)
            now=store.now(); old=store.state(STREAM)
            state=State(version=old.version+1,soft=mode,soft_until=now+120,dwell_start=now,
                        episode_start=now if mode=='SOFT_SILENT' else 0,episode_deadline=now+600 if mode=='SOFT_SILENT' else 0)
            with store.db.transaction(): store._save(STREAM,state)
            store.close()
            assert await supervisor.reload_plugins([ID],reason='guard-fixture-load')
        await seed_soft('SOFT_SILENT')
        n=len(driver.calls)
        for components in ([TextComponent('x')],[ImageComponent(binary_hash='fixture')],[VoiceComponent(binary_hash='fixture')]):
            assert await send(message(components=components)) is None
        assert len(driver.calls)==n
        await seed_soft('RESTRAINED')
        driver.fail=True; n=len(driver.calls)
        outcomes=await asyncio.gather(*(send() for _ in range(100)))
        assert all(x is None for x in outcomes) and len(driver.calls)==n+1
        assert await send() is None and len(driver.calls)==n+1
        driver.fail=False
        record('O04/O05 native 100 concurrent SendService attempts grant one interval permit; failed delivery and absent success receipt do not refund')
        record('O02 native soft-silent guard with offline synthetic mode fixture, not a model decision')
        await command('/maihush unmute'); await command('/maihush mute 1s')
        await asyncio.sleep(1.1)
        await manager.invoke_hook('send_service.before_send',message=serialize_session_message(message()))
        with sqlite3.connect(work/'data/plugins'/ID/'maihush.sqlite3') as db:
            observed=json.loads(db.execute('SELECT payload FROM stream_state WHERE stream=?',(STREAM,)).fetchone()[0])
        assert observed['hard']=='NONE' and observed['soft']=='RECOVERING'
        record('S03 native finite hard expiry enters recovery with no automatic arrival message')
        # Native self-config transport rejects unsupported integration switches
        # before changing the active instance. No false flag is coerced from 0.
        active=tomllib.loads(config_path.read_text(encoding='utf8'))
        for flag in ('identity_h03_enabled','frequency_h02_enabled','receipt_h01_enabled'):
            altered=json.loads(json.dumps(active)); altered.setdefault('integrations',{})[flag]=True
            try: await supervisor.validate_plugin_config(ID,altered)
            except ValueError: pass
            else: raise AssertionError('unsupported integration accepted')
        altered=json.loads(json.dumps(active)); altered.setdefault('integrations',{})['identity_h03_enabled']=True
        assert not await supervisor.notify_plugin_config_updated(ID,altered,config_version='1')
        assert (await call('get_contract'))['ok']
        record('I04/S05 native unsupported integration config rejection retains active plugin')
        await command('/maihush unmute'); await command('/maihush mute 20m')
        process=supervisor._runner_process
        process.kill(); await process.wait(); await asyncio.sleep(.2)
        n=len(driver.calls); crashed=await send()
        report['boundary_observations'].append('O08 actual Runner termination: '+('driver reached after missing target' if crashed is not None else 'registered invocation failed and ABORT remained effective'))
        from src.common.shutdown import request_shutdown
        request_shutdown('end-of-isolated-scenario')
        n=len(driver.calls); shutdown_send=await send()
        assert shutdown_send is not None and len(driver.calls)==n+1
        report['boundary_observations'].append('O08 actual Host shutdown state skips guard and counted driver receives message')
        report['counted_driver_calls']=len(driver.calls)
    finally:
        if port: await port.stop()
        await supervisor.stop(); engine.dispose()
    record('native shutdown joins workers and unregisters components')
    return report

if __name__=='__main__':
    work=Path(os.environ['MAIBOT_INSTANCE_DIR'])
    try:
        report=asyncio.run(run(work,os.environ['MAIHUSH_TEST_LAYER']))
        report['passed']=True
        (work/'result.json').write_text(json.dumps(report,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        from src.common.logger import shutdown_logging
        shutdown_logging()

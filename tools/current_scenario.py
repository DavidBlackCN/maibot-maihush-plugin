"""Current Identity + Hush, official Host/SDK; loopback model/platform substitutes."""
from pathlib import Path
import asyncio, json, os, importlib.metadata
import tomli_w
HUSH='davidblackcn.maibot-maihush-plugin'
IDENTITY='davidblackcn.maibot-maiidentity-plugin'

async def run(work):
    scope = None
    from src.config.config import config_manager
    config_manager.initialize(); cfg=config_manager.get_global_config()
    # SnowLuma supplies account_id per route; the official fallback may be empty.
    cfg.bot.qq_account=''; cfg.bot.platform=''; cfg.telemetry.enable=False; cfg.maim_message.enabled=False
    from src.common.database.database import initialize_database,engine
    initialize_database()
    from src.chat.message_receive.chat_manager import chat_manager
    session=await chat_manager.get_or_create_session('qq','10001','987654321',account_id='123456789',scope=scope)
    stream=session.session_id
    cfg.bot.nickname='\u5c0f\u6843'
    configs={
        'maibot-maihush-plugin':{'plugin':{'enabled':True,'config_version':'2'},'groups':[{'group_id':'987654321'}], 'management':{'source':'identity'}},
        'maibot-maiIdentity-plugin':{'plugin':{'enabled':True,'config_version':'4'},'management':{'admin_accounts':['10001']}}}
    for folder,config in configs.items():
        (work/'plugins'/folder/'config.toml').write_text(tomli_w.dumps(config),encoding='utf8')
    from src.plugin_runtime.integration import get_plugin_runtime_manager
    from src.plugin_runtime.host.supervisor import PluginSupervisor
    manager=get_plugin_runtime_manager(); manager.ensure_builtin_hook_specs_registered()
    supervisor=PluginSupervisor(plugin_dirs=[work/'plugins'],group_name='joint-test',hook_spec_registry=manager._hook_spec_registry,
        socket_path=os.environ.get('MAIHUSH_TEST_SOCKET_PATH'),max_restart_attempts=0,health_check_interval_sec=300,runner_spawn_timeout_sec=25)
    manager._third_party_supervisor=supervisor; manager._register_capability_impls(supervisor)
    model_calls=[]
    async def models(plugin_id,capability,args): return ['joint-test']
    async def model(plugin_id,capability,args):
        model_calls.append(plugin_id)
        if plugin_id==HUSH:
            data=json.loads(args['prompt'][1]['content'])
            return {'success':True,'response':json.dumps({'schema_version':1,'intent':'pause','target':'bot','decision':'soft_silence','duration_seconds':60,'reason_code':'RESPECT_REQUEST','evidence_message_ids':[data['request']['id']],'reply':'好，你们先聊。'})}
        return {'success':False,'response':'','model_name':'synthetic-unavailable'}
    supervisor.capability_service.register_capability('llm.get_available_models',models)
    supervisor.capability_service.register_capability('llm.generate',model)
    from src.platform_io.manager import get_platform_io_manager
    from src.platform_io.drivers.base import PlatformIODriver
    from src.platform_io.types import DriverDescriptor,DriverKind,DeliveryStatus,DeliveryReceipt,RouteBinding
    class CountedDriver(PlatformIODriver):
        def __init__(self):
            super().__init__(DriverDescriptor('joint-counted',DriverKind.LOCAL,'qq','123456789',scope)); self.calls=[];self.texts=[]
        async def send_message(self,message,route_key,metadata=None):
            self.calls.append(message.session_id)
            from src.common.data_models.message_component_data_model import TextComponent
            self.texts.append(''.join(c.text for c in message.raw_message.components if isinstance(c,TextComponent)))
            return DeliveryReceipt(message.message_id,route_key,DeliveryStatus.SENT,driver_id=self.driver_id,driver_kind=DriverKind.LOCAL,external_message_id='test-'+message.message_id)
    port=get_platform_io_manager(); driver=CountedDriver(); await port.add_driver(driver)
    port.send_route_table.bind(RouteBinding(driver.descriptor.route_key,driver.driver_id,DriverKind.LOCAL,priority=1000))
    async def call(plugin,name,args=None):
        reply=await supervisor.invoke_api(plugin,name,args=args or {},timeout_ms=8000)
        assert reply.error is None and reply.payload.get('success'),reply
        return reply.payload['result']
    try:
        await supervisor.start()
        assert {HUSH,IDENTITY} <= set(supervisor._registered_plugins)
        from tools.current_cases import run_cases
        return await run_cases(work,supervisor,manager,call,configs,stream,scope,driver,model_calls)
    finally:
        await supervisor.stop()
        await port.stop()
        engine.dispose()

if __name__=='__main__':
    from src.common.logger import shutdown_logging
    work=Path(os.environ['MAIBOT_INSTANCE_DIR'])
    try:
        result=asyncio.run(asyncio.wait_for(run(work),120))
        (work/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        shutdown_logging()

"""Real Host/Runner/SDK receipt and identity checks; synthetic model and platform."""
import asyncio
from datetime import datetime
import importlib.metadata
import os
import uuid
import tomli_w

HUSH='davidblackcn.maibot-maihush-plugin'
IDENTITY='davidblackcn.maibot-maiidentity-plugin'


async def run_cases(work,supervisor,manager,call,configs,stream,scope,driver,model_calls):
    from src.chat.message_receive.message import SessionMessage
    from src.common.data_models.mai_message_data_model import MessageInfo,UserInfo,GroupInfo
    from src.common.data_models.message_component_data_model import MessageSequence,TextComponent
    from src.plugin_runtime.component_query import component_query_service
    from src.plugin_runtime.hook_payloads import serialize_session_message
    from src.services import send_service
    cases=[]
    def raw(text,actor='10001',mid=None):
        m=SessionMessage(message_id=mid or uuid.uuid4().hex,timestamp=datetime.now(),platform='qq')
        m.message_info=MessageInfo(user_info=UserInfo(actor,'麦麦的妈妈'),group_info=GroupInfo('987654321','test'))
        m.session_id=stream; m.account_id='123456789'; m.scope=scope
        m.raw_message=MessageSequence([TextComponent(text)]); m.processed_plain_text=text
        return m
    async def command(text,actor='10001',mid=None):
        executor,groups,_=component_query_service.find_command_by_text(text)
        assert executor,text
        return await executor(message=raw(text,actor,mid),matched_groups=groups,plugin_config={})
    async def state(): return (await call(HUSH,'get_public_constraints',{'stream_id':stream}))['data']
    async def reload(folder,plugin):
        (work/'plugins'/folder/'config.toml').write_text(tomli_w.dumps(configs[folder]),encoding='utf8')
        assert await supervisor.reload_plugins([plugin],reason='hush-experience')
    inspected=await supervisor.inspect_plugin_config(HUSH)
    assert not inspected.config_error and inspected.config_schema['sections']
    assert (await call(HUSH,'get_public_route_binding',{'stream_id':stream}))['data']['stream_id']==stream
    cases.append('native_schema_and_default_all_without_group_configuration')
    mid=uuid.uuid4().hex
    assert (await command('/maihush mute 5m',mid=mid))[0]
    assert (await state())['hard']=='HARD_SILENT'
    assert driver.texts==['好，我先安静 5 分钟。'],driver.texts
    assert not await send_service.text_to_stream('好，我先安静 5 分钟。',stream)
    assert not await send_service.text_to_stream('unrelated ordinary send',stream)
    assert len(driver.calls)==1
    cases.append('native_identity_command_one_receipt_no_same_text_bypass')
    await command('/maihush mute 5m',mid=mid)
    await command('/maihush renew 10m')
    assert len(driver.calls)==1
    assert await supervisor.reload_plugins([HUSH],reason='receipt-restart')
    await command('/maihush mute 5m',mid=mid)
    assert len(driver.calls)==1 and (await state())['hard']=='HARD_SILENT'
    cases.append('repeat_renew_restart_no_duplicate_receipt')
    await command('/maihush unmute')
    before=len(driver.calls)
    await command('/maihush mute 5m','10003')
    assert (await state())['hard']=='NONE'  # Nickname never grants admin.
    configs['maibot-maiIdentity-plugin']['management']['admin_accounts']=[]
    await reload('maibot-maiIdentity-plugin',IDENTITY)
    await command('/maihush mute 5m')
    assert (await state())['hard']=='NONE'
    configs['maibot-maiIdentity-plugin']['management']['admin_accounts']=['10001']
    await reload('maibot-maiIdentity-plugin',IDENTITY)
    cases.append('identity_revocation_and_nickname_do_not_authorize')
    configs['maibot-maihush-plugin']['groups']=[{'group_id':'987654321',
        'management':{'source':'local','admin_accounts':['10003']}}]
    await reload('maibot-maihush-plugin',HUSH)
    await command('/maihush mute 5m','10001')
    assert (await state())['hard']=='NONE'
    assert (await command('/maihush mute 5m','10003'))[0]
    assert (await state())['hard']=='HARD_SILENT'
    from src.chat.message_receive.chat_manager import chat_manager
    second=await chat_manager.get_or_create_session('qq','10001','888888888',account_id='123456789',scope=scope)
    second_stream=second.session_id
    m=raw('hello');m.session_id=second_stream
    m.message_info.group_info=GroupInfo('888888888','second')
    await manager.invoke_hook('chat.receive.after_process',message=serialize_session_message(m))
    for _ in range(40):
        binding=await call(HUSH,'get_public_route_binding',{'stream_id':second_stream})
        if binding['ok']: break
        await asyncio.sleep(.1)
    assert binding['ok'],binding
    assert (await state())['hard']=='HARD_SILENT'
    assert (await call(HUSH,'get_public_constraints',{'stream_id':second_stream}))['data']['hard']=='NONE'
    assert (await command('/maihush unmute','10003'))[0]
    configs['maibot-maihush-plugin']['management']={'source':'local','admin_accounts':['10003']}
    configs['maibot-maihush-plugin']['groups'][0]['management']={'source':'identity'}
    await reload('maibot-maihush-plugin',HUSH)
    await command('/maihush mute 5m','10003')
    assert (await state())['hard']=='NONE'
    assert (await command('/maihush mute 5m','10001'))[0]
    assert (await command('/maihush unmute','10001'))[0]
    configs['maibot-maihush-plugin']['management']={'source':'identity'}
    configs['maibot-maihush-plugin']['groups']=[]
    cases.append('native_per_group_authorization_and_incremental_discovery')
    configs['maibot-maihush-plugin']['social']={'enabled':True,'task_name':'joint-test'}
    await reload('maibot-maihush-plugin',HUSH)
    import sqlite3, json
    # The preceding manual unmute has a finite social cooldown. Reset only
    # this new isolated test clock's cooldown in its own stopped fixture DB.
    assert (await supervisor.unload_plugins([HUSH],reason='isolated-social-clock')).success
    db=sqlite3.connect(work/'data/plugins'/HUSH/'maihush.sqlite3')
    data=json.loads(db.execute('SELECT payload FROM stream_state WHERE stream=?',(stream,)).fetchone()[0])
    data['pause_after']=0
    db.execute('UPDATE stream_state SET payload=? WHERE stream=?',(json.dumps(data),stream));db.commit();db.close()
    assert await supervisor.reload_plugins([HUSH],reason='isolated-social-clock-ready')
    before=len(driver.calls); calls=model_calls.count(HUSH)
    result=await manager.invoke_hook('chat.receive.after_process',message=serialize_session_message(raw('小桃安静一会','10003')))
    assert result.aborted
    for _ in range(60):
        if (await state())['soft']=='SOFT_SILENT' and len(driver.calls)>before: break
        await asyncio.sleep(.1)
    assert (await state())['soft']=='SOFT_SILENT'
    assert driver.texts[before:]==['好，你们先聊。'],driver.texts
    assert model_calls.count(HUSH)==calls+1
    assert not await send_service.text_to_stream('ordinary reply after social silence',stream)
    cases.append('native_social_same_model_call_receipt_and_core_request_consumed')
    assert (await supervisor.unload_plugins([HUSH],reason='lifecycle-check')).success
    assert await supervisor.reload_plugins([HUSH],reason='lifecycle-restart')
    assert (await state())['soft']=='SOFT_SILENT'
    cases.append('unload_storage_close_and_soft_deadline_restore')
    return {'passed':True,'host':os.environ['MAIBOT_HOST_VERSION'],
            'sdk':importlib.metadata.version('maibot-plugin-sdk'),'cases':cases,
            'counted_driver_deliveries':len(driver.calls),'synthetic_model_calls':len(model_calls),
            'real_model_requests':0,'real_platform_sends':0}

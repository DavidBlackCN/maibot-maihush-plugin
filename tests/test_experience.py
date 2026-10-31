"""Public configuration, native plugin methods and synthetic SDK IO."""
import asyncio
import json
import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from maihush.host.plugin import MaiHushPlugin
from maihush.host.config import MaiHushConfig
from maihush.host.receipts import KEY, Receipts
from maihush.host.routes import resolve
from tests.helpers import message

STREAM='synthetic-test-stream'


async def start_public(directory, *, identity=False, social=False, reply=True):
    p=MaiHushPlugin()
    settings={'groups':[{'group_id':'987654321','route_scope':'synthetic-test-scope'}],
              'management':{'source':'identity' if identity else 'local','admin_accounts':['10001']},
              'social':{'enabled':social,'task_name':'test' if social else ''},'reply':{'enabled':reply}}
    p.set_plugin_config(MaiHushConfig.model_validate(settings).model_dump())
    streams=[{'platform':'qq','is_group_session':True,'group_id':'987654321','account_id':'123456789',
              'scope':'synthetic-test-scope','stream_id':STREAM,'session_id':STREAM}]
    calls=[]; delivered=[]
    async def model(messages,**kwargs):
        calls.append(messages)
        data=json.loads(messages[1]['content'])
        return {'success':True,'response':json.dumps({'schema_version':1,'intent':'pause','target':'bot',
            'decision':'soft_silence','duration_seconds':60,'reason_code':'RESPECT_REQUEST',
            'evidence_message_ids':[data['request']['id']],'reply':'好，你们先聊。'})}
    async def custom(kind,data,stream,**kwargs):
        raw=message(stream=stream); raw['raw_message']=[{'type':kind,'data':data}]
        result=await p.before_send(message=raw)
        if result['action']=='continue':
            delivered.append(result['modified_kwargs']['message']['processed_plain_text'])
            return {'sent':True,'message_id':'receipt'}
        return {'sent':False}
    async def text(text,stream,**kwargs):
        result=await p.before_send(message=message(text=text,stream=stream))
        if result['action']=='continue': delivered.append(text)
        return result['action']=='continue'
    ctx=SimpleNamespace(paths=SimpleNamespace(data_dir=directory),logger=logging.getLogger('hush-test'),
        chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=streams)),
        config=SimpleNamespace(get=AsyncMock(side_effect=lambda key,default='': '小桃' if key=='bot.nickname' else '自然简短')),
        llm=SimpleNamespace(get_available_models=AsyncMock(return_value=['test']),generate=AsyncMock(side_effect=model)),
        message=SimpleNamespace(get_by_id=AsyncMock(return_value=None)),
        api=SimpleNamespace(call=AsyncMock(return_value={'is_admin':True})),
        send=SimpleNamespace(custom=AsyncMock(side_effect=custom),text=AsyncMock(side_effect=text)))
    p._set_context(ctx)
    await p.on_load()
    return p,ctx,calls,delivered


async def command(p,text,actor='10001',mid='command'):
    raw=message(text=text,actor=actor,mid=mid)
    return await p.command(message=raw,stream_id=STREAM,platform='qq',group_id='987654321',user_id=actor,text=text)


def test_commands_confirm_once_and_replay_renew_are_silent(tmp_path):
    async def scenario():
        p,ctx,calls,delivered=await start_public(tmp_path)
        try:
            assert (await command(p,'/maihush mute 5m'))[0]
            assert delivered==['好，我先安静 5 分钟。'] and not calls
            assert (await p.before_send(message=message()))['action']=='abort'
            await command(p,'/maihush mute 5m')
            await command(p,'/maihush renew 10m',mid='renew')
            assert len(delivered)==1
            await command(p,'/maihush status',mid='status')
            assert len(delivered)==1
            await command(p,'/maihush unmute',mid='unmute')
            assert (await p.before_send(message=message()))['action']=='continue'
            assert len(delivered)==1
        finally: await p.on_unload()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure',[False,True])
def test_identity_false_or_unavailable_never_falls_back(tmp_path,failure):
    async def scenario():
        p,ctx,_,_=await start_public(tmp_path,identity=True)
        try:
            if failure: ctx.api.call.side_effect=TimeoutError
            else: ctx.api.call.return_value={'is_admin':False}
            assert not (await command(p,'/maihush mute'))[0]
            assert p.service.peek(STREAM).hard=='NONE'
            ctx.api.call.side_effect=None; ctx.api.call.return_value={'is_admin':True}
            assert (await command(p,'/maihush mute',mid='approved'))[0]
            assert p.service.peek(STREAM).hard=='HARD_SILENT'
        finally: await p.on_unload()
    asyncio.run(scenario())


@pytest.mark.parametrize('text,expected',[('小桃先安静一会',1),('小桃别回复，直接安静',0)])
def test_natural_ack_reuses_single_call_and_consumes_request(tmp_path,text,expected):
    async def scenario():
        p,ctx,calls,delivered=await start_public(tmp_path,social=True)
        try:
            assert (await p.receive(message=message(text=text)))['action']=='abort'
            for _ in range(100):
                if not p.worker.pending: break
                await asyncio.sleep(.01)
            assert len(calls)==1 and len(delivered)==expected
            assert p.service.peek(STREAM).soft=='SOFT_SILENT'
            assert '小桃' in calls[0][1]['content']
            await p.receive(message=message(text=text))
            assert len(calls)==1 and len(delivered)==expected
        finally: await p.on_unload()
    asyncio.run(scenario())


def test_ack_failure_and_restart_never_replays(tmp_path):
    async def scenario():
        p,ctx,_,_=await start_public(tmp_path)
        ctx.send.custom.side_effect=TimeoutError
        assert (await command(p,'/maihush mute'))[0]
        assert ctx.send.custom.await_count==1 and not p.receipts.pending
        await p.on_unload()
        p,ctx,_,delivered=await start_public(tmp_path)
        try:
            assert p.service.peek(STREAM).hard=='HARD_SILENT'
            await command(p,'/maihush mute')
            ctx.send.custom.assert_not_awaited()
            assert not delivered
        finally: await p.on_unload()
    asyncio.run(scenario())


def test_receipt_binding_one_use_version_and_no_text_exemption():
    permits=Receipts(); token=permits.issue(STREAM,3,2,'好，你们先聊。')
    ordinary=message(text='好，你们先聊。')
    assert permits.consume(ordinary,3,2) is None
    raw=deepcopy(ordinary); raw['raw_message']=[{'type':'dict','data':{KEY:token}}]
    assert permits.consume(raw,3,2)['modified_kwargs']['message']['raw_message']==[{'type':'text','data':'好，你们先聊。'}]
    assert permits.consume(raw,3,2)['action']=='abort'
    token=permits.issue(STREAM,3,2,'好'); raw['raw_message'][0]['data'][KEY]=token
    assert permits.consume(raw,4,2)['action']=='abort'


def test_normal_sends_no_cross_group_veto_or_storage_writes(tmp_path):
    async def scenario():
        p,ctx,_,_=await start_public(tmp_path)
        try:
            rows=ctx.chat.get_all_streams.return_value
            rows.append({**rows[0],'group_id':'888888888','session_id':'second','stream_id':'second'})
            raw=p.settings.model_dump(); raw['groups'].append({'group_id':'888888888','route_scope':'synthetic-test-scope'})
            await p.on_config_update('self',raw,'new')
            before=await p.io(lambda:list(p.service.db.conn.iterdump()))
            results=await asyncio.gather(*(p.before_send(message=message(
                stream=STREAM if i%2 else 'second',group='987654321' if i%2 else '888888888')) for i in range(100)))
            assert all(r['action']=='continue' for r in results)
            assert before==await p.io(lambda:list(p.service.db.conn.iterdump()))
        finally: await p.on_unload()
    asyncio.run(scenario())


def test_ambiguous_group_requires_bot_account():
    async def scenario():
        rows=[{'platform':'qq','is_group_session':True,'group_id':'987654321','account_id':a,
               'scope':None,'session_id':a,'stream_id':a} for a in ('123456789','234567890')]
        ctx=SimpleNamespace(chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=rows)))
        cfg=MaiHushConfig(groups=[{'group_id':'987654321'}])
        runtime,issues=await resolve(ctx,cfg)
        assert not runtime.routes and issues
        cfg=MaiHushConfig(groups=[{'group_id':'987654321','bot_account_id':'123456789'}])
        runtime,issues=await resolve(ctx,cfg)
        assert len(runtime.routes)==1 and not issues
    asyncio.run(scenario())


def test_restart_without_host_discovery_preserves_known_silence(tmp_path):
    async def scenario():
        p,ctx,_,_=await start_public(tmp_path,reply=False)
        await command(p,'/maihush mute')
        deadline=p.service.peek(STREAM).hard_until
        await p.on_unload()
        # The Host temporarily returns no sessions, but configured group stays.
        ctx.chat.get_all_streams.return_value=[]
        fresh=MaiHushPlugin(); fresh.set_plugin_config(p.settings.model_dump()); fresh._set_context(ctx)
        await fresh.on_load()
        try:
            assert fresh.service.peek(STREAM).hard_until==deadline
            assert (await fresh.before_send(message=message()))['action']=='abort'
        finally: await fresh.on_unload()
    asyncio.run(scenario())

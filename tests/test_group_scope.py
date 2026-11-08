"""Scope selection, per-group authorization and incremental Host discovery."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from maihush.host.config import MaiHushConfig
from maihush.host.routes import resolve
from maihush.host.messages import evidence
from maihush.domain.models import Denied
from tests.helpers import message
from tests.test_experience import start_public, command, STREAM


def row(group='987654321', account='123456789', scope=None, stream='first'):
    return dict(platform='qq', is_group_session=True, group_id=group, account_id=account,
                scope=scope, session_id=stream, stream_id=stream)


def test_default_all_exclusions_allowlist_and_private_sessions():
    async def run():
        rows=[row(), row(group='888888888', stream='second'),
              {**row(stream='private'), 'is_group_session':False},
              {**row(stream='other'), 'platform':'other'}]
        ctx=SimpleNamespace(chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=rows)))
        for scope,expected in [({},2), ({'excluded_groups':['888888888']},1),
                               ({'mode':'allowlist'},0),
                               ({'mode':'allowlist','group_ids':['987654321']},1),
                               ({'mode':'allowlist','group_ids':['987654321'],'excluded_groups':['987654321']},0)]:
            cfg=MaiHushConfig(scope=scope,groups=[{'group_id':'888888888','management':{'source':'local'}}])
            runtime,issues=await resolve(ctx,cfg)
            assert len(runtime.routes)==expected and not issues
    asyncio.run(run())


def test_management_replaces_global_and_specificity_is_order_independent():
    groups=[{'group_id':'987654321','management':{'source':'local','admin_accounts':['10002']}},
            {'group_id':'987654321','bot_account_id':'123456789','management':{'source':'identity'}},
            {'group_id':'987654321','bot_account_id':'123456789','route_scope':'','management':{'source':'local'}}]
    for rules in (groups,list(reversed(groups))):
        cfg=MaiHushConfig(groups=rules,management={'source':'local','admin_accounts':['10001']})
        assert cfg.management_for('888888888','123456789',None).admin_accounts==['10001']
        assert cfg.management_for('987654321','234567890',None).admin_accounts==['10002']
        assert cfg.management_for('987654321','123456789','named').source=='identity'
        assert cfg.management_for('987654321','123456789',None).admin_accounts==[]
    with pytest.raises(ValueError):
        MaiHushConfig(groups=[groups[1],{'group_id':'987654321','route_scope':'named'}])


@pytest.mark.parametrize('invalid', ['duplicate_stream','duplicate_identity','missing_account','missing_session'])
def test_untrusted_or_conflicting_host_bindings_are_rejected(invalid):
    async def run():
        rows=[row()]
        if invalid=='duplicate_stream': rows.append(row(group='888888888'))
        if invalid=='duplicate_identity': rows.append(row(stream='second'))
        if invalid=='missing_account': rows[0].pop('account_id')
        if invalid=='missing_session': rows[0].pop('session_id')
        ctx=SimpleNamespace(chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=rows)))
        with pytest.raises(Denied): await resolve(ctx,MaiHushConfig())
    asyncio.run(run())


def test_group_local_has_no_identity_fallback_and_identity_has_no_local_fallback(tmp_path):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,identity=True,reply=False)
        try:
            raw=p.settings.model_dump()
            raw['groups'][0]['management']={'source':'local','admin_accounts':['10002']}
            await p.on_config_update('self',raw,'local')
            assert not (await command(p,'/maihush mute',actor='10001'))[0]
            assert (await command(p,'/maihush mute',actor='10002',mid='local'))[0]
            ctx.api.call.assert_not_awaited()
            assert (await command(p,'/maihush unmute',actor='10002',mid='release'))[0]
            raw['groups'][0]['management']={'source':'identity'}
            raw['management']={'source':'local','admin_accounts':['10002']}
            await p.on_config_update('self',raw,'identity')
            ctx.api.call.side_effect=TimeoutError
            assert not (await command(p,'/maihush mute',actor='10002',mid='failed'))[0]
            assert p.service.peek(STREAM).hard=='NONE'
        finally: await p.on_unload()
    asyncio.run(run())


def test_new_command_discovers_group_without_resetting_existing_state(tmp_path):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,reply=False)
        try:
            await command(p,'/maihush mute')
            deadline=p.service.peek(STREAM).hard_until
            revision=p.service.revision
            p.history[STREAM].append('keep'); p.sent[STREAM].append(123)
            original_worker=p.worker
            ctx.chat.get_all_streams.return_value.append(row(group='888888888',scope='synthetic-test-scope',stream='new'))
            raw=message(stream='new',group='888888888',text='/maihush mute',actor='10001',mid='new-command')
            result=await p.command(message=raw,stream_id='new',platform='qq',group_id='888888888',user_id='10001',text='/maihush mute')
            assert result[0]
            assert p.service.peek('new').hard=='HARD_SILENT'
            assert p.service.peek(STREAM).hard_until==deadline
            assert p.history[STREAM]==type(p.history[STREAM])(['keep'],maxlen=20)
            assert list(p.sent[STREAM])==[123] and p.worker is original_worker
            assert p.service.revision==revision and p.active==p.service.config
            ctx.chat.get_all_streams.side_effect=TimeoutError
            await p.refresh_routes()
            assert p.service.peek(STREAM).hard_until==deadline
            assert len(p.active.routes)==2
        finally: await p.on_unload()
    asyncio.run(run())


def test_receive_triggers_discovery_and_exclusion_releases_only_selected_group(tmp_path):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,reply=False)
        try:
            await command(p,'/maihush mute')
            ctx.chat.get_all_streams.return_value.append(row(group='888888888',scope='synthetic-test-scope',stream='new'))
            raw=message(stream='new',group='888888888')
            await p.receive(message=raw)
            await p.discovery_task
            assert p.active.route('new')
            await p.io(p.service.command,evidence(p.active,{**raw,'message_id':'mute-new'}),'mute',60,True)
            settings=p.settings.model_dump();settings['scope']['excluded_groups']=['987654321']
            await p.on_config_update('self',settings,'exclude')
            assert not p.active.route(STREAM)
            assert p.service.peek('new').hard=='HARD_SILENT'
            assert (await p.before_send(message=message()))['action']=='continue'
            assert (await p.before_send(message=raw))['action']=='abort'
        finally: await p.on_unload()
    asyncio.run(run())


def test_restart_with_query_failure_restores_current_local_rule(tmp_path):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,reply=False)
        await command(p,'/maihush mute')
        deadline=p.service.peek(STREAM).hard_until
        await p.on_unload()
        from maihush.host.plugin import MaiHushPlugin
        settings=p.settings.model_dump();settings['groups'][0]['management']={'source':'local','admin_accounts':['10002']}
        ctx.chat.get_all_streams.side_effect=TimeoutError
        fresh=MaiHushPlugin();fresh.set_plugin_config(settings);fresh._set_context(ctx)
        await fresh.on_load()
        try:
            assert fresh.service.peek(STREAM).hard_until==deadline
            assert fresh.active.route(STREAM).admin_accounts==['10002']
            assert not (await command(fresh,'/maihush unmute',actor='10001',mid='old'))[0]
            assert (await command(fresh,'/maihush unmute',actor='10002',mid='new'))[0]
        finally: await fresh.on_unload()
    asyncio.run(run())


def test_same_group_accounts_and_scopes_do_not_share_silence(tmp_path):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,reply=False)
        try:
            ctx.chat.get_all_streams.return_value.extend([
                row(account='234567890',scope='synthetic-test-scope',stream='other-account'),
                row(scope='other-scope',stream='other-scope')])
            await p.refresh_routes()
            await command(p,'/maihush mute')
            assert p.service.peek(STREAM).hard=='HARD_SILENT'
            for stream in ('other-account','other-scope'):
                assert p.service.peek(stream).hard=='NONE'
                assert await p.io(p.service.guard,stream)
        finally: await p.on_unload()
    asyncio.run(run())


def test_final_policy_unknown_managed_denied_and_excluded_abstains(tmp_path):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,reply=False,scope={'excluded_groups':['888888888']})
        request={'id':'unknown','stream_id':'unknown','route':{'platform':'qq','account_id':'123456789',
                 'scope':None,'chat_type':'group','target_id':'777777777'}}
        ctx.behavior_context=lambda:{'request':request}
        try:
            assert (await p.evaluate_behavior())['reason']=='route_unavailable'
            request['route']['target_id']='888888888'
            assert (await p.evaluate_behavior())['decision']=='abstain'
            request['route']['target_id']='777777777';request['route']['platform']='other'
            assert (await p.evaluate_behavior())['decision']=='abstain'
        finally: await p.on_unload()
    asyncio.run(run())


def test_config_publication_preserves_concurrently_discovered_muted_group(tmp_path):
    async def run():
        p,ctx,_,_=await start_public(tmp_path,reply=False)
        try:
            settings=MaiHushConfig.model_validate({**p.settings.model_dump(),'default_minutes':30})
            prepared=await p.build_runtime(settings)
            ctx.chat.get_all_streams.return_value.append(row(group='888888888',scope='synthetic-test-scope',stream='new'))
            await p.refresh_routes()
            raw=message(stream='new',group='888888888')
            await p.io(p.service.command,evidence(p.active,raw),'mute',60,True)
            deadline=p.service.peek('new').hard_until
            await p.apply_config(prepared,settings)
            assert p.active.route('new')
            assert p.service.peek('new').hard_until==deadline
            assert p.active.hard.default_seconds==1800
        finally: await p.on_unload()
    asyncio.run(run())

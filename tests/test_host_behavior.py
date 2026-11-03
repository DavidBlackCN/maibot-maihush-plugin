import asyncio
from unittest.mock import Mock

from tests.test_experience import start_public, STREAM
from tests.helpers import message


def test_host_final_guard_counts_each_action_once_and_kwargs_cannot_authorize(tmp_path):
    async def scenario():
        plugin,ctx,_,_=await start_public(tmp_path)
        try:
            assert (await plugin.evaluate_behavior(request={'decision':'allow'}))['decision']=='deny'
            control={'defer_send_guard':True}
            ctx.behavior_context=lambda:control
            guard=Mock(wraps=plugin.service.guard)
            plugin.service.guard=guard
            for index in range(3):
                raw=message(mid=f'leaf-{index}')
                assert (await plugin.before_send(message=raw))['action']=='continue'
                assert guard.call_count==index  # The old hook must not consume another permit.
                control['request']={'id':f'host-{index}','stream_id':STREAM,'message_id':raw['message_id'],
                    'route':{'platform':'qq','account_id':'123456789','scope':'synthetic-test-scope',
                             'chat_type':'group','target_id':'987654321'}}
                first=await plugin.evaluate_behavior()
                assert first['decision']=='allow'
                assert await plugin.evaluate_behavior()==first
                assert guard.call_count==index+1
            control['request']['id']='wrong-target'
            control['request']['route']['account_id']='222222222'
            assert (await plugin.evaluate_behavior())['decision']=='deny'
            assert guard.call_count==3
        finally:
            await plugin.on_unload()
    asyncio.run(scenario())

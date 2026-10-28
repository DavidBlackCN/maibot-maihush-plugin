import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tests.helpers import config,message
from tests.test_worker import start

def test_finite_aliases_use_actual_actor_and_preserve_original_message_dedup(tmp_path):
    async def scenario():
        p,model=await start(tmp_path,config(decision={'enabled':False}))
        p.ctx.message=SimpleNamespace(get_by_id=AsyncMock(return_value=None))
        p.ctx.send=SimpleNamespace(text=AsyncMock(return_value=True))
        async def cmd(text,user,mid):
            raw=message(text,actor=user,mid=mid)
            await p.command(message=raw,text=text,user_id=user,stream_id=raw['session_id'],platform='qq',group_id='987654321')
        try:
            await cmd('/maihush 安静5分钟','10003','unauthorized')
            assert (await p.io(p.service.state,'synthetic-test-stream')).hard=='NONE'
            await cmd('/maihush 安静5分钟','10001','authorized')
            state=await p.io(p.service.state,'synthetic-test-stream');assert state.hard=='HARD_SILENT'
            await cmd('/maihush 安静5分钟','10001','authorized')
            assert (await p.io(p.service.state,'synthetic-test-stream')).hard_until==state.hard_until
            await cmd('/maihush 恢复聊天','10001','authorized')
            assert (await p.io(p.service.state,'synthetic-test-stream')).hard=='HARD_SILENT'
            await cmd('/maihush 恢复聊天','10001','new-command')
            assert (await p.io(p.service.state,'synthetic-test-stream')).hard=='NONE'
            assert not model.calls
        finally:await p.on_unload()
    asyncio.run(scenario())

"""Busy managed sends veto before Host timeout, without touching permits."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from maihush.host.plugin import MaiHushPlugin
from tests.helpers import config, message


def test_busy_managed_queue_vetoes_without_io_and_unrelated_route_still_passes():
    async def scenario():
        plugin=MaiHushPlugin()
        plugin.active=config(); plugin.ready=True
        plugin.service=SimpleNamespace(guard=object())
        plugin.io=AsyncMock(return_value=True)
        await plugin.control_gate.acquire()
        try:
            unrelated=message(stream='other-stream',group='99999')
            assert (await plugin.before_send(unrelated))['action']=='continue'
            replies=await asyncio.wait_for(asyncio.gather(*(plugin.before_send(message()) for _ in range(100))),.8)
            assert all(reply=={'action':'abort','reason':'MAIHUSH_BUSY'} for reply in replies)
            plugin.io.assert_not_awaited()
            assert plugin.control_gate.locked()
        finally:
            plugin.control_gate.release()
        assert (await plugin.before_send(message()))['action']=='continue'
        plugin.io.assert_awaited_once()
        assert not plugin.control_gate.locked()
    asyncio.run(scenario())


def test_cancelled_waiter_does_not_release_someone_elses_control_gate():
    async def scenario():
        plugin=MaiHushPlugin(); plugin.active=config()
        await plugin.control_gate.acquire()
        task=asyncio.create_task(plugin.before_send(message()))
        await asyncio.sleep(.01)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        else: raise AssertionError('cancellation must propagate')
        assert plugin.control_gate.locked()
        plugin.control_gate.release()
    asyncio.run(scenario())

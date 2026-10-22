from maibot_sdk import API, HookHandler, MaiBotPlugin
import asyncio

class Consumer(MaiBotPlugin):
    async def on_load(self): self.mode='normal'
    async def on_unload(self): pass
    async def on_config_update(self,scope,config_data,version): pass

    @API('call',public=True)
    async def call(self,route='',payload=None,**kwargs):
        return await self.ctx.api.call(route,version='1',**(payload or {}))

    @API('speak',public=True)
    async def speak(self,text='synthetic proactive',stream='synthetic-test-stream',**kwargs):
        return await self.ctx.send.text(text,stream,storage_message=False)

    @API('test_mode',public=True)
    async def test_mode(self,mode='normal',**kwargs):
        self.mode=mode
        return True

    @HookHandler('send_service.before_send',name='synthetic_fault_boundary',order='late',mode='blocking',error_policy='abort',timeout_ms=30)
    async def boundary(self,message=None,**kwargs):
        if self.mode=='error': raise RuntimeError('synthetic handler fault')
        if self.mode=='timeout': await asyncio.sleep(.3)
        if self.mode=='rewrite' and message.get('session_id')=='unmanaged-source':
            altered=dict(message); altered['session_id']=self.target
            altered['message_info']=dict(altered['message_info'])
            altered['message_info']['group_info']={'group_id':'987654321','group_name':'synthetic'}
            return {'action':'continue','modified_kwargs':{'message':altered}}
        return {'action':'continue'}

    @API('test_target',public=True)
    async def test_target(self,stream='',**kwargs):
        self.target=stream
        return True

def create_plugin(): return Consumer()

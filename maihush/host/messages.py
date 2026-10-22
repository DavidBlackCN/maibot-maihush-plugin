"""Only native command/hook entry points call this adapter, never public APIs."""
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from ..domain.models import Denied

@dataclass(frozen=True)
class Evidence:
    stream: str
    actor: str
    mid: str
    text: str
    segments: tuple
    fingerprint: str
    role: str


def route_for(config, message, *, outbound=False):
    if not isinstance(message,dict): raise Denied('MALFORMED_ROUTE')
    if not config.enabled: return None
    platform=message.get('platform')
    stream=message.get('session_id')
    route=config.route(stream)
    if not route and isinstance(platform,str) and platform != 'qq': return None
    info=message.get('message_info')
    group=info.get('group_info') if isinstance(info,dict) else None
    if group is not None and not isinstance(group,dict): raise Denied('MALFORMED_ROUTE')
    if not route:
        # Missing session on a managed account/scope cannot prove exclusion.
        candidates=[r for r in config.routes if r.enabled and message.get('account_id') in (None,'',r.bot_account_id) and message.get('scope') in (None,'',r.route_scope)]
        if candidates and (not group or any(r.group_id==group.get('group_id') for r in candidates)):
            if group is None and isinstance(info,dict) and 'group_info' in info: return None
            raise Denied('MALFORMED_ROUTE')
        if not isinstance(platform,str): raise Denied('MALFORMED_ROUTE')
        return None
    if not isinstance(group,dict) or (platform,message.get('account_id'),message.get('scope'),group.get('group_id')) != (route.platform,route.bot_account_id,route.route_scope,route.group_id):
        raise Denied('SCOPE_MISMATCH')
    return route


def evidence(config, message, envelope=None):
    route=route_for(config,message)
    if not route: raise Denied('UNSUPPORTED_SCOPE')
    info=message.get('message_info',{}); user=info.get('user_info',{})
    if not isinstance(user,dict): raise Denied('UNTRUSTED_CONTEXT')
    actor=user.get('user_id'); mid=message.get('message_id')
    flags=[message.get(k,False) for k in ('is_bot','is_system','is_notify','is_mai_message')]
    additional=info.get('additional_config',{})
    flags += [additional.get(k,False) for k in ('is_bot','is_system','is_notify','is_mai_message')] if isinstance(additional,dict) else [True]
    if any(type(x) is not bool or x for x in flags) or not isinstance(actor,str) or not re.fullmatch(r'[1-9][0-9]{4,19}',actor) or actor in [route.bot_account_id,*route.other_bot_accounts] or not isinstance(mid,str) or not 1<=len(mid)<=256:
        raise Denied('UNTRUSTED_CONTEXT')
    segments=message.get('raw_message')
    if not isinstance(segments,list) or any(not isinstance(x,dict) for x in segments): raise Denied('UNTRUSTED_CONTEXT')
    direct=''.join(s['data'] for s in segments if s.get('type')=='text' and isinstance(s.get('data'),str))
    if len(direct.encode('utf8'))>8192 or len(segments)>128:
        raise Denied('CONTEXT_TOO_LARGE')
    if envelope is not None:
        required={'stream_id':route.stream_id,'group_id':route.group_id,'platform':route.platform,'user_id':actor,'text':direct}
        if any(type(envelope.get(k)) is not str or envelope[k]!=v for k,v in required.items()): raise Denied('SCOPE_MISMATCH')
        if message.get('processed_plain_text') != direct or any(s.get('type')!='text' for s in segments): raise Denied('UNTRUSTED_CONTEXT')
    stable={'stream':route.stream_id,'author':actor,'mid':mid,'text':direct,'raw':segments,'route':route.identity(),'instance':config.instance_id}
    fingerprint=sha256(json.dumps(stable,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return Evidence(route.stream_id,actor,mid,direct,tuple(segments),fingerprint,route.role(actor))

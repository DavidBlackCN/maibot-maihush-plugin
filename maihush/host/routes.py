"""Discover official Host group sessions; never synthesize session identities."""
import asyncio
import re
from .config import Route, RuntimeConfig
from ..domain.models import Denied


def bind(settings, *, platform, account, scope, group, stream):
    management = settings.management_for(group, account, scope)
    return Route(enabled=True, platform=platform, adapter_namespace='host-qq',
                 bot_account_id=account, route_scope=scope or '', group_id=group, stream_id=stream,
                 admin_accounts=[a for a in management.admin_accounts if a != account]
                 if management.source == 'local' else [])


async def resolve(ctx, settings, previous=None):
    issues = []
    try:
        rows = await asyncio.wait_for(ctx.chat.get_all_streams(platform='qq'), 2)
        if not isinstance(rows, list): raise Denied('ROUTES_UNAVAILABLE')
    except Exception:
        rows = []
        issues.append('会话查询失败，保留已核验会话；暂不接纳未知会话')
    routes = []
    streams, identities = set(), set()
    for row in rows:
        if not isinstance(row, dict): raise Denied('ROUTES_UNAVAILABLE')
        if row.get('platform') != 'qq' or row.get('is_group_session') is not True: continue
        group = row.get('group_id')
        if not isinstance(group, str) or not re.fullmatch(r'[1-9][0-9]{4,19}', group):
            raise Denied('ROUTES_UNAVAILABLE')
        if not settings.scope.includes(group): continue
        stream, account, scope = row.get('session_id'), row.get('account_id'), row.get('scope')
        if (type(stream) is not str or not 1 <= len(stream) <= 256 or row.get('stream_id') != stream
                or type(account) is not str or not re.fullmatch(r'[1-9][0-9]{4,19}', account)
                or scope is not None and (type(scope) is not str or not scope or scope != scope.strip() or '*' in scope)):
            raise Denied('ROUTES_UNAVAILABLE')
        key = (account, scope, group)
        if stream in streams or key in identities: raise Denied('ROUTES_AMBIGUOUS')
        streams.add(stream); identities.add(key)
        routes.append(bind(settings, platform='qq', account=account, scope=scope, group=group, stream=stream))
    # A partial/failed scan is not an operator request to release silence.
    if previous:
        for route in previous.routes:
            if not settings.scope.includes(route.group_id): continue
            if route.stream_id in streams:
                current = next(r for r in routes if r.stream_id == route.stream_id)
                if current.identity() != route.identity(): raise Denied('ROUTES_AMBIGUOUS')
                continue
            key = (route.bot_account_id, route.route_scope or None, route.group_id)
            if key in identities: raise Denied('ROUTES_AMBIGUOUS')
            routes.append(bind(settings, platform=route.platform, account=route.bot_account_id,
                               scope=route.route_scope, group=route.group_id, stream=route.stream_id))
    routes.sort(key=lambda r: r.identity())
    raw = {'enabled': settings.plugin.enabled, 'routes': [r.model_dump() for r in routes],
           'selection': settings.model_dump(),
           'hard': {'default_seconds': settings.default_minutes * 60},
           'soft': {'max_seconds': settings.social.max_seconds},
           'decision': {'enabled': settings.social.enabled, 'task_name': settings.social.task_name},
           'autonomous': {'enabled': settings.autonomous_enabled}}
    return RuntimeConfig.model_validate(raw), issues


async def persona(ctx):
    async def read(key):
        try:
            value = await asyncio.wait_for(ctx.config.get(key, ''), 1)
            return value[:1000] if isinstance(value, str) else ''
        except Exception:
            return ''
    name, personality, style = await asyncio.gather(*(read(k) for k in
        ('bot.nickname', 'personality.personality', 'personality.reply_style')))
    return name.strip(), {'name': name, 'personality': personality, 'reply_style': style}

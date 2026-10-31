"""Bind configured groups to official Host sessions, never synthesize session IDs."""
import asyncio
import re
from .config import Route, RuntimeConfig
from ..domain.models import Denied


async def resolve(ctx, settings, previous=None):
    rows = await asyncio.wait_for(ctx.chat.get_all_streams(platform='qq'), 2) if settings.groups else []
    if not isinstance(rows, list):
        raise Denied('ROUTES_UNAVAILABLE')
    routes, issues = [], []
    for group in settings.groups:
        matches = [r for r in rows if isinstance(r, dict) and r.get('platform') == 'qq'
                   and r.get('is_group_session') is True and r.get('group_id') == group.group_id
                   and r.get('scope') == (group.route_scope or None)
                   and (not group.bot_account_id or r.get('account_id') == group.bot_account_id)]
        if len(matches) != 1:
            issues.append(f'群 {group.group_id}：' + ('存在多个账号，请填写机器人 QQ' if matches else '尚未发现会话，请在群内发送消息'))
            # Keep an already verified binding until the operator removes it.
            # Discovery failure must not erase an existing mute deadline.
            if previous:
                routes.extend(r for r in previous.routes if r.group_id == group.group_id
                              and r.route_scope == group.route_scope
                              and (not group.bot_account_id or r.bot_account_id == group.bot_account_id))
            continue
        row = matches[0]
        stream, account = row.get('session_id'), row.get('account_id')
        if (type(stream) is not str or not 1 <= len(stream) <= 256 or row.get('stream_id') != stream
                or type(account) is not str or not re.fullmatch(r'[1-9][0-9]{4,19}', account)):
            raise Denied('ROUTES_UNAVAILABLE')
        routes.append(Route(enabled=True, adapter_namespace='host-qq', bot_account_id=account,
                            route_scope=group.route_scope, group_id=group.group_id, stream_id=stream,
                            admin_accounts=[a for a in settings.management.admin_accounts if a != account]
                            if settings.management.source == 'local' else []))
    raw = {'enabled': settings.plugin.enabled, 'routes': [r.model_dump() for r in routes],
           'configured_groups': [g.model_dump() for g in settings.groups],
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

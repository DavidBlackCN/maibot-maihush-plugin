"""Host final policy uses the same state machine and serialized quota as send hooks."""
from hashlib import sha256
import time


def control(plugin):
    reader = getattr(plugin._ctx, 'behavior_context', None)
    return reader() if reader is not None else {}


def remember_receipt(plugin, message, result, state):
    modified = result.get('modified_kwargs', {}).get('message')
    if result.get('action') != 'continue' or not isinstance(modified, dict):
        return
    now = time.monotonic()
    plugin.control_receipts = {k: v for k, v in plugin.control_receipts.items() if v['expires'] > now}
    if len(plugin.control_receipts) >= 128:
        raise RuntimeError('BEHAVIOR_RECEIPT_CAPACITY')
    plugin.control_receipts[message['message_id']] = dict(stream=message['session_id'],
        version=state.version, revision=plugin.service.revision, expires=now+5,
        digest=sha256(modified['processed_plain_text'].encode()).hexdigest())


async def evaluate(plugin):
    request = control(plugin).get('request')
    result = {'version': 1, 'decision': 'deny', 'reason': 'untrusted_request'}
    if not isinstance(request, dict):
        return result
    if not plugin.ready or plugin.quiescing or plugin.active is None:
        return {**result, 'reason': 'not_ready'}
    target = request.get('route', {})
    stream = request.get('stream_id')
    route = plugin.active.route(stream)
    if route is None:
        configured = plugin.active.configured_groups or []
        if any(g['group_id'] == target.get('target_id') and target.get('chat_type') == 'group'
               and (not g.get('bot_account_id') or g['bot_account_id'] == target.get('account_id'))
               and (g.get('route_scope') or None) == target.get('scope') for g in configured):
            return {**result, 'reason': 'route_unavailable'}
        return {**result, 'decision': 'abstain', 'reason': 'not_managed'}
    if (target.get('platform'), target.get('account_id'), target.get('scope'), target.get('target_id')) != (
            route.platform, route.bot_account_id, route.route_scope or None, route.group_id):
        return {**result, 'reason': 'route_mismatch'}
    action_id = request.get('id')
    if not isinstance(action_id, str) or not action_id:
        return result
    now = time.monotonic()
    plugin.control_actions = {k: v for k, v in plugin.control_actions.items() if now-v[0] < 300}
    if action_id in plugin.control_actions:
        return plugin.control_actions[action_id][1]
    if len(plugin.control_actions) >= 8192:
        return {**result, 'reason': 'capacity'}
    state = plugin.service.peek(stream)
    receipt = plugin.control_receipts.pop(request.get('message_id'), None)
    acknowledged = bool(receipt and receipt['expires'] > now and receipt['stream'] == stream
                        and (receipt['version'], receipt['revision']) == (state.version, plugin.service.revision)
                        and receipt['digest'] == request.get('text_digest'))
    allowed = acknowledged or await plugin.io(plugin.service.guard, stream)
    result = {**result, 'decision': 'allow' if allowed else 'deny',
              'reason': 'acknowledgement' if acknowledged else 'group_policy',
              'revision': plugin.service.revision, 'state_version': state.version}
    plugin.control_actions[action_id] = (now, result)
    return result

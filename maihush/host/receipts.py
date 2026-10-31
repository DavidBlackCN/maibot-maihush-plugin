"""One-use custom envelopes, converted to text by the normal send Hook.

No text matching or group-wide bypass. A missing/expired permit cannot render
an acknowledgement. Other send guards still run and may veto the message.
"""
from copy import deepcopy
import secrets
import time

KEY = 'maihush_receipt'


class Receipts:
    def __init__(self):
        self.pending = {}

    def issue(self, stream, version, revision, text):
        token = secrets.token_hex(24)
        self.pending[token] = (stream, version, revision, text, time.monotonic()+5)
        return token

    def consume(self, message, version, revision):
        segments = message.get('raw_message', [])
        marked = [s for s in segments if isinstance(s, dict) and s.get('type') == 'dict'
                  and isinstance(s.get('data'), dict) and KEY in s['data']]
        if not marked:
            return None
        token = marked[0]['data'].get(KEY)
        permit = self.pending.pop(token, None) if isinstance(token, str) else None
        if (not permit or len(segments) != 1 or permit[0] != message.get('session_id')
                or (permit[1], permit[2]) != (version, revision) or time.monotonic() > permit[4]):
            return {'action': 'abort', 'reason': 'MAIHUSH_RECEIPT_EXPIRED'}
        modified = deepcopy(message)
        modified['raw_message'] = [{'type': 'text', 'data': permit[3]}]
        modified['processed_plain_text'] = permit[3]
        return {'action': 'continue', 'modified_kwargs': {'message': modified}}

    def clear(self):
        self.pending.clear()


def duration_text(seconds):
    if seconds % 3600 == 0:
        return f'{seconds // 3600} 小时'
    if seconds % 60 == 0:
        return f'{seconds // 60} 分钟'
    return f'{seconds} 秒'

"""Finite aliases inside the authenticated local MaiHush Command only."""
import re

def parts_for(text):
    parts=text.split(' ')
    if len(parts)!=2:return parts
    if parts[1] in ('恢复聊天','解除静音'):return ['/maihush','unmute']
    if parts[1] in ('状态','帮助'):return ['/maihush','status' if parts[1]=='状态' else 'help']
    match=re.fullmatch(r'(安静|静音|续期)([1-9][0-9]{0,4})(分钟|小时)',parts[1])
    if match:
        return ['/maihush','renew' if match[1]=='续期' else 'mute',str(int(match[2]))+('m' if match[3]=='分钟' else 'h')]
    return parts

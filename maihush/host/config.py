"""Strict operator configuration; no chat input enters this model."""
from typing import Literal
from maibot_sdk.config import PluginConfigBase
from pydantic import ConfigDict, Field, model_validator
import re

class Strict(PluginConfigBase):
    @model_validator(mode='before')
    @classmethod
    def reject_coercion(cls, value):
        if isinstance(value, dict):
            for key, item in value.items():
                field=cls.model_fields.get(key)
                if field and (field.annotation is bool or field.annotation == Literal[False]) and type(item) is not bool:
                    raise ValueError('strict boolean required')
        return value

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

class Route(Strict):
    enabled: bool = False
    platform: Literal['qq'] = 'qq'
    adapter_namespace: str = 'synthetic-test-adapter'
    bot_account_id: str = '123456789'
    route_scope: str = 'synthetic-test-scope'
    group_id: str = '987654321'
    stream_id: str = 'synthetic-test-stream'
    admin_accounts: list[str] = Field(default_factory=list)
    other_bot_accounts: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_route(self):
        if self.route_scope != self.route_scope.strip() or '*' in self.route_scope:
            raise ValueError('route_scope must be exact; empty means Host scope=None')
        if not self.adapter_namespace or not self.stream_id:
            raise ValueError('explicit route identity required')
        accounts = [self.bot_account_id, self.group_id, *self.admin_accounts, *self.other_bot_accounts]
        if any(not re.fullmatch(r'[1-9][0-9]{4,19}', a) for a in accounts):
            raise ValueError('canonical QQ strings required')
        actors = [self.bot_account_id, *self.admin_accounts, *self.other_bot_accounts]
        if len(set(actors)) != len(actors): raise ValueError('overlapping accounts')
        return self

    def identity(self):
        return (self.platform, self.adapter_namespace, self.bot_account_id, self.route_scope, self.group_id, self.stream_id)

    def role(self, actor):
        return 'admin' if actor in self.admin_accounts else 'member'

class Soft(Strict):
    enabled: bool = True
    default_seconds: int = Field(60, ge=10, le=300)
    max_seconds: int = Field(300, ge=10, le=300)
    episode_max_seconds: int = Field(600, ge=10, le=600)
    group_hour_budget_seconds: int = Field(900, ge=10, le=900)
    requester_hour_budget_seconds: int = Field(300, ge=10, le=300)
    requester_cooldown_seconds: int = Field(60, ge=10, le=600)
    group_candidate_cooldown_seconds: int = Field(10, ge=1, le=60)
    min_dwell_seconds: int = Field(10, ge=10, le=60)

class Restraint(Strict):
    min_send_interval_seconds: int = Field(30, ge=10, le=120)
    max_seconds: int = Field(120, ge=10, le=300)
    hour_budget_seconds: int = Field(600, ge=10, le=600)

class Recovery(Strict):
    duration_seconds: int = Field(60, ge=60, le=300)
    min_send_interval_seconds: int = Field(60, ge=10, le=300)
    invitation_cooldown_seconds: int = Field(60, ge=60, le=300)

class Hard(Strict):
    default_seconds: int = Field(1200, ge=1, le=86400)
    max_seconds: int = Field(86400, ge=1, le=86400)

class Autonomous(Strict):
    enabled: bool = False
    window_seconds: int = Field(120, ge=30, le=300)
    min_successful_sends: int = Field(8, ge=4, le=32)
    max_gap_seconds: int = Field(20, ge=1, le=60)
    min_bot_share_percent: int = Field(70, ge=50, le=100)
    duration_seconds: int = Field(60, ge=10, le=120)
    cooldown_seconds: int = Field(600, ge=300, le=3600)
    hour_budget_seconds: int = Field(180, ge=10, le=300)

class Decision(Strict):
    enabled: bool = False
    task_name: str = ''
    timeout_seconds: int = Field(8, ge=1, le=10)
    candidate_ttl_seconds: int = Field(15, ge=2, le=30)
    max_context_messages: int = Field(12, ge=1, le=20)
    max_context_bytes: int = Field(4096, ge=512, le=8192)
    max_calls_per_group_hour: int = Field(12, ge=1, le=60)
    max_calls_per_instance_hour: int = Field(60, ge=1, le=300)
    max_concurrent: int = Field(2, ge=1, le=2)
    queue_capacity: int = Field(64, ge=1, le=64)
    max_output_tokens: int = Field(256, ge=128, le=512)

class Storage(Strict):
    audit_retention_days: int = Field(7, ge=1, le=30)
    request_retention_hours: int = Field(24, ge=24, le=168)

class PluginSection(Strict):
    config_version: Literal['2'] = '2'
    enabled: bool = True

class RuntimeConfig(Strict):
    # Internal resolved routes and safety limits; not user configuration.
    plugin: PluginSection = Field(default_factory=PluginSection)
    config_version: int = Field(1, ge=1, le=1)
    enabled: bool = True
    instance_id: str = 'maihush-local'
    routes: list[Route] = Field(default_factory=list)
    soft: Soft = Field(default_factory=Soft)
    restraint: Restraint = Field(default_factory=Restraint)
    recovery: Recovery = Field(default_factory=Recovery)
    autonomous: Autonomous = Field(default_factory=Autonomous)
    hard: Hard = Field(default_factory=Hard)
    decision: Decision = Field(default_factory=Decision)
    storage: Storage = Field(default_factory=Storage)
    bot_names: list[str] = Field(default_factory=lambda: ['麦麦', 'maimai', 'maibot'])
    configured_groups: list[dict] | None = None

    @model_validator(mode='after')
    def relationships(self):
        s=self.soft; d=self.decision
        if not self.instance_id.strip(): raise ValueError('instance_id required')
        if not (s.min_dwell_seconds <= s.default_seconds <= s.max_seconds <= s.episode_max_seconds <= s.group_hour_budget_seconds):
            raise ValueError('soft caps inconsistent')
        if s.requester_hour_budget_seconds < s.max_seconds:
            raise ValueError('requester caps inconsistent')
        if self.hard.default_seconds > self.hard.max_seconds: raise ValueError('hard caps inconsistent')
        if d.candidate_ttl_seconds <= d.timeout_seconds or (d.enabled and not d.task_name.strip()): raise ValueError('explicit task and bounded TTL required')
        if self.autonomous.duration_seconds>min(self.autonomous.hour_budget_seconds,self.restraint.max_seconds): raise ValueError('autonomous caps inconsistent')
        streams=[r.stream_id for r in self.routes]
        keys=[(r.platform,r.bot_account_id,r.route_scope,r.group_id) for r in self.routes]
        if len(set(streams)) != len(streams) or len(set(keys)) != len(keys): raise ValueError('duplicate route')
        return self

    def route(self, stream):
        return next((r for r in self.routes if self.enabled and r.enabled and r.stream_id == stream),None)


class Group(Strict):
    group_id: str = Field('', description='受管理的 QQ 群号', json_schema_extra={'label': '群号'})
    bot_account_id: str = Field('', description='通常留空；同群有多个机器人账号时填写', json_schema_extra={'label': '机器人 QQ'})
    route_scope: str = Field('', description='通常留空；适配器设置了 scope 时填写相同值', json_schema_extra={'label': '适配器作用域'})

    @model_validator(mode='after')
    def identifiers(self):
        if self.group_id and not re.fullmatch(r'[1-9][0-9]{4,19}', self.group_id):
            raise ValueError('群号应为 QQ 数字字符串')
        if self.bot_account_id and not re.fullmatch(r'[1-9][0-9]{4,19}', self.bot_account_id):
            raise ValueError('机器人 QQ 格式不正确')
        if self.route_scope != self.route_scope.strip() or '*' in self.route_scope:
            raise ValueError('作用域必须准确填写')
        return self


class Management(Strict):
    source: Literal['identity', 'local'] = Field('identity', description='identity 使用 MaiIdentity 管理员；local 使用下方名单，查询失败不切换来源', json_schema_extra={'label': '管理员来源'})
    admin_accounts: list[str] = Field(default_factory=list, max_length=32, description='仅 local 模式生效，填写管理员 QQ', json_schema_extra={'label': '本地管理员'})

    @model_validator(mode='after')
    def accounts(self):
        if any(not re.fullmatch(r'[1-9][0-9]{4,19}', x) for x in self.admin_accounts):
            raise ValueError('管理员 QQ 格式不正确')
        if len(set(self.admin_accounts)) != len(self.admin_accounts):
            raise ValueError('管理员名单重复')
        return self


class Social(Strict):
    enabled: bool = Field(False, description='理解明确对机器人提出的安静或恢复邀请，需要模型任务', json_schema_extra={'label': '自然劝静'})
    task_name: str = Field('', description='填写 MaiBot 已配置的模型任务名', json_schema_extra={'label': '模型任务'})
    max_seconds: int = Field(300, ge=60, le=300, description='单次自然劝静最长秒数', json_schema_extra={'label': '最长劝静时间'})


class Reply(Strict):
    enabled: bool = Field(True, description='首次进入安静时回应一句；续期与自动到期不提醒', json_schema_extra={'label': '确认回应'})
    mute_text: str = Field('好，我先安静 {duration}。', max_length=120, description='管理员静音命令的回复；{duration} 为静音时长，留空不回复', json_schema_extra={'label': '命令静音回复'})

    @model_validator(mode='after')
    def template(self):
        from string import Formatter
        for _, name, spec, conversion in Formatter().parse(self.mute_text):
            if name is not None and (name != 'duration' or spec or conversion):
                raise ValueError('回复仅支持 {duration} 占位符')
        if any(ord(c) < 32 for c in self.mute_text):
            raise ValueError('回复请使用一行短句')
        return self


class MaiHushConfig(Strict):
    plugin: PluginSection = Field(default_factory=PluginSection)
    groups: list[Group] = Field(default_factory=list, max_length=32, description='只管理这里列出的群；空名单不管理任何群', json_schema_extra={'label': '受管理群'})
    management: Management = Field(default_factory=Management)
    default_minutes: int = Field(20, ge=1, le=1440, description='管理员命令未填写时长时采用，最长 24 小时', json_schema_extra={'label': '默认静音分钟'})
    social: Social = Field(default_factory=Social)
    reply: Reply = Field(default_factory=Reply)
    autonomous_enabled: bool = Field(False, description='实验性：连续多次发言且占比高时短暂减少发言，默认关闭', json_schema_extra={'label': '自动减少发言'})

    @model_validator(mode='after')
    def settings(self):
        if any(not g.group_id for g in self.groups):
            raise ValueError('请填写受管理群的群号')
        if self.social.enabled and not self.social.task_name.strip():
            raise ValueError('开启自然劝静需要填写模型任务名')
        keys = [(g.group_id, g.bot_account_id, g.route_scope) for g in self.groups]
        if len(set(keys)) != len(keys):
            raise ValueError('受管理群配置重复')
        return self

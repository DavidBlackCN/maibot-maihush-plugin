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
    owner_accounts: list[str] = Field(default_factory=list)
    admin_accounts: list[str] = Field(default_factory=list)
    other_bot_accounts: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_route(self):
        if not self.adapter_namespace or not self.route_scope or not self.stream_id:
            raise ValueError('explicit route identity required')
        accounts = [self.bot_account_id, self.group_id, *self.owner_accounts, *self.admin_accounts, *self.other_bot_accounts]
        if any(not re.fullmatch(r'[1-9][0-9]{4,19}', a) for a in accounts):
            raise ValueError('canonical QQ strings required')
        actors = [self.bot_account_id, *self.owner_accounts, *self.admin_accounts, *self.other_bot_accounts]
        if len(set(actors)) != len(actors): raise ValueError('overlapping accounts')
        return self

    def identity(self):
        return (self.platform, self.adapter_namespace, self.bot_account_id, self.route_scope, self.group_id, self.stream_id)

    def role(self, actor):
        return 'owner' if actor in self.owner_accounts else 'admin' if actor in self.admin_accounts else 'member'

class Soft(Strict):
    enabled: bool = True
    default_seconds: int = Field(60, ge=10, le=300)
    privileged_default_seconds: int = Field(120, ge=10, le=600)
    max_seconds: int = Field(300, ge=10, le=300)
    privileged_max_seconds: int = Field(600, ge=10, le=600)
    episode_max_seconds: int = Field(600, ge=10, le=600)
    group_hour_budget_seconds: int = Field(900, ge=10, le=900)
    requester_hour_budget_seconds: int = Field(300, ge=10, le=300)
    privileged_requester_hour_budget_seconds: int = Field(600, ge=10, le=600)
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
    model_name: str = ''
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

class Control(Strict):
    acknowledgements_enabled: Literal[False] = False

class Integrations(Strict):
    identity_h03_enabled: Literal[False] = False
    frequency_h02_enabled: Literal[False] = False
    receipt_h01_enabled: Literal[False] = False

class PluginSection(Strict):
    config_version: Literal['1'] = '1'
    enabled: bool = True

class MaiHushConfig(Strict):
    # Required by the actual 2.15 Runner config transport. The root integer
    # continues to version the business configuration from the design input.
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
    control: Control = Field(default_factory=Control)
    integrations: Integrations = Field(default_factory=Integrations)

    @model_validator(mode='after')
    def relationships(self):
        s=self.soft; d=self.decision
        if not self.instance_id.strip(): raise ValueError('instance_id required')
        if not (s.min_dwell_seconds <= s.default_seconds <= s.max_seconds <= s.privileged_max_seconds <= s.episode_max_seconds <= s.group_hour_budget_seconds):
            raise ValueError('soft caps inconsistent')
        if s.privileged_default_seconds > s.privileged_max_seconds or s.requester_hour_budget_seconds < s.max_seconds or s.privileged_requester_hour_budget_seconds < s.privileged_max_seconds:
            raise ValueError('requester caps inconsistent')
        if self.hard.default_seconds > self.hard.max_seconds: raise ValueError('hard caps inconsistent')
        if d.candidate_ttl_seconds <= d.timeout_seconds or (d.enabled and not d.task_name.strip()): raise ValueError('explicit task and bounded TTL required')
        if d.model_name: raise ValueError('select an existing task; concrete model override unsupported')
        if self.autonomous.duration_seconds>min(self.autonomous.hour_budget_seconds,self.restraint.max_seconds): raise ValueError('autonomous caps inconsistent')
        streams=[r.stream_id for r in self.routes]
        keys=[(r.platform,r.bot_account_id,r.route_scope,r.group_id) for r in self.routes]
        if len(set(streams)) != len(streams) or len(set(keys)) != len(keys): raise ValueError('duplicate route')
        return self

    def route(self, stream):
        return next((r for r in self.routes if self.enabled and r.enabled and r.stream_id == stream),None)

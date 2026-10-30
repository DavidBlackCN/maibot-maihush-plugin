# MaiHush

为 MaiBot 群聊提供有限时长的静音与发言克制，支持本地管理员控制及可选的自然语言劝静。

- 当前版本：`0.2.3`
- MaiBot 版本范围：`1.3.12–1.3.15`
- Plugin SDK 版本范围：`2.15.0–2.16.0`
- 项目地址：[DavidBlackCN/maibot-maihush-plugin](https://github.com/DavidBlackCN/maibot-maihush-plugin)

仅支持精确配对 **Host 1.3.12 / SDK 2.15.0**、**Host 1.3.15 / SDK 2.16.0**，不支持中间版本或交叉配对。无需私有 Host 补丁，不依赖 MaiCoordinate。

## 功能

### 管理员硬静音

在已配置的群内用 `/maihush mute 20m` 请求静音，`renew` 延长期限，`unmute` 解除本插件约束。权限仅由该路由的 `owner_accounts` / `admin_accounts` 决定，群管标签、昵称、关系和 MaiIdentity 权限不替代这两份名单。

硬静音独立于 MaiVita 睡眠。唤醒 Vita 不解除静音，解除静音也不唤醒 Vita。没有永久静音，最长 24 小时；控制操作默认无成功群回执。

### 自然语言劝静（可选）

开启 `decision.enabled` 并指定 Host 已有模型任务后，插件可判断明确劝静、间接抱怨和重新邀请。普通成员也能提出软请求，管理员建议得到优先关注，但模型不保证服从；间接抱怨最多触发克制，单独 @ 不恢复。

普通软静默最多 300 秒，管理级自然语言请求最多 600 秒，均受群与请求者预算约束。邀请恢复仍检查请求来源和最短停留时间，不能借邀请撤销他人的有效管理级约束。默认不开启，不产生模型调用。

### 无模型自主克制（可选）

`autonomous.enabled` 独立于模型开关，根据正常发送链确认成功的消息统计过度发言。默认阈值为 120 秒窗口内至少 8 次连续成功发送、相邻间隔不超过 20 秒、观察到的机器人占比至少 70%，满足后有限时长降频发言，不进入硬静音，也不调用模型。

### 发送守卫与状态保留

正常 Host 发送链上的文字、媒体、分段消息和插件主动消息都接受守卫检查。约束期限、预算与冷却持久保存，重载不自动重置；恢复后不报到、不补发旧消息。只读公开约束 API 可供其他插件查询，不授予静音控制或发送权限。

## 安装

### 1. 放置插件

将插件放入 MaiBot 的 `plugins`，同一 ID 只安装一份，使用 Bot 自己的 Python 环境：

```text
MaiBot/
└── plugins/
    └── maibot-maihush-plugin/
        ├── _manifest.json
        ├── plugin.py
        └── maihush/
```

### 2. 配置受管理群

首次加载默认没有受管理群和管理员。[config.example.toml](config.example.toml) 中的路由全部是**停用的合成示例**。填写真实 Bot QQ、群号、Host 已有 stream_id、实际 scope 和本地适配器标签，确认管理员名单后才设置该路由 `enabled = true`。

`route_scope = ""` 只匹配 Host 的 `scope=None`，不是通配符。Hush 不自动接管其他群；新增路由需要重启，已有路由身份不能在线改绑。部署须保证 account/scope/session 对应唯一可信适配器。

### 3. 启动并查看

启动后在配置群里发送 `/maihush status`。暂不启用 `decision` 和 `autonomous` 即可使用管理员硬静音。状态和错误消息也受正常发送守卫限制，没收到消息不等于操作失败或成功；可查看服务日志与持久审计。

### 从旧版本更新

停机备份代码、配置及完整 SDK 数据目录，再替换代码。数据库位于 `data_dir/maihush.sqlite3`，保留期限、撤销和审计状态；不删库重建。根级 `config_version = 1` 与 `[plugin] config_version = "1"` 必须同时保留，前者为业务版本，后者供 Runner 使用。

根级 `enabled = false` 会清除本插件约束；Host 卸载或禁用则移除守卫，重新启用仍读取持久期限。不要用这两种不同操作互相替代。跨实例或已绑定路由的变更需离线迁移，当前无自动迁移工具。回滚应停机成套恢复匹配的代码、配置与数据。

## 命令

只作用于消息所在的已启用群，不支持指定他群或全局操作。

| 命令 | 权限与行为 |
| --- | --- |
| `/maihush help`、`/maihush status` | 当前群成员查看帮助或状态 |
| `/maihush mute [20m]` | 本地管理员请求硬静音，默认 1200 秒，不缩短已有期限 |
| `/maihush renew 10m` | 本地管理员续期已有硬静音；取旧期限和“当前时刻加时长”的较大值，不累加 |
| `/maihush unmute` | 本地管理员解除本插件约束，进入软请求冷却，不自动报到 |
| `/maihush diagnostics` | 本地管理员查看健康、版本与开关，不公开名单 |
| `/maihush 安静5分钟` | 硬静音中文写法，权限不变 |
| `/maihush 续期5分钟` | 续期中文写法，权限不变 |
| `/maihush 恢复聊天` | 解除约束中文写法，权限不变 |

英文时长接受 ASCII 正整数和可选 `s` / `m` / `h`，不超过 86400 秒。命令成功处理不等于平台消息已送达。

## 配置

完整字段、范围、默认值与跨字段约束见 [config.example.toml](config.example.toml)。

### 路由与管理员

```toml
[[routes]]
# 先填写真实字段再启用；以下账号和 ID 仅为结构示例
enabled = false
platform = "qq"
adapter_namespace = "host-qq"
bot_account_id = "123456789"
# 若真实 Host scope=None 则留空；否则填写精确 scope
route_scope = ""
group_id = "987654321"
stream_id = "替换成Host实际会话ID"
# QQ 账号均为字符串，不能互相重复或包含 Bot 自身
owner_accounts = []
admin_accounts = []
other_bot_accounts = []
```

### 软约束、硬静音与恢复

```toml
[hard]
# 默认 20 分钟，最多 24 小时
default_seconds = 1200
max_seconds = 86400

[restraint]
# 克制时发送至少间隔 30 秒
min_send_interval_seconds = 30
max_seconds = 120
hour_budget_seconds = 600

[recovery]
# 恢复期 60 秒，发送至少间隔 60 秒
duration_seconds = 60
min_send_interval_seconds = 60
invitation_cooldown_seconds = 60
```

`[soft]` 设置普通与管理级请求时长、连续静默上限、群和请求者滚动小时预算及冷却。降低上限时须同时调整默认时长和预算，避免配置不一致。硬静音独立于 NORMAL / RESTRAINED / SOFT_SILENT / RECOVERING 四种软状态。

### 模型裁判

```toml
[decision]
# 显式启用才调用模型；先在 Host 配置好任务
enabled = false
task_name = ""
# 不支持直接覆盖具体模型，必须留空
model_name = ""
timeout_seconds = 8
# 必须大于 timeout_seconds
candidate_ttl_seconds = 15
```

可配置每群/实例调用预算、队列、上下文和输出上限。同群已有候选时新候选会拒绝，不自动重试。模型超时、错误格式、过期或版本失配不会新增静默。

### 自主克制与存储

```toml
[autonomous]
# 独立的本地统计开关，不调用模型
enabled = false
window_seconds = 120
min_successful_sends = 8

[storage]
# 审计保留天数与请求保留小时数
audit_retention_days = 7
request_retention_hours = 24
```

`[control] acknowledgements_enabled` 和 `[integrations]` 的 H01/H02/H03 开关只能为 false。没有静音回执豁免、全局频率覆盖或通用跨插件授权。

版本变化见 [UPDATE_NOTES.md](UPDATE_NOTES.md)。

## 已知限制

这不是平台级禁言。Host 跳过守卫、插件卸载或熔断、直接平台发送、后置 Hook 改路由等情况不在拦截保证内；已通过守卫的在途消息不可撤回。存储错误或时钟回跳需停机核对数据，不能清库“解锁”。自然语言可能漏判；未观察到的消息会使自主克制统计不完整。

## 鸣谢

感谢 MaiBot、插件 SDK 及社区文档。本插件独立实现，参考 MaiVita / MaiIdentity 的公开契约，未复制旧 GroupMuter 实现。

## 许可

[MIT](LICENSE)。

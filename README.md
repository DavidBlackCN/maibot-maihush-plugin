# MaiHush

让 MaiBot 在 QQ 群里安静一会：管理员可以定时静音，也可以开启自然劝静，让麦麦理解“先安静一会”等请求。

当前版本 **0.4.0**，已适配 MaiBot 1.4.1／Plugin SDK 2.21.0。完整兼容范围见 [_manifest.json](_manifest.json)。

## 安装与设置

将插件放入所选 Bot 实例的 `plugins/maibot-maihush-plugin/` 后启动，在 WebUI 设置管理员来源即可。默认管理当前实例中所有已核验的 QQ 群会话，无需逐个填写群号；不管理私聊。也可编辑生成的 `config.toml` 后重载：

```toml
[management]
source = "identity"
```

机器人账号、scope 和会话信息由 MaiBot 查询，不需要填写会话 ID。同群的不同机器人或作用域分别保存静音状态。新群消息会触发发现，定时扫描每 30 秒补充；会话尚未核验时拒绝管理操作和受管发送，稍后可重新操作。自动纳管不会自动静音，也不会自动开启自然劝静。

`identity` 使用 MaiIdentity 的管理员名单。独立使用本插件时，改用本地名单：

```toml
[management]
source = "local"
admin_accounts = ["123456789"]
```

QQ 群管理员身份、昵称和固定身份称呼都不会自动授予权限。权限查询失败时拒绝管理操作。

## 管理范围与群级权限

只排除少数群时：

```toml
[scope]
mode = "all"
excluded_groups = ["888888888"]
```

只管理指定群时，使用 `mode = "allowlist"` 并填写 `group_ids = ["987654321"]`；此模式下空名单不管理任何群。排除名单始终优先。

`groups` 是可选的群级例外规则，不决定哪些群纳入管理。没有填写规则的群继承全局管理员设置。例如全局使用 Identity，某个群改用独立本地名单：

```toml
[management]
source = "identity"

[[groups]]
group_id = "987654321"

[groups.management]
source = "local"
admin_accounts = ["123456789"]
```

群级 `management` 整体替换全局设置，不合并名单；`local` 空名单表示无人拥有管理权限。`identity` 查询失败不会回退到本地名单。未填写群级 `management` 则继承全局设置。

群规则默认匹配该群全部账号和作用域。需要分别设置时，在对应的 `[[groups]]` 中填写 `bot_account_id` 或 `route_scope`：不填 scope 匹配全部作用域，`route_scope = ""` 仅匹配无 scope 的会话。限定条件更多的规则优先，同优先级且存在重叠的规则会被拒绝。选中的规则未填写 `management` 时使用全局设置。

## 命令

命令只作用于当前受管理群。

| 命令 | 作用 |
| --- | --- |
| `/maihush help` | 查看帮助 |
| `/maihush status` | 查看状态 |
| `/maihush mute 20m` | 管理员静音，省略时长默认 20 分钟 |
| `/maihush renew 10m` | 将期限至少延长到当前时刻的 10 分钟后 |
| `/maihush unmute` | 管理员解除 Hush 的限制 |
| `/maihush diagnostics` | 管理员查看运行情况 |

支持 `s` 秒、`m` 分钟、`h` 小时，最长 24 小时。也可用 `/maihush 安静5分钟`、`/maihush 续期5分钟`、`/maihush 恢复聊天`。

首次命令静音默认回应一句，可修改或关闭：

```toml
[reply]
enabled = true
mute_text = "好，我先安静 {duration}。"
```

续期和自然到期不提醒，重启保留期限，恢复后不补发消息。确认回复失败不影响静音。

## 自然劝静

默认关闭。开启前在 MaiBot 中配置模型任务：

```toml
[social]
enabled = true
task_name = "你的模型任务名"
max_seconds = 300
```

普通成员可明确对麦麦说“先安静一会”“少说两句”，也可邀请“可以回来聊了”。插件参考机器人名字和 @ 判断对象，再由模型决定是否接受。首次接受时可自然回应一句；明确要求不回复时直接安静。回应与判断使用同一次模型调用，不改变生活插件的活动。

自然请求不能解除管理员静音。每群每小时最多 12 次判断，实例最多 60 次；静音累计时间与重复请求也有上限。判断失败不新增静音。交由 Hush 处理的明确请求不会再交给核心生成另一条回复。

`autonomous_enabled = true` 可开启实验性的自动减少发言，默认关闭。它按短时发送次数估计，合并紧邻的消息片段；并非精确的对话轮次统计，不需要模型。

## 使用边界与排错

- **命令没生效**：核对管理范围及当前群的管理员来源；查看日志中是否提示会话查询失败或身份冲突。正常的多账号会话会自动分别识别。
- **静音时看不到状态**：状态、帮助和错误回复仍受静音约束，状态也会写入 MaiBot 日志。其他插件可通过 `get_public_constraints` 查询已知会话的公开状态。
- **解除后仍不说话**：生活插件的睡眠限制独立生效。Hush 不负责唤醒，也不停止 QQ 空间活动；确认回应同样不能绕过已接入的睡眠检查。
- 自然降频按发送间隔执行，允许有限的短消息连续发送；不会调节主程序模型调用频率，不保证节省模型费用。
- 普通 Host 上限制正常消息发送；配套统一控制 Host 和 QQ 连接器可同时约束直接戳一戳、表情回应、转发及文件发送。MaiTogether 的补丁版本须与 Host 匹配，安装前核对其支持范围。这不是 QQ 平台禁言，已经提交的请求允许收尾。
- 普通 Host 上 Smart Poke 的直接回戳仍可能绕过静音；要求完全安静时，应使用配套统一控制组合，或停用该功能。独立 HTTP、QQ 空间及其他未纳入的出口不受此组合保护。
- 将群加入排除名单，或从 allowlist 的允许名单移除，会解除对该群的管理；删除群级规则仅恢复全局权限设置。会话查询暂时失败不会解除已有静音。仅卸载插件不删除保存的期限，普通 Host 卸载期间不执行拦截；统一控制将 Hush 配为必需策略时，卸载后受管动作会被拒绝。

[完整配置示例](config.example.toml) · [版本变化](UPDATE_NOTES.md) · [项目仓库](https://github.com/DavidBlackCN/maibot-maihush-plugin)

第三方插件接入请参阅 [公开 API 与发送约束](docs/API.md)。

感谢 MaiBot、插件 SDK 及社区文档。许可证：[MIT](LICENSE)。

# MaiHush

让 MaiBot 在指定 QQ 群里安静一会：管理员可以定时静音，也可以开启自然劝静，让麦麦理解“先安静一会”等请求。

当前版本 **0.3.0**。兼容范围见 [_manifest.json](_manifest.json)。

## 安装与设置

将插件放入 `MaiBot/plugins/maibot-maihush-plugin/` 后启动，在 WebUI 添加受管理群并设置管理员来源。也可编辑生成的 `config.toml` 后重载：

```toml
[[groups]]
group_id = "987654321"

[management]
source = "identity"
```

替换群号。默认不管理任何群；机器人账号和会话信息由 MaiBot 查询，不需要填写会话 ID。首次加入的群发送消息后，最多等待约 30 秒识别。同群有多个机器人账号时，额外填写 `bot_account_id`；适配器使用了 scope 时填写 `route_scope`。

`identity` 使用 MaiIdentity 的管理员名单。独立使用本插件时，改用本地名单：

```toml
[management]
source = "local"
admin_accounts = ["123456789"]
```

QQ 群管理员身份、昵称和固定身份称呼都不会自动授予权限。权限查询失败时拒绝管理操作。

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

普通成员可明确对麦麦说“先安静一会”“少说两句”，也可邀请“可以回来聊了”。插件参考机器人名字和 @ 判断对象，再由模型决定是否接受。首次接受时可自然回应一句；明确要求不回复时直接安静。回应与判断使用同一次模型调用，不改变 MaiVita 的活动。

自然请求不能解除管理员静音。每群每小时最多 12 次判断，实例最多 60 次；静音累计时间与重复请求也有上限。判断失败不新增静音。交由 Hush 处理的明确请求不会再交给核心生成另一条回复。

`autonomous_enabled = true` 可开启实验性的自动减少发言，默认关闭。它按短时发送次数估计，合并紧邻的消息片段；并非精确的对话轮次统计，不需要模型。

## 使用边界与排错

- **命令没生效**：核对群号、管理员来源；查看日志中是否提示会话未发现或多账号冲突。
- **静音时看不到状态**：状态、帮助和错误回复仍受静音约束，状态也会写入 MaiBot 日志。其他插件可通过 `get_public_constraints` 查询已知会话的公开状态。
- **解除后仍不说话**：Vita 睡眠独立生效。Hush 不负责唤醒，也不停止 QQ 空间活动；确认回应同样不能绕过睡眠限制。
- 自然降频按发送间隔执行，允许有限的短消息连续发送；不会调节主程序模型调用频率，不保证节省模型费用。
- 限制覆盖 MaiBot 正常发送流程。直接调用平台接口的插件、已经提交平台的消息不在控制范围；这不是 QQ 平台禁言。
- 从配置移除一个群会解除对该群的管理；仅卸载插件不删除保存的期限，卸载期间不执行拦截。

[完整配置示例](config.example.toml) · [版本变化](UPDATE_NOTES.md) · [项目仓库](https://github.com/DavidBlackCN/maibot-maihush-plugin)

感谢 MaiBot、插件 SDK 及社区文档。许可证：[MIT](LICENSE)。

# MaiHush

控制 MaiBot 在指定 QQ 群中的发言：管理员可以定时静音，也可让机器人根据聊天情况少说几句。

- **定时静音**：管理员用命令静音、续期或恢复聊天，最长 24 小时。
- **自然语言劝静**：可选功能，通过模型理解“先安静一会儿”等请求。
- **自动减少发言**：可选功能，在机器人连续发言过多时降低频率，不调用模型。

当前版本 **0.2.3**。支持以下 MaiBot／Plugin SDK 组合：**1.3.18／2.19.0**、**1.3.15／2.16.0**、**1.3.12／2.15.0**。

## 安装与配置

1. 将插件放入 `MaiBot/plugins/maibot-maihush-plugin/`，目录内应直接包含 `plugin.py`、`_manifest.json` 和 `maihush/`。
2. 启动 MaiBot，编辑生成的 `config.toml`，为需要管理的群添加一条 `[[routes]]` 配置。
3. 填好群信息和管理员 QQ，启用该配置并重启 MaiBot。在目标群发送 `/maihush status` 检查状态。

默认不管理任何群。下面是填写示例，**请替换账号和会话信息后再启用**：

```toml
[[routes]]
enabled = false
platform = "qq"
adapter_namespace = "host-qq"
bot_account_id = "123456789"
route_scope = ""
group_id = "987654321"
stream_id = "填写Host中该群的实际会话ID"
owner_accounts = ["123456780"]
admin_accounts = []
other_bot_accounts = []
```

- `bot_account_id` 填机器人 QQ，`group_id` 填群号。
- `stream_id` 从 MaiBot 的目标群会话中核对，不能直接填群号。
- `route_scope` 与适配器上报值一致；Host 未设置 scope 时留空。
- `adapter_namespace` 填本地适配器标签。
- `owner_accounts` 和 `admin_accounts` 填有权使用静音命令的 QQ，各名单不要重复，也不要填机器人自己。

QQ 群管理员身份不会自动获得本插件权限；MaiIdentity 的管理员设置也不替代这里的名单。完整字段说明见 [配置示例](config.example.toml)。

## 命令

命令只作用于当前已配置的群。

| 命令 | 用途 |
| --- | --- |
| `/maihush help` | 查看帮助 |
| `/maihush status` | 查看当前状态 |
| `/maihush mute 20m` | 管理员静音 20 分钟，省略时长也为 20 分钟 |
| `/maihush renew 10m` | 管理员将静音期限至少延长到当前时刻的 10 分钟后 |
| `/maihush unmute` | 管理员解除本插件的静音与发言限制 |
| `/maihush diagnostics` | 管理员查看插件运行情况 |

也支持 `/maihush 安静5分钟`、`/maihush 续期5分钟`、`/maihush 恢复聊天`。英文命令的时长可用 `s`（秒）、`m`（分）、`h`（小时）；续期不会缩短已有期限。

静音操作成功后不会额外发送群提示，恢复后也不会补发之前的消息。

## 可选功能

**自然语言劝静**默认关闭。开启前先在 MaiBot 中配置模型任务，再填写该任务名：

```toml
[decision]
enabled = true
task_name = "填写已有模型任务名"
```

开启后会调用模型。普通成员也可请求机器人暂时安静，但自然语言请求不能解除管理员命令设置的静音。模型判断失败时不会新增静音。

**自动减少发言**默认关闭，开启不需要模型：

```toml
[autonomous]
enabled = true
```

机器人在短时间内连续发言、且占群内消息比例较高时，会暂时放慢发言。时长、频率和调用额度可在配置示例中查看，初次使用保持默认即可。

## 常见问题

**命令不生效？** 检查该群配置是否启用，机器人 QQ、群号、scope 和会话 ID 是否正确，发令账号是否在管理员名单中。新增受管理群后需要重启。

**看不到状态回复？** 状态回复同样受静音限制，请查看 MaiBot 日志。

**解除静音后仍不说话？** MaiVita 的睡眠独立生效，解除 Hush 静音不会唤醒 Vita，唤醒 Vita 也不会解除 Hush 静音。

这是机器人自身的发送限制，不是 QQ 平台禁言。卸载插件后限制不再生效；绕过 MaiBot 正常发送流程的插件消息不在控制范围内，已经发出的消息也无法撤回。配置根级 `enabled = false` 会清除当前限制；仅卸载插件不会清除保存的期限。

[版本变化](UPDATE_NOTES.md) · [项目仓库](https://github.com/DavidBlackCN/maibot-maihush-plugin)

感谢 MaiBot、插件 SDK 及社区文档。许可证：[MIT](LICENSE)。

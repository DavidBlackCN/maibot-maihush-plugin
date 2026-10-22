# MaiHush 0.1.0

MaiBot 群聊发言约束插件，从零实现。[源码仓库](https://github.com/DavidBlackCN/maibot-maihush-plugin)。正式名称、Python 包及目录为 `maihush`，许可证 MIT。

实际验收版本为 **Host 1.3.12 / maibot-plugin-sdk 2.15.0**。manifest 声明的范围为 Host 1.3.12–1.99.99、SDK 2.15.0–2.99.99；运行时仍只接受上述固定验收版本，其他版本未验证。

解压安装包到独立插件目录。首次加载默认没有受管理群、没有管理员、没有模型调用。根据 `config.example.toml` 配置 QQ 机器人账号、实际 scope、已有真实 stream_id 和当前群 ID；账号均为字符串。示例全为合成值，不能直接部署。新增路由需要重启；已有路由身份不可在线改绑。`[plugin].config_version = "1"` 是固定 Runner 必需的传输配置，根级 `config_version = 1` 是业务版本。根级 `enabled=false` 会明确清除本插件约束；Host 的插件卸载/禁用会移除守卫，重新启用仍读取持久化期限。

命令只操作真实消息所在的当前群。本地 `owner_accounts` / `admin_accounts` 是独立运维信任根；群管标签、昵称、关系、模型、MaiIdentity JSON proof 均不授权。适配器 namespace 是本地绑定标签，Host envelope 没有可单独认证的 namespace；运维必须保证 account/scope/session 对应唯一已审计适配器。受信 Host 和已安装插件属于部署边界，不能隔离恶意同进程插件。

| 命令 | 权限与行为 |
| --- | --- |
| `/maihush help`、`status` | 当前启用群成员；查询也经过正常发送守卫 |
| `/maihush mute [20m]` | 当前路由本地管理员；默认 1200 秒，不能缩短已有更长期限 |
| `/maihush renew 10m` | 已有硬静默；取旧期限与当前时刻加时长的较大值，不累加 |
| `/maihush unmute` | 清除自己的约束，设置 60 秒软请求冷却，无自动报到 |
| `/maihush diagnostics` | 本地管理员；版本、健康态和功能开关，不含名单 |

时长只接受 ASCII 正整数加可选 `s/m/h`，硬上限 86400 秒，无永久静默，不支持他群或全局参数。控制动作默认没有成功群回执。Host 的组件调用成功仅代表处理函数执行成功，不代表业务授权或平台送达；本地 SQLite 审计是操作结果依据。错误消息也会被静默守卫拦截。

自然语言功能默认关闭。只有明确选择 **Host 已存在的任务**，设置 `decision.task_name` 并开启 `decision.enabled` 后才调用 SDK 模型，不改全局模型配置。0.1.0 不接受具体模型覆盖 `model_name`。普通人与管理员都经过有界社交裁判，管理员优先关注但不保证服从；无随机顶嘴。直接文本、引用、转发、否定、对他人发言和重新邀请保留来源，歧义采用保守筛选，可能漏掉有效请求。单独 @ 不恢复。

软态为 NORMAL / RESTRAINED / SOFT_SILENT / RECOVERING；硬态独立为 NONE / HARD_SILENT。普通软静默单次最多 300 秒，管理员自然语言最多 600 秒；episode 最多 600 秒，群滚动小时并集最多 900 秒，请求者分别最多 300/600 秒。事务仅预留增量，提前恢复仅释放未发生区间；请求、额度、调用预算、冷却和期限重启保留。克制与恢复间隔默认 30/60 秒，分段可能仅第一段获准。模型超时、错误 JSON、未知字段、过期或版本失配均不新增静默。

正常 SendService 路径的文本、媒体、分段和其他插件主动消息都受 BLOCKING/ABORT 守卫约束，没有文本、空串、tag 或 receipt 豁免。after_send 仅统计，不退款或解锁。H01/H02/H03 首版只允许 false。没有公共硬写 API 或 silence Tool；get_contract@1 仅描述，get_state@1 一律 UNTRUSTED_CONTEXT。解除 MaiHush 不唤醒 MaiVita、不改频率。

已知 Host 边界：注册且被调用的 Hook 异常/超时可 ABORT；熔断、Runner 缺席/崩溃、卸载、禁用、关闭和无目标可能跳过守卫。已通过守卫的在途发送不可撤回，后续 Hook 改路由及直接 Platform IO/网关发送不在保证内。处理器级 ABORT 的意外异常也可能影响无关路由。插件不是平台禁言或进程故障时的全局封锁。

数据库固定保存在 SDK `data_dir/maihush.sqlite3`，单写入者锁、未知 schema/损坏库拒绝启动，不自动删库。运行中存储异常进入 DEGRADED，拒绝新写，已知约束按原期限到期；无可信状态时保守拦截。时钟后跳不补发额度；修复需本地停机、核对时钟和数据后重载。跨实例/已绑定路由的变更需要独立离线迁移，首版不提供自动迁移工具。卸载不删除持久化状态。

测试源码与固定源锁在 `tests/`，原生隔离脚本在 `tools/run_isolated.py`，生成证据必须指定仓库外目录。T0 预置模型端口不证明 IPC 或语义效果；T1/T2 使用原始 Runner、Host 能力/分发/发送链，末端为计数测试驱动。联合检查只覆盖 MaiVita 睡眠否决、MaiHush 硬约束及 MaiIdentity 授权拒绝，生活状态为隔离合成 fixture。未完成真实 QQ、真实模型效果、完整 bot CLI、长期运行与生产验收；不报告模型准确率。

接口依据为 [官方插件文档](https://docs.mai-mai.org/plugin/)、固定 [MaiBot 提交](https://github.com/Mai-with-u/MaiBot/tree/5c7637e6913c84d1c3d5e742fa9e95a7e70d3501) 与 [SDK 2.15.0](https://pypi.org/project/maibot-plugin-sdk/2.15.0/)。只读核对 MaiVita / MaiIdentity 契约，未复制其实现或旧 GroupMuter 代码，Host/SDK 不随包分发。

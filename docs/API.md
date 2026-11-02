# MaiHush 公开 API v1

调用形式：`ctx.api.call("davidblackcn.maibot-maihush-plugin.<方法>", version="1", stream_id=...)`。

这些接口只读。结果不是身份认证、管理授权或发送许可；没有面向第三方的静音写接口或发送例外令牌。

## 会话与状态

| 方法 | 返回内容 |
| --- | --- |
| `get_public_route_binding` | 指定已知受管会话的 platform、adapter_namespace、bot_account_id、route_scope、group_id、stream_id |
| `get_public_constraints` | 指定已知受管会话的静音状态、健康状态、版本与时间信息 |

成功：`{"api_version":1,"ok":true,"data":{...},"error":null}`。
失败：`{"api_version":1,"ok":false,"data":null,"error":{"code":"...","retryable":false}}`。

只查询调用方已知的 Host 会话，不枚举群、用户或管理员名单。消费者应使用正式入站消息携带的会话、账号、群号与 scope，对照路由结果；不能只按群号拼造会话 ID。空 scope 对应 null。

`get_public_constraints` 的关键字段：

| 字段 | 含义 |
| --- | --- |
| `hard` | `NONE` 或 `HARD_SILENT`（管理员静音） |
| `soft` | `NORMAL`、`SOFT_SILENT`、`RESTRAINED` 或 `RECOVERING` |
| `health` | 运行健康状态；非 READY 时不得推定可进行额外社交动作 |
| `state_version` / `config_revision` | 当前状态及配置版本，用于发现变化 |
| `route_fingerprint` | 路由绑定指纹，不是身份凭据 |
| `as_of` / `valid_until` | Unix 秒时间戳，快照建议使用期限最多 5 秒；状态可提前变化 |
| `remaining_seconds` / `reason` | 当前期限与中文说明，仅用于展示 |
| `send_permit` | 始终为 false；读取不预约、不消费或重置发送额度 |
| `guard_scope` | `send_service_while_registered`，约束只作用于守卫实际参与的普通发送流程 |

硬静音或软静音时停止可见输出。RESTRAINED/RECOVERING 是有限连续片段与发送间隔控制，不是永久禁止，也不是可以自由发送；实际判定及计数由发送守卫完成。不要把 `remaining_seconds` 当作下次可发送时间。

## 不可用与并发

- 未就绪或切换中，约束查询返回 NOT_READY；路由查询可能返回 ROUTE_UNAVAILABLE。未知路由返回不可用，而不是“确定不受管”。
- INVALID_ROUTE 表示约束查询参数不合法。其他 Host RPC 失败、异常、超时或不完整响应也应当视为未知。
- 消费者必须设置整体查询超时与并发上限。`retryable=false` 不允许立即无限重试；后续独立事件可重新查询。
- 同一个插件的一次查询不会改变状态；跨插件的多次查询不是事务。生成前预查只能减少不必要开销，延迟或生成后仍需复查，并经过最终受控发送路径。
- 时间未过期、版本未变化、健康状态正常，均不能保证查询结束后仍然允许发送。不能持有快照作为长期许可。
- 受管范围内查询失败时停止当前动作；不要把一个会话的失败扩大为所有群不可用。只有独立、明确的配置或可信路由证据，才能确认某会话不由本插件管理。

## 发送边界

`ctx.send.text`、`ctx.send.emoji` 等正常发送进入 Host SendService 守卫。直接调用适配器公开 API（例如 `adapter.napcat.message.send_poke`）没有自动经过这一流程。只查询本接口不能弥补这条发送路径缺口。

发送返回 false 不足以区分静音拒绝与网络失败。消费者不得据此改走不受控通道；已提交但结果未知时也不得追加另一条输出。需要完整强约束时，必须验证最终平台动作经过受控路径。

Hush 命令确认使用本实例内部的一次性凭据，不对外提供。第三方复制确认文字、构造自定义消息或调用公开查询都不能获得同等例外。

守卫卸载、Host 跳过熔断处理器、插件进程不可用，以及已经提交平台的请求，不属于“普通守卫可靠执行”的保证。Hook 的 error_policy=abort 不能代替 Host 在这些情况下的策略。不要向用户宣称 Hush 是平台级禁言或能阻止独立模型生成。

## 其他接口

`get_contract` 用于读取功能描述；`get_state` 不向任意调用方开放私有状态，返回 UNTRUSTED_CONTEXT。不要尝试通过增加调用参数绕过该边界。

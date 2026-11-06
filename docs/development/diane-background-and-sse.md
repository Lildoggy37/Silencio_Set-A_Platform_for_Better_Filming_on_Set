# Diane：后台执行与 SSE 进度

已实现后台聊天的提交、持久投递、worker领取、节点进度和SSE订阅。真实本机RabbitMQ、独立Celery worker、PostgreSQL、Redis及SSE链路已用模拟模型通过联调；试用服务已启用后台聊天。尚未用真实模型验证这条新后台链路，也未进行吞吐或稳定性压测。

## 请求如何流转

```text
聊天窗口 POST /conversations/{id}/runs → 202 + queued
  → PostgreSQL：问题、本轮UUID、待投递状态同事务提交
  → Agent dispatcher 领取待投递记录
  → RabbitMQ agent队列：仅投递本轮UUID
  → Celery worker：重新检查账号、剧组和本次入组身份，领取执行权
  → 原有 LangGraph + MCP 工具循环
  → PostgreSQL：阶段、版本、结果、耗时和token用量
  → Redis：仅发布版本唤醒信号
  → FastAPI SSE：重新授权并读取PG快照 → 聊天窗口
```

请求不在HTTP连接内等待模型。数据库里的queued记录同时充当投递箱，没有增加新的通用任务系统。dispatcher只处理明确提交的Agent请求，不运行全剧组业务自动检查。

## 状态与一致性

| 状态 | 页面含义与处理 |
|---|---|
| queued | 排队中；最多等待5分钟，期间同会话不接受另一个问题 |
| running / model | 正在生成回答 |
| running / tools | 正在查询资料 |
| completed | 结果已保存，可断线重读 |
| failed | 模型失败、预算／工作时限结束或权限失效；不自动再次调用模型 |
| interrupted | 排队超时、worker中断或执行权到期；读状态时确认并释放本会话占用 |

每次进度更新递增 `progress_version`。本轮UUID仍是提交幂等键和检查点线程标识；重复POST返回同一记录。worker只允许 `queued → running` 一次，并持有独立执行凭据；重复消息不会进入模型。完成时检查状态、执行凭据、到期时间和当前成员权限，过期worker不能覆盖终态。

dispatcher每批最多10条，以10秒投递租期防止重复领取，消息持久化并等待publisher confirm。投递失败／结果不明时保留数据库记录，后续可以重投UUID；不将发送失败误报为模型执行失败。任务确认较晚，且禁用Celery整体自动重试；已领取但中断的运行不会因消息重投自动重跑。

Agent队列显式使用quorum类型及Celery队列检测，规避本机RabbitMQ 4.4禁止旧global QoS的问题。仍只有一个本机broker，没有搭建多节点集群。已有同名classic队列的环境不能直接覆盖队列类型，部署时应先安排保留消息的迁移，不能直接删除队列。

三个队列显式使用各自同名路由键，避免都继承默认documents路由而相互投递。本机已移除Agent队列旧的documents绑定，没有删除队列或清空消息。其他既有环境升级时也应核对旧绑定；未使用ETA／countdown任务，Celery关于direct exchange不支持原生延迟投递的启动提示不影响当前立即执行链路。

## SSE边界

- 只传递阶段及最终结果快照，**尚未逐token输出正文**。运行耗时和token消耗在最终结果中展示。
- 浏览器关闭面板会释放订阅，不取消worker。重新展开／刷新并进入原剧组后，按原运行ID读取快照，不重新POST问题。
- SSE带递增事件ID；重连时返回最新PG快照，不保证逐条重放断线期间的所有中间阶段。完整最终问答仍在会话历史中。
- Redis仅用于唤醒。服务至少每10秒重新检查登录会话和数据库状态，丢失通知不丢失结果；权限或登录失效时发送access_revoked并结束订阅。连接中断可重新订阅读状态，不会自动重新调用模型。
- 页面按进度版本合并历史GET和SSE结果，避免先发出的旧历史响应覆盖刚接收的消息。

## 接口

前缀 `/api/v1/crews/{crew_id}/diane`。保留上一轮同步查询和任务提案接口；任务提案生成及确认本轮不改为后台任务。

| 方法与路径 | 说明 |
|---|---|
| `GET` 前缀 | 新增 `execution_mode: synchronous / background` |
| `POST /conversations/{id}/runs` | `{question, turn_id}`，需要后台开关、登录、资料完善、成员范围、CSRF／Origin和原模型限流，返回202 |
| `GET /conversations/{id}/runs/{turn_id}` | 读取本人当前入组身份下的一轮状态和结果 |
| `GET /conversations/{id}/runs/{turn_id}/events` | Cookie认证的同源SSE；订阅也需本人、剧组、会话和消息范围校验 |

## 启动

先更新锁文件依赖并迁移 `0028_diane_background`。已有模型、数据库、Redis、RabbitMQ配置保持在后端；新增 `DIANE_BACKGROUND_ENABLED=false` 默认开关。

正式开发环境分别启动：

```powershell
python scripts/dev.py agent-dispatcher
python scripts/dev.py agent-worker
python scripts/dev.py api
```

只有配置 `DIANE_ENABLED=true`、`DIANE_BACKGROUND_ENABLED=true` 和有效broker地址，才接受后台请求。Windows开发worker使用单进程solo，本机进程数不代表生产并发；Linux入口使用prefork。未进行Windows稳定性或高并发验收。

既有隔离试用库可分别运行：

```powershell
python scripts/agent_preview.py dispatcher
python scripts/agent_preview.py worker
python scripts/browser_preview.py --diane --diane-background
```

上述后台命令只应在RabbitMQ就绪后启用。当前试用已启动worker、dispatcher及带 `--diane-background` 的API，状态接口返回background；数据库、Redis、RabbitMQ健康检查均为ok。不会静默把失败的后台任务改为同步重放。重启本机依赖后需重新登录。

## 验证与当前限制

29项Agent／MCP／配置检查通过；2项真实PostgreSQL联调覆盖排队先返回、重复投递领取、投递失败保留请求、SSE重新订阅、过期执行凭据及入组身份变化；6项聊天页面检查通过。模型和消息发布器在这些检查中均为模拟对象，SSE使用真实ASGI响应与Redis测试实现；不将此计为真实RabbitMQ联调。

原启动阻塞已定位到WSL内核：RabbitMQ写入线程等待jbd2_log_wait_commit，同时存在BAD_PAGE／SOFTLOCKUP。保存原RabbitMQ目录文件快照后，经用户明确授权重启WSL，再按原配置恢复Redis和RabbitMQ；RabbitMQ约3秒完成启动并通过AMQP握手。未重置队列或修改模型密钥。快照是在内核I/O停顿时制作的文件副本，不将它声称为一致性停机备份，原数据目录保持不动。

真实队列联调在独立测试库完成：API先返回queued，投递实际AMQP消息，独立Windows solo Celery worker复用原业务服务、LangGraph和MCP执行；Redis发布进度并由SSE读取最终快照。主动重复投递同一消息后，最终版本为6，记录2次模拟模型调用、1次业务工具调用。模拟模型报告70 tokens，仅为验证用量字段，不是实际计费或模型性能数据。路由键修正后该链路再次通过。未采用Celery eager模式冒充消息队列消费。

浏览器使用原生EventSource和截获的虚构SSE核对：关闭并重开窗口后读取同一结果，累计1次提交、2次订阅；没有第二次POST。桌面放大仍与红区上下齐平并向右铺满，390px手机窗口无横向溢出、无页面脚本错误。浏览器检查的模拟回复与真实队列联调为两项独立证据，不将其混写为真实模型端到端效果验证。

本轮未调用真实模型、未进行压测。仍未实现自动检查点恢复、运行取消、token正文流式输出、异步任务提案或多worker容量验收。后续接受控恢复／取消与创作辅助。

试用库已备份后升级到0028，原有剧组、任务、资源和预约保持不变；前后端已恢复可访问，API使用后台模式。生产构建通过；本次仅修改队列路由配置后补跑对应配置检查，未重复无关测试。

参考：[Celery任务确认与幂等](https://docs.celeryq.dev/en/stable/userguide/tasks.html)、[Celery RabbitMQ队列](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html)、[FastAPI流式响应](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)、[SSE连接行为](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)。

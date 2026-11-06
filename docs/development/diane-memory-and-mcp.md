# Diane：会话记忆、运行状态与 MCP

后续更新：已加入后台提交、Celery worker和SSE快照通道，并通过真实RabbitMQ／独立worker的模拟模型联调，试用已启用后台模式，详见[后台与SSE](diane-background-and-sse.md)。下文为记忆与MCP阶段边界。

本批实现取代“每次独立查询、刷新清空”的页面行为。模型继续使用百炼 `deepseek-v4.1-flash`，LangChain 适配模型，LangGraph 编排单 Agent。没有新增服务器或向量数据库。

## 已落地的调用链

```text
聊天窗口 → FastAPI（登录、CSRF、限流、剧组权限）
  → PostgreSQL 领取本轮消息，检查重复请求及同会话占用
  → 读取近期完整问答与用户明确保存的偏好
  → LangGraph：model → tools → model
      ├─ AsyncPostgresSaver：每个节点保存消息、阶段、调用观测和预算
      └─ LangChain MCPAdapter → Streamable HTTP → FastMCP
          → 当前用户／剧组绑定的工具 → 原有业务服务 → PostgreSQL
  → 再次检查成员身份，保存本轮结果 → 页面展示
```

MCP 使用 FastMCP 的真实 HTTP ASGI 应用和流式 ASGI transport，在同一事件循环内完成协议通信。没有监听端口、公开 MCP 地址或额外进程；不是将函数改名为 MCP。当前工具服务的用户和剧组在请求内绑定，模型只能提供业务筛选参数。日后拆为独立 HTTP 服务时，必须另外实现服务认证与逐请求身份传递，不能直接公开这个无网络监听的内部实现。

LangChain `langchain.mcp.MCPAdapter` 目前是 Beta，封装在 `diane/mcp.py` 并通过锁文件固定依赖。FastMCP 4.1.6、MCP 2.3.0、langgraph-checkpoint-postgres 3.2.0 为本批解析到的版本。

## 四类数据及边界

| 数据 | 存储与规则 |
|---|---|
| 完整问答历史 | `diane_conversations`、`diane_conversation_turns`；本人＋剧组＋本次入组身份隔离，历史每页50轮，最近20段对话可切换 |
| 近期模型上下文 | 最多6轮成功问答、累计最多12000 UTF-8字节，从最近一轮向前选完整问答；不拼接旧工具结果或旧工具调用，不额外调用模型生成摘要 |
| 长期偏好 | `diane_preferences`；用户手动保存“简洁／详细”和“全面筹备／拍摄排期／资源准备”，可跨当前剧组内不同对话使用。模型不能自动写入偏好 |
| 运行检查点 | LangGraph 管理的4张表；按本轮消息UUID保存节点状态、消息、模型／工具观测及已计入预算 |

历史回答只帮助理解指代。任务状态、资源余量、成员档期和排期等当前事实要求本轮重新调用工具查询；历史记忆不替代权限检查。未做真实性自动保证、语义记忆、向量检索、自动摘要或 GraphRAG。

## 状态、重复提交和中断

- 每轮消息由客户端生成 UUID，作为请求幂等键、结果运行标识和检查点线程标识。相同ID及相同内容再次提交返回既有状态，不再次调用模型；改变内容或跨会话复用返回409。
- 状态为 `running → completed / failed`。通过对话行锁完成领取，同一会话仅一轮运行中；不同会话仍受原有单进程总并发限制。
- 运行占用到期时间为配置工作时限＋15秒；异常取消会尝试结束占用。进程中断后，在下一次读取／提交时将到期的运行标为 `interrupted`，用户可重新提问。
- 本批保存检查点，**不自动恢复中断图、不重放模型请求、不自动重试写操作**。用户刷新页面不会启动另一轮推理，也不会自动创建任务。
- 任务拟定也已通过 MCP，生成后仍保存私有提案；人工确认创建沿用原先原子事务和幂等回执，不让模型直接创建正式任务。提案确认暂未改为 LangGraph interrupt/resume。

## HTTP 接口

前缀 `/api/v1/crews/{crew_id}/diane`，均要求登录、完善资料及当前剧组访问权；写接口还要求 Origin/CSRF。

| 方法与路径 | 说明 |
|---|---|
| `POST /conversations` | 创建空对话，201 |
| `GET /conversations` | 本人最近20段对话 |
| `GET /conversations/{id}?before={ordinal}` | 最新50轮或向前翻页，含运行／失败状态 |
| `POST /conversations/{id}/queries` | `{question, turn_id}`，返回本轮状态和可用结果；保留原模型限流 |
| `GET /preferences` | 当前用户在本剧组的明确偏好，无记录返回默认值 |
| `PUT /preferences` | `{response_style, focus}`，白名单枚举；不接收自由系统提示词 |

旧 `/queries` 作为无历史单轮接口保留。任务提案接口不变。

## 本地启用

1. 安装锁文件依赖，执行 `python scripts/dev.py migrate`，应用迁移 `0027_diane_memory`。
2. 正式开发用 `python scripts/dev.py api`；已有试用入口为 `python scripts/browser_preview.py --diane`。Windows入口已选择兼容 psycopg 的 SelectorEventLoop。
3. 启用 Diane 时创建独立、受总并发约束的 psycopg 连接池，运行 `AsyncPostgresSaver.setup()`。业务数据库继续使用 SQLAlchemy＋asyncpg。Alembic 自动生成排除检查点库自行管理的4张表。
4. API重启后重新登录。聊天首次打开恢复最近对话；可以新建对话、切换历史或保存偏好。传给提供方的范围包括问题、近期问答、已保存偏好及本轮查询资料，页面已说明。

不读取或打印密钥；测试只使用虚构数据及模拟模型。检查点与历史包含私有数据，沿用私有数据库部署，不开放直接查询接口。

## 验证与后续

本批针对性验证：9项Agent／MCP检查、3项真实PostgreSQL联调、8项Diane页面／任务提案检查；前端生产构建通过。数据库联调覆盖多轮上下文、真实检查点写入、偏好隔离、历史持久化、重复请求、跨用户访问拒绝及中断状态；原任务提案生成／确认事务链路仍通过。模型均为模拟响应，不代表真实模型效果或性能压测结论。

本机试用库已备份后升级到0027，原有2个剧组、3项任务、2项资源及1项预约保持不变。浏览器以模拟Agent接口检查刷新后重新进入剧组恢复两轮对话、保存偏好、桌面放大与红区严格对齐、390px手机窗口无横向溢出，未调用模型；本机前后端服务已恢复。

下一批才接 Celery＋RabbitMQ Agent队列和 FastAPI SSE，再衔接持久化取消、断线重连及受控恢复。Redis在该阶段承担短期事件／协调，PostgreSQL仍保存权威状态。当前仍同步等待结果；不能宣称已具备后台持续执行、SSE、自动恢复或高QPS能力。

参考：[LangChain MCP](https://docs.langchain.com/oss/python/langchain/mcp)、[LangGraph记忆](https://docs.langchain.com/oss/python/langgraph/add-memory)、[FastMCP HTTP部署](https://gofastmcp.com/docs/deployment/http)。

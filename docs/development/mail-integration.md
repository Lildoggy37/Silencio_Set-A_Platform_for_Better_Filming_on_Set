# 消息队列与注册／邀请邮件真实联调

已在独立本机环境跑通：HTTP 申请验证码 → Redis 短期加密载荷 → PostgreSQL 任务与 Outbox → RabbitMQ 发布确认 → Linux Celery Worker → 回环 SMTP 收件器 → 验证码兑换 → 注册 → 登录。

邀请邮件、账号匹配、昵称门槛、明确接受入组、重复接受及撤销未发送邀请也已通过同一真实链路，详见[邀请入组联调](invitation-integration.md)。

全程使用测试邮箱 `example.com` 和本地收件器，不向互联网邮箱发送邮件，不读取或修改根目录 `.env`，不调用模型。没有新增后台自动变更检查。

## 环境与运行

本机已有 Ubuntu 22.04 WSL；原始运行包、凭据、日志和 PostgreSQL 测试数据存于被 Git 忽略的 `runtime/local-services`，未执行系统软件包安装或注册后台系统服务。RabbitMQ／Erlang 的执行缓存、队列数据和 Cookie 使用 WSL 原生临时目录，按项目路径摘要及版本隔离；Cookie 采用 `0600` 权限，停止节点后删除：

| 部件 | 本轮版本及用途 |
|---|---|
| PostgreSQL | 17.11，既有 Windows 独立实例，回环端口 55432 |
| Redis | 7.4.12，官方 Jammy 包的项目副本，回环端口 56379；没有替换系统 Redis 6.0 |
| RabbitMQ | 4.4.0，官方 Jammy 包的项目副本，AMQP 端口 55672；实际握手返回此版本 |
| Erlang | OTP 27.3.4.19，官方 RabbitMQ 软件源包；节点分布端口 55673、独立发现端口 55674，均绑定回环地址 |
| Worker | Linux Python 3.12.16、Celery 5.6.3，prefork 池，并发 1，消费 notifications 队列 |
| SMTP | 测试期间在回环随机端口启动的接收器，只接收，不中继 |

RabbitMQ、Erlang 和 Redis 包按官方索引中的 SHA256 校验；Linux Python 由 uv 0.13.7 下载到项目目录。Worker 虚拟环境使用现有 `requirements-dev.lock`，另外安装与既有开发环境一致的 pip 26.2.2、setuptools 84.2.1，再以可编辑方式安装项目。运行脚本管理的是此次准备好的项目副本，不是通用的一键安装器。

在 `backend` 目录按需执行：

```powershell
.\.venv\Scripts\python.exe scripts/postgres_local.py start
.\.venv\Scripts\python.exe scripts/redis_local.py start
.\.venv\Scripts\python.exe scripts/rabbitmq_local.py start
.\.venv\Scripts\python.exe scripts/rabbitmq_local.py status
.\.venv\Scripts\python.exe scripts/mail_smoke.py
.\.venv\Scripts\python.exe scripts/rabbitmq_local.py stop
.\.venv\Scripts\python.exe scripts/redis_local.py stop
.\.venv\Scripts\python.exe scripts/postgres_local.py stop
```

不需要 Docker。控制脚本从 Windows 委托现有 WSL；消息测试在项目 Linux 虚拟环境中运行。`mail_smoke.py` 每次创建新的 `silencio_mail_it_<UUID>` 数据库，执行全部迁移到 `0024_impact_history`，生成本次专用认证／邮件加密配置，自动启动和关闭 Worker 与 SMTP 收件器。测试不会读取个人模型密钥。

此前从 Windows 挂载目录加载 Erlang 和恢复队列发生明显延迟。脚本现在将已校验的原始包一次解压到 WSL 原生执行缓存，队列数据也使用原生目录；路径记录在私有配置的 `linux_data_dir`，不删除或移动原目录。缓存丢失时从本地原始包重新解压，不重新下载。启动等待上限仍为 120 秒，提前退出会立即失败，日志位于 `rabbitmq/startup.log`。这些临时目录只承载测试节点，可能被系统清理，不用于正式业务持久化；PostgreSQL 测试库仍保留在项目目录。

RabbitMQ 使用独立节点 `silencio_local@localhost`、专用账号和 vhost；测试开始时仅清理此专用环境 `notifications` 队列中上次失败留下的消息。不要并发运行此测试，不要将其配置指向业务 Broker。测试库保留供排查，不删除原有数据库。

摘要写入 `runtime/local-services/last-mail-run.json`；私有设置和 Worker 日志存于 `runtime/local-services/mail-runs`。邮件正文和验证码只在测试收件器内存中解析，不打印或提交。Worker 在测试结束时退出，服务端用独立脚本按需启停。

## 本轮验证结果

- 验证码接口返回 202 和 `pending_dispatch`，任务与 Outbox 均已持久化；返回体没有验证码或内部任务引用。
- Redis 中保存的是加密载荷；数据库任务引用没有明文邮箱，RabbitMQ 任务参数只有任务 UUID，消息为持久化模式。
- 实际 Broker 接受发布后，Outbox 才变为 `dispatched`，任务进入 `queued`。
- 在验证码阶段将同一任务重复投递，再排入第二个独立任务；Linux 单并发 Worker 完成后恰收到两封验证码邮件，每个任务执行尝试数均为 1，结果均为 `accepted`，状态均为 `succeeded`。这验证本次重复消息没有触发重复发送，不等同 SMTP 严格 exactly-once。
- 从本地真实收到的邮件中取出验证码，完成 HTTP 验证、注册、登录及本人资料读取。
- 扩展场景通过邀请邮件完成受邀人注册及确认入组；错误邮箱、未补昵称、重复创建和无权邀请均被拒绝，重复接受只有一条成员关系及一次成功接受审计。撤销未发送邀请后不再投递，最终仅收到两封验证码和一封邀请。
- 单条真实邮件链路通过；相关 `test_mail_jobs.py` 的 15 项定向测试通过。Redis 升至 7.4.12 后，既有完整筹备／排期／通告业务链也再次通过，邮件测试按需跳过。

## 发现与修正

1. 邮件加密载荷使用 Redis `SET ... EXAT`，本机旧版 Redis 6.0 不支持该参数。将项目独立实例升级到 7.4.12，与部署示例的 Redis 7 主版本对齐，保留系统版本，不添加旧版兼容分支。先前 Redis 6.0 上的基础业务验证不代表邮件链路可用。
2. RabbitMQ 4.3 起默认不再允许临时非独占队列，导致 Celery 默认控制队列声明失败。正式 Worker 配置增加 `control_queue_exclusive=True` 和 `event_queue_exclusive=True`，业务任务队列保持持久化，不启用 Broker 的旧行为开关。相应 Celery 最低版本声明调整为 5.6；两份现有锁文件已锁定 5.6.3，未升级其他业务依赖。
3. 独立 Erlang 运行包需重定位启动路径；运行缓存、队列数据和 Cookie 使用 WSL 原生文件系统，避免 Windows 挂载目录的权限与恢复延迟。脚本直接管理正式节点进程，避免供应商启动脚本的代理进程与节点 PID 混淆。启动诊断留在忽略目录；发现端口使用 55674。停止时按记录的 PID、项目专属 Erlang 路径和节点环境标识核验，不终止其他节点。

## 本次未覆盖

没有验证外部邮箱服务、STARTTLS／SSL 真实服务、邮件送达外部收件箱、邀请的完整浏览器操作、Broker 重启恢复、Worker 崩溃与任务时限、SMTP 提交后的断连、并发事务竞争或吞吐指标。已有模拟测试不能替代这些真实故障验证。Diane 仍在非 Agent 基础业务与联调之后接入。

参考：[RabbitMQ 官方软件源](https://www.rabbitmq.com/docs/install-debian)、[Erlang 兼容矩阵](https://www.rabbitmq.com/docs/which-erlang)、[RabbitMQ 队列规则](https://www.rabbitmq.com/docs/queues)、[Celery 5.6 配置](https://docs.celeryq.dev/en/v5.6.3/userguide/configuration.html)、[Redis SET 参数](https://redis.io/docs/latest/commands/set/)。

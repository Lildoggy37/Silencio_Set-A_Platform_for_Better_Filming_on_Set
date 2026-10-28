# 账号模块第三步：验证邮件与Outbox

阶段记录：本页保留验证码邮件链路实现与验证说明；后续已接前端账号及独立邀请邮件任务，最新迁移`0005_invitations`和状态见[邀请链路说明](invitations.md)。真实PostgreSQL/Redis/RabbitMQ/Mailpit容器联调尚未完成。认证和邮件默认关闭，不宣称软件已可完整使用或已通过高QPS验收。

## 1. 核心路径与阅读顺序

`POST /auth/email-verifications` → Redis当前验证码＋短期加密载荷 → PostgreSQL同事务保存任务和Outbox → 202受理 → dispatcher取得投递租约 → RabbitMQ确认 → Worker条件取得执行权 → 校验载荷/当前请求 → SMTP → 持久结果 → 消费确认。

路径均位于`/api/v1`。202仅返回验证请求标识、到期时间、`resend_after_seconds`重发间隔和受理时`pending_dispatch`状态，既不是“邮件已发出”也不是“用户已收到”；不返回验证码、载荷ID或任意可查任务ID。数据库提交失败返回503；队列暂时不可用不撤销已持久受理。

建议阅读：`mail/service.py`（请求与消费编排）→ `jobs/repository.py`（事务、租约、状态）→ `mail/payloads.py`（短期加密）→ `mail/smtp.py`（SMTP分类）→ `jobs/dispatcher.py`和`jobs/tasks.py`（投递与Worker入口）。全部位于`backend/src/silencio_set/`。

## 2. 敏感载荷与失败边界

- 使用成熟Fernet认证加密，Redis只保存版本`v1`与密文，包含收件人、用途、请求、载荷ID和有效期的绑定。TTL不超过当前验证码截止时间；密钥仅来自独立`MAIL_PAYLOAD_KEY`。
- 任务/Outbox与RabbitMQ不保存原始验证码、收件地址、密文或解密密钥。队列消息仅有任务UUID；邮件模板固定，不能提交任意邮件内容或营销收件人列表。
- 缺失、过期、篡改、密钥不匹配或引用错配都停止发送，不从HMAC反推验证码。发送前再次校验当前请求；重发后旧任务跳过，已在途的旧邮件仍可能到达，但旧码不能兑换。
- Redis与PostgreSQL不是同一事务。数据库失败或提交结果不明时不贸然删除Redis记录，以免破坏可能已提交的任务；无数据库引用的加密载荷按TTL清理。用户仍受重发间隔约束。
- SMTP结果分`accepted`、`rejected`、`unknown`，另有发送前的`not_sent`。连接建立前确定失败或明确4xx拒绝可有限重试；进入提交阶段后连接中断保守记为未知，不自动重发。服务接受不等于到达收件箱；SMTP不保证严格只发送一次。

## 3. 投递、执行与恢复初值

| 机制 | 当前实现与边界 |
|---|---|
| 持久状态 | `background_jobs`及`outbox_events`；迁移`0003_mail_jobs` |
| 去重范围 | 任务`job_type + idempotency_key`、Outbox `event_key`唯一；验证码请求UUID作为稳定业务范围 |
| Outbox | 每轮最多取1条，`FOR UPDATE SKIP LOCKED`，数据库时间、随机token及30秒租约，网络调用不在事务里 |
| Broker | JSON持久消息、确认后才记已投递；客户端自动publish重试关闭，失败由Outbox退避协调 |
| 投递预算 | 最多5次，退避2/4/8/16/30秒，上限受验证码截止时间限制；失败/过期进入失败终态并停止未执行任务 |
| 无消费恢复 | 已确认投递但30秒未开始的任务可有限补投，仍计同一投递预算；不据此宣称消费已完成 |
| Worker | 条件取得任务、随机执行token及120秒租约，旧token或过期租约不能回写；重复消息不能直接触发发送 |
| SMTP预算 | 单次socket超时最多5秒，明确可重试失败最多3次尝试，重试等待10秒；Celery软/硬时限55/60秒，仅Linux Worker使用 |
| 失联执行 | 运行租约到期保守记`DELIVERY_UNKNOWN`并停止自动重放，不借新Worker盲目再发；当前无长任务心跳续租 |
| 防状态回退 | 投递确认仅能更新待执行状态，不把正在执行或已完成任务改回排队；执行结果与重试Outbox协调同事务提交 |

这是邮件任务的初始实现，不是通用分布式任务平台。Celery仅该任务开启late ack，不启用无界Worker丢失重排；Celery返回不作为业务成功依据。SMTP接受后数据库回写失败仍可能留下未知结果，不掩盖为成功。吞吐、资源隔离、告警与恢复演练仍须实际验证。

数据设计同步为D03 D0.23：补充邮件截止时间、Outbox任务外键、token/租约及失败终态，属于既有有限重试要求的技术细化，没有增加业务需求。当前只落地验证邮件，不开放匿名任务状态查询；其他业务任务及剧组外键后续补齐。

## 4. 本地运行（需用户先启动Docker）

保留已有根目录`.env`，不要覆盖或复制其中Qwen密钥。只在Git忽略的`deploy/.env`中合并开发配置（保留示例中的数据库/Redis/RabbitMQ密码），另填写：

```dotenv
AUTH_ENABLED=true
AUTH_SECRET=<私有随机认证密钥，32—256字节>
MAIL_ENABLED=true
MAIL_PAYLOAD_KEY=<独立Fernet密钥>
```

占位符不能直接运行。可在自己的终端分别用`secrets.token_urlsafe(32)`及`Fernet.generate_key()`生成，再手动填入；不要提交、截图或粘贴到聊天。API、Worker、dispatcher必须使用相同的一组密钥。更换邮件密钥会令未发送的旧密文失效，当前没有多密钥轮换窗口。

从仓库根目录执行，先依赖和迁移，再启应用：

```powershell
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile mail config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile mail up -d postgres redis rabbitmq mailpit
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile mail build api worker dispatcher
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile mail run --rm api alembic upgrade head
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile mail up -d api worker dispatcher
```

Mailpit收件箱：`http://127.0.0.1:8025`，SMTP端口1025，仅本机可访问。容器内目标是`mailpit:1025`；Compose模板不传外部SMTP账号或Qwen密钥，不配置邮件中继。Mailpit中含验证码和测试地址，不得公开、转发或提交截图。镜像为`axllent/mailpit:v1.31.5`，标签已核对官方发布记录，未宣称已拉取运行。

宿主机API调试时，根目录配置使用`MAIL_HOST=127.0.0.1`、`MAIL_PORT=1025`、`MAIL_MODE=local`；与容器Worker保持认证/加密密钥一致，连接URL仍按宿主机/容器分别配置。后台可用`.venv\Scripts\python.exe -m silencio_set.jobs.dispatcher --once`检查一轮，但Windows原生不是正式Celery Worker运行环境。

仅本地测试收件箱允许明文SMTP且禁止用户名密码；外部SMTP代码支持STARTTLS/SSL及系统证书验证，但本轮未配置或测试真实服务。发件模板、凭据及传输必须届时单独核实。停止使用`docker compose ... --profile mail down`，不要加`-v`删除数据卷。

## 5. 验证结果与待验证事项

- 后端累计198项测试通过（新增47项），`pip check`通过，迁移头`0003_mail_jobs`；保留既有Starlette/httpx弃用警告。
- 加密真实调用cryptography；覆盖篡改、错密钥、错载荷ID、缺失、TTL、重发失效和不回显。数据库/任务状态使用模拟与离线SQL验证，覆盖提交、租约/token、防回退、确认失败、未知结果不重试及消息仅含UUID。
- SMTP使用测试期间临时监听`127.0.0.1`随机端口的接收器，确实走TCP；验证明确接受、451拒绝、DATA后断连未知、关闭失败不抹去已知接受，以及SMTP错误脱敏。完整测试走申请→本地邮件→验证码兑换→注册→登录→昵称，不向外网或真实邮箱发送。
- 数据库、Redis/Lua和RabbitMQ投递仍是模拟；上述测试不是生产端到端、真实事务竞争或可靠性证明。Mailpit的Compose profile配置解析通过，但Docker引擎未运行，尚未构建或启动容器，未验证真实Broker确认、Linux任务时限和Worker崩溃恢复。
- 正式启动前仍须验证真实PostgreSQL迁移/并发、Redis脚本/TTL及失效、RabbitMQ确认/重复/断线、SMTP/TLS和未知结果恢复、监测告警与压力测试。198项单元/模拟测试不替代这些验收。
- 本轮未改真实`.env`、未生成或保存用户配置密钥、未使用Qwen、未提交推送。测试临时SMTP服务器在结束时关闭。

实现参考：[Fernet](https://cryptography.io/en/48.0.1/fernet/)、[Python SMTP](https://docs.python.org/3.12/library/smtplib.html)、[Celery确认与重投配置](https://docs.celeryq.dev/en/stable/userguide/configuration.html)、[Mailpit Docker](https://mailpit.axllent.org/docs/install/docker/)、[Mailpit发布记录](https://github.com/axllent/mailpit/releases/tag/v1.31.5)。

后续状态：前端账号表单和受理/失败状态已接入并通过模拟测试；真实依赖联调仍需可用的Docker引擎及本地密钥配置，再进入剧组业务。

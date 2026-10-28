# 后端基础框架

已实现配置、FastAPI应用工厂、请求标识、错误响应、健康检查、SQLAlchemy/Alembic、账号、验证/邀请邮件任务、剧组基础、岗位管理、邀请及成员退出/移除接口。邀请记录、邮件任务和Outbox同事务保存，接受/撤销使用剧组协调锁，详见[邀请链路说明](../docs/development/invitations.md)。筹备任务已实现创建、指派、流转、历次提交、幂等回执及离组阻断，见[任务说明](../docs/development/tasks.md)。资源登记、预约审批与资源交接阻断已接入；实物借还、耗材、文件、安排交接与模型功能尚未实现。

`accounts/`包含注册登录、CSRF、会话及昵称；`mail/`处理加密短期载荷和SMTP；`jobs/`实现后台任务、Outbox、投递与条件消费；`crews/`实现剧组、成员、岗位授权及邀请；`tasks/`处理筹备任务；`notifications/`消费任务通知及本人回执；`availability/`处理本人档期；`resources/`处理资源登记、范围授权、容量与预约。迁移头为`0009_resources`，此前档期迁移需要`btree_gist`，真实迁移待验证。认证/邮件默认关闭。任务通知消费者须显式运行；资源变动暂仅记录事件，尚无资源通知消费者。见[通知与交接](../docs/development/notifications-and-handover.md)、[档期](../docs/development/availability.md)及[资源与预约](../docs/development/resources-and-reservations.md)。真实PostgreSQL/Redis/RabbitMQ联调未完成。

建议阅读顺序：`src/silencio_set/core/config.py` → `main.py` → `api/health.py` → `infrastructure/health.py`。完整启动步骤见[本地开发](../docs/development/local-development.md)。

## 入口与边界

- `uvicorn silencio_set.main:create_app --factory`：启动时加载配置；导入包不联网。
- `GET /api/v1/health/live`：进程能响应就返回200，不代表依赖可用。
- `GET /api/v1/health/ready`：PostgreSQL执行`SELECT 1`、Redis执行`PING`、RabbitMQ完成AMQP握手；全部成功200，否则503。并发合并、结果缓存5秒，各检查限时1.5秒。只说明连通，不验证迁移版本、任务消费、磁盘空间或业务正确性。
- `/docs`：本地OpenAPI页面；账号路径可见，未配置认证时返回503，不是生产服务。
- `worker_entry:app`：Celery入口；注册`silencio.mail.send_verification`及`silencio.mail.send_invitation`，分别条件领取固定类型任务并重查当前验证码或邀请/权限。消息只传任务UUID。文档和Diane任务未接入。
- `python -m silencio_set.jobs.dispatcher`：显式启用邮件后的Outbox投递进程；`--once`只运行一轮。无需Redis/Celery Beat作为正式任务状态源。
- `python -m silencio_set.notifications.consumer`：迁移后显式运行站内通知消费进程；`--once`至多一项事件。通知、接收人和消费标记同事务提交，不调用SMTP；任务交接清单也已接入。
- 缺少依赖URL允许API启动，但就绪检查失败；错误格式的URL阻止启动。连接URL须有显式端口，数据库仅接受`postgresql+asyncpg`。Worker和迁移缺配置直接失败，不偷偷使用内存队列或SQLite。
- 源码运行时`.env`按仓库路径定位；打包安装/容器使用环境变量，不在安装目录查找`.env`。环境变量优先，未实现模块的配置暂忽略。`DIANE_ENABLED=true`会拒绝启动，防止假启用。
- 只支持本地开发/测试。邮件已实现任务/Outbox及受控重试，但尚未验证真实数据库和Broker故障恢复；不承诺SMTP严格只发一次。当前剧组查询检查有效成员和读取权限，其他业务对象授权及全系统幂等仍待相应业务落地。

## 验证

在此目录执行`.venv\Scripts\python.exe -m pytest -q`。测试使用显式设置、模拟数据库与Redis/Lua，以及仅监听回环地址的临时SMTP测试接收器；不访问真实邮箱或模型服务。

`requirements.lock`和`requirements-dev.lock`由Python 3.12下的pip-tools生成。运行锁用于容器；开发锁含fakeredis/Lua模拟器，用于本地测试，不被运行镜像安装。Windows中文路径生成锁文件需向子进程传递`PYTHONUTF8=1`；本轮使用pip 26.2.2及pip-tools 7.6.2。更换Python或平台需重新验证，不视为跨平台测试证明。镜像标签固定主版本而非摘要，后续上线前仍需锁定镜像并做漏洞审核。

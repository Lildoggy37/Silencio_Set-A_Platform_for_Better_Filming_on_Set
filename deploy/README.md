# 本地容器配置

`compose.yaml`编排PostgreSQL、Redis、RabbitMQ、API与普通Worker；可选`mail` profile增加Outbox投递进程及Mailpit本地测试收件箱。数据使用本机Docker命名卷，不是分布式存储；前端在宿主机运行。所有映射端口仅绑定`127.0.0.1`。

从仓库根目录执行：

```powershell
# 示例只含公开的本地开发密码，不读取根目录的真实.env。
docker compose --env-file deploy/.env.example -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env.example -f deploy/compose.yaml up -d --build
docker compose --env-file deploy/.env.example -f deploy/compose.yaml exec api alembic upgrade head
# 停止容器，保留命名卷；不要追加-v，否则会删除数据。
docker compose --env-file deploy/.env.example -f deploy/compose.yaml down
```

只启动依赖时，将`up -d --build`替换为`up -d postgres redis rabbitmq`，宿主机API按[开发说明](../docs/development/local-development.md)配置。

当前配置不加载根目录`.env`，不向容器传入Qwen或邮件凭据。三个公开示例密码只适用于本机，不得用于公网。替换密码时使用独立的、Git忽略的`deploy/.env`，显式修改`--env-file`；本阶段模板限定URL安全字符。根目录中的宿主机连接URL要同步修改。已有数据卷里的数据库/消息队列账户不会因环境变量变化自动重建，勿用删卷冒充安全改密。

RabbitMQ管理页：`http://127.0.0.1:15672`，用户名`silencio`，密码见所选本地配置。Worker注册验证码与邀请邮件两个固定任务；就绪检查不会证明它们已消费。共享一个Worker并非通知和文档资源已完全隔离。

认证/邮件默认关闭。在Git忽略的`deploy/.env`中手动合并启用开关、私有`AUTH_SECRET`和独立`MAIL_PAYLOAD_KEY`后，按[邮件链路说明](../docs/development/mail-flow.md)启动`mail` profile并先迁移后受理。根目录`.env`不自动传入容器，不复制Qwen密钥。Mailpit页面为`http://127.0.0.1:8025`，仅截获测试邮件，含验证码，不得公开或用于真实收件。

最新迁移为`0009_resources`，包括筹备任务、站内通知、本人档期及资源预约。档期排斥约束需要`btree_gist`，迁移角色须有相应扩展创建权限；实际安装及数据库执行尚未验证。任务站内通知采用独立的PostgreSQL事务消费进程，资源事件尚未接入该消费者；邮件Worker不投递这些事件。日后迁移并联调时显式运行：

```powershell
docker compose --env-file deploy/.env.example -f deploy/compose.yaml --profile notifications up -d --build notification-consumer
```

实际启用认证的环境使用已有的私有`deploy/.env`及相同连接配置，不覆盖该文件。此profile尚未实际运行联调，运行边界见[通知与交接说明](../docs/development/notifications-and-handover.md)。邀请邮件链接采用`INVITATION_PUBLIC_ORIGIN`，默认`http://127.0.0.1:5173`，须与认证允许来源对应且不带路径。Mailpit中的邀请链接也是敏感数据。详见[邀请链路说明](../docs/development/invitations.md)。

首次构建需下载镜像与依赖。Docker引擎须先启动；`config --quiet`只验证Compose解析，不能证明镜像可拉取、容器可启动或服务已联通。

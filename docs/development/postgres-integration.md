# PostgreSQL 与 Redis 真实业务联调

本轮从模拟仓储验证推进到真实 PostgreSQL 和 Redis。数据库和业务服务使用正式实现；显式指定 `--real-redis` 时使用正式 Redis 连接池，未指定时保留快速 fakeredis 模式。未增加应用内存运行模式，不新增后台自动变更检查。

## 本机环境

- PostgreSQL 17.11，官方 Windows x64 免安装包，来自 PostgreSQL 官网链接的 EDB 下载页。
- 只解压服务端运行所需的 `bin`、`lib`、`share` 及许可证，位于 `runtime/local-services/postgresql-17.11`。
- 独立实例只监听 `127.0.0.1:55432`，使用 SCRAM 密码认证；不注册 Windows 系统服务，不依赖 Docker。
- 随机生成的本地数据库凭据存于 `runtime/local-services/postgres-local.json`；整个 `runtime` 目录被 Git 忽略，不读取或改写根目录 `.env`。
- Windows 初始化使用指向项目 `runtime/local-services` 的英文目录联接，路径记录在私有配置的 `data_dir`。实际数据仍在项目目录；目录联接只解决本机中文路径初始化问题。
- 安装包：[PostgreSQL Windows 下载](https://www.postgresql.org/download/windows/)、[EDB binaries](https://www.enterprisedb.com/download-postgresql-binaries)。现有控制脚本只管理此次已初始化的实例，不负责下载、安装或重建数据。

## 运行方法

在 `backend` 目录使用普通开发账号执行：

```powershell
.\.venv\Scripts\python.exe scripts/postgres_local.py start
.\.venv\Scripts\python.exe scripts/postgres_local.py status
.\.venv\Scripts\python.exe scripts/postgres_smoke.py
.\.venv\Scripts\python.exe scripts/postgres_local.py stop
```

真实缓存联调使用已有 `Ubuntu-22.04` WSL 中的项目独立 Redis 7.4.12，不安装系统服务。早期基础业务链曾使用系统 Redis 6.0.16；邮件联调发现其不支持 `EXAT` 后，已改为官方 Redis 7 包的项目副本，详见 [消息与邮件联调](mail-integration.md)：

```powershell
.\.venv\Scripts\python.exe scripts/postgres_local.py start
.\.venv\Scripts\python.exe scripts/redis_local.py start
.\.venv\Scripts\python.exe scripts/redis_local.py status
.\.venv\Scripts\python.exe scripts/postgres_smoke.py --real-redis
.\.venv\Scripts\python.exe scripts/redis_local.py stop
.\.venv\Scripts\python.exe scripts/postgres_local.py stop
```

Redis 独立实例绑定回环地址 `56379`，启用随机密码、64 MB 内存上限和 `noeviction`，关闭持久化。凭据及配置位于忽略的 `runtime/local-services`；测试不执行 `FLUSHDB` 或 `FLUSHALL`。脚本启动 `linux-runtime/usr/bin/redis-server`，不更新系统 Redis 或 WSL 软件包。[Redis 官方 WSL 说明](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-on-windows/)。

`postgres_smoke.py` 每次创建一个新的 `silencio_it_<UUID>` 数据库，再运行全部 Alembic 迁移及 `integration_tests` 中的 HTTP 业务测试；不清空或删除已有数据库。库名和退出码保存在 `runtime/local-services/last-integration-run.json`，数据库保留以便排查。测试临时文件也存于该忽略目录，连接密码仅通过子进程环境传递。

这些测试需显式运行；普通 `pytest` 仍只收集 `tests`，避免每次开发都启动数据库联调。集成测试配置使用 `_env_file=None`，不读取个人模型密钥。

## 已完成的验证

PostgreSQL 17.11 上全部迁移执行到 `0024_impact_history`，`btree_gist` 可用。一次完整 HTTP 场景通过，包含：

1. 真实 Argon2 密码登录、CSRF 会话、首次补全昵称、创建剧组及调整岗位。
2. 演员本人登记档期；剧本新建、确认、设为工作稿；场戏编辑、提交及审核；人物演员绑定。
3. 场地资源登记、管理人审批授权、预约与审批。
4. 计划草稿、人员和场地安排、发布前检查、正式发布及同键重放。
5. 从正式排期生成通告，填写集合信息、审核发布、输出真实 PDF。
6. 数据库事件消费生成通知；演员知悉确认；管理端读取已确认回执。
7. 切换剧本工作稿，同步标记排期和通告待复核；显式记录变更问题，无变化检查不重复记历史。
8. 场戏来源适用性复核、问题关闭及历史读取；复核不自动清除已发布排期的风险标记。
9. 撤回正式排期，发布指针清空，关联通告失效；重新查询数据库确认昵称确已持久化。

以上业务链先在 PostgreSQL 17.11 与真实 Redis 6.0.16 上通过，并在升级到 Redis 7.4.12 后再次通过。另验证登录轮换后旧会话删除、新会话有有效 TTL；8 次并发限流请求在阈值 3 下精确放行 3 次，其余返回 429 及重试等待时间。该检查验证 Lua 原子行为，不作为 QPS 压测。

本轮发现并修复 `ImpactIssue.resolution_basis` 的空值映射：使用 `JSONB(none_as_null=True)`，使未解决问题写入 SQL `NULL`，满足数据库 `resolution_valid` 约束。数据库结构无需修改，迁移版本不变。

## 验证边界

这证明上述单场景的 HTTP、正式仓储及真实数据库／缓存可协同工作；不是高并发或上线验收。该筹备业务测试的账号及演员入组关系由种子创建，不验证注册或邀请邮件。独立的[邮件测试](mail-integration.md)已验证 RabbitMQ、Linux Celery、本地 SMTP、验证码注册登录和邀请确认入组；完整邀请浏览器操作、外部邮箱、故障恢复、多实例并发及性能指标仍待验证。测试执行耗时不作为接口性能或 Agent 时长指标。

已补充基础入口、岗位和响应式布局的真实 Edge 页面验证，见 [浏览器联调说明](browser-integration.md)。后续验证消息与邮件链路、部署目标版本并补齐其他业务页面；Diane 仍在非 Agent 基础流程完成之后接入。

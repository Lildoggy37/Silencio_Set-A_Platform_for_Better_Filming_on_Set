# 本地开发

核心筹备业务的前后端已接入，当前迁移头为 `0025_workflow_policies`，已在真实 PostgreSQL 执行。数据库、缓存、消息队列、本地邮件及核心筹备 HTTP 链路已通过联调，浏览器已覆盖登录、岗位、排期与通告发布和 PDF 下载。边界见[数据库与缓存](postgres-integration.md)、[浏览器](browser-integration.md)和[消息与邮件](mail-integration.md)。认证与邮件需要显式配置启用，Diane 首批只读查询代码已接入但默认关闭，启用配置及验证边界见[Diane说明](diane-read-only.md)。

## 1. 环境与秘密

- 当前使用 Node 24、Windows Python 3.12 和 Ubuntu 22.04 WSL。邮件 Worker 在 Linux 运行。
- 真实根目录`.env`保持Git忽略，**不要用示例覆盖已有文件**。Qwen配置仍未消费；启用本地邮件需另配认证及加密密钥，不使用Qwen密钥代替。
- 容器使用`deploy/.env.example`中的公开本地开发密码，显式指定`--env-file`，不挂载或传入根目录`.env`。这些密码绝不能用于公网。
- 示例连接字符串使用显式端口。切换端口或密码时同步宿主机配置与Compose映射；示例支持URL安全密码字符，复杂凭据需正确URL编码和调整模板。

## 2. 当前本机启动入口

在 `backend` 目录执行下列命令。各进程使用同一份根目录 `.env`，当前终端的环境变量优先。先配置实际数据库、Redis 和 RabbitMQ 连接，再执行迁移。已有配置应手动合并，保留原密钥。

| 命令 | 用途 |
|---|---|
| `.\.venv\Scripts\python.exe scripts/dev.py migrate` | 将已配置数据库迁移至最新版本 |
| `.\.venv\Scripts\python.exe scripts/dev.py api` | 启动本机 API，监听 8000 |
| `.\.venv\Scripts\python.exe scripts/dev.py notifications` | 持续生成任务、排期与通告的站内通知 |
| `.\.venv\Scripts\python.exe scripts/dev.py dispatcher` | 将已受理的邮件任务投递到 RabbitMQ |
| `.\.venv\Scripts\python.exe scripts/dev.py worker` | 启动 Linux 邮件 Worker |

API 与通知消费者分别占用一个终端。启用验证码／邀请邮件时，再分别运行 dispatcher 和 worker，并配置 SMTP、`AUTH_SECRET` 与独立的 `MAIL_PAYLOAD_KEY`。消费者处理已发生的业务事件。来源变更检查继续通过页面显式触发。

Windows 的 worker 命令委托现有 `Ubuntu-22.04` WSL 和 `runtime/local-services/worker-venv/bin/python`，这是此前准备好的本机运行环境。其他发行版用 `--distro 名称` 指定。原生 Linux 直接使用调用脚本的 Python 环境。两个平台均以单并发 prefork 运行，消费 `notifications` 队列。显式设置的配置环境变量按名称经 WSLENV 传递，避免 API 和 Worker 使用不同设置。

独立 PostgreSQL、Redis、RabbitMQ 的启停见[本机服务记录](mail-integration.md)。其中 `*_smoke.py` 创建隔离测试库，`browser_preview.py` 使用测试账号，日常业务启动使用本节命令及自己的数据库配置。

前端在 `frontend` 目录执行 `npm.cmd run dev`。访问 `http://127.0.0.1:5173`，Vite 将 `/api` 请求转发至 API。每个进程在自己的终端按 Ctrl+C 停止，之后按需停止独立依赖服务。

真实 SMTP 的配置项见[邮箱接入说明](smtp-setup.md)。

## 3. 既有 Compose 启动方式

在仓库根目录，先验证配置，再启动：

```powershell
docker compose --env-file deploy/.env.example -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env.example -f deploy/compose.yaml up -d --build
docker compose --env-file deploy/.env.example -f deploy/compose.yaml exec api alembic upgrade head
docker compose --env-file deploy/.env.example -f deploy/compose.yaml ps
```

在`frontend`目录的另一终端：

```powershell
npm.cmd ci
npm.cmd run dev
```

访问`http://127.0.0.1:5173`。前端同源请求`/api`，Vite代理到`http://127.0.0.1:8000`。`/docs`可查看当前接口。端口占用时先检查本机已有进程，不要直接杀掉不明进程。

## 4. 后端环境与连接配置

先只启动三个依赖服务：

```powershell
docker compose --env-file deploy/.env.example -f deploy/compose.yaml up -d postgres redis rabbitmq
```

在`backend`目录创建环境（已有`.venv`直接复用），安装锁定依赖：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

没有`py`命令时使用已安装Python 3.12的完整路径执行第一行。无需激活脚本或调整执行策略。

把根目录`.env.example`中的三个连接URL**手动合并**到已有`.env`，保留原有密钥；或仅在当前PowerShell会话设置以下公开开发配置：

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://silencio:dev_pg_only@127.0.0.1:5432/silencio'
$env:REDIS_URL = 'redis://:dev_redis_only@127.0.0.1:6379/0'
$env:CELERY_BROKER_URL = 'amqp://silencio:dev_mq_only@127.0.0.1:5672/silencio'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn silencio_set.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

需要热更新时追加`--reload`。所有运行配置仅供开发；不要把开发服务器暴露公网。默认关闭访问日志以免原始查询参数进入日志；应用错误日志只记录请求标识与异常类型。后续补充受控结构化日志，不记录密码、验证码和私人原文。

Windows 邮件 Worker 通过上文入口使用现有 WSL Linux 环境。Compose 方式则使用 Linux 容器。验证／邀请邮件任务、Outbox 与有限重试已实现，详见[邮件链路说明](mail-flow.md)。

## 5. 检查与预期结果

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

- `live`为200：仅表示API能响应。
- `ready`为200：三个依赖探针均成功；503时PowerShell会报告HTTP错误，可在浏览器或开发工具中查看脱敏响应。
- 没配连接URL：`not_configured`；连不上：`unavailable`；不返回真实连接地址。
- 就绪结果最多缓存5秒。未验证迁移版本、Worker消费、文件读写、模型与邮件。
- Docker引擎未运行：容器启动失败是环境问题，不要把它说成联调通过；无依赖时仍可跑单元测试与前端构建。

## 6. 验证与依赖更新

在`backend`目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

日常修改只运行受影响模块的测试，整套测试按交付需要执行。普通单元测试隔离真实 `.env`，使用数据库替身、fakeredis/Lua 和回环 SMTP。真实服务的检查独立按需执行，见前述联调记录。

在`frontend`目录：

```powershell
npm.cmd test
npm.cmd run build
```

依赖更新时人工审核并提交锁文件，不能只修改范围声明。后端更新命令（在联网的开发环境）：

```powershell
# Windows中文路径必须让构建子进程同样使用UTF-8，单独-X utf8不够。
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
# 本轮已验证工具版本；仅更新项目.venv，不更改系统Python。
.\.venv\Scripts\python.exe -m pip install pip==26.2.2 pip-tools==7.6.2
.\.venv\Scripts\python.exe -m piptools compile --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file requirements.lock pyproject.toml
.\.venv\Scripts\python.exe -m piptools compile --extra dev --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file requirements-dev.lock pyproject.toml
```

锁文件当前在Windows/Python 3.12生成；Docker/Linux构建和依赖兼容仍须实际验证。镜像使用主版本标签，尚未做摘要锁定与完整供应链审核。

## 7. 停止与数据保留

开发终端按`Ctrl+C`结束前端或API。从仓库根目录停止容器：

```powershell
docker compose --env-file deploy/.env.example -f deploy/compose.yaml down
```

命名卷会保留。**不执行`down -v`**，该选项会删除本地数据库、队列及文件卷。当前没有自动备份/恢复实现。

## 8. 当前功能与下一阶段

当前已补充无需 Docker 的 PostgreSQL 17.11、WSL Redis 7.4.12 和 RabbitMQ 4.4.0 独立测试环境，迁移到 `0024_impact_history` 及完整 HTTP 筹备发布链已通过；基础登录、剧组岗位及响应式布局的真实 Edge 检查也已通过。Linux Celery prefork、本地 SMTP、验证码注册登录和邀请确认入组已跑通。操作方式及边界见[数据库与缓存](postgres-integration.md)、[浏览器](browser-integration.md)及[消息与邮件](mail-integration.md)；下文各模块的“真实联调待补”应结合这些记录理解，邀请的完整浏览器操作、外部邮箱、真实故障和其他完整页面流转仍待验证。

资源登记、预约、借还、库存、剧本文件与版本、场戏／文字分镜、人物／资源需求、演员绑定、排期草稿与冲突检查、正式排期发布与撤回、通告审核发布与 PDF、通知回执管理、私有勘景资料及来源复核均已接入前后端。后续推进实际邮箱配置和 Diane。性能指标与公开部署在相应交付阶段评估。

人物与演员绑定及排期／通告复核联动已接入，规则、权限及边界见[选角实现说明](character-casting.md)。

正式排期撤回、关联通告失效及发布通知回执管理已接入，见[撤回与回执说明](plan-withdrawal-and-receipts.md)。

剧本来源变更清单、场戏／分镜适用性复核及排期检查衔接已接入，范围和当前限制见[来源复核说明](source-applicability.md)。

总览中的统一变更待处理清单及资料／排期／通告定位已接入，不新增数据库迁移，见[变更工作台说明](change-workbench.md)。

显式变更检查、持久问题及观察历史已接入，新增迁移`0024_impact_history`；普通刷新保持只读。规则和异步边界见[问题历史说明](impact-history.md)。

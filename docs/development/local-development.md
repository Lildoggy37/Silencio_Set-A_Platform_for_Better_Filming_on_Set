# 本地开发：基础框架

范围：前端工作台与账号、健康检查、连接池、迁移、验证码/邀请邮件、剧组组织管理、筹备任务、任务通知、本人档期，以及资源登记／预约／审批授权和离组责任检查。不是业务成品或生产部署方案，不调用Qwen。迁移头为`0009_resources`，此前档期迁移需`btree_gist`；认证/邮件默认关闭。真实依赖联调待完成，最新业务边界见[资源与预约](resources-and-reservations.md)。

## 1. 环境与秘密

- 推荐Node 24与Python 3.12；Docker Desktop需由用户启动并使用Linux容器。
- 真实根目录`.env`保持Git忽略，**不要用示例覆盖已有文件**。Qwen配置仍未消费；启用本地邮件需另配认证及加密密钥，不使用Qwen密钥代替。
- 容器使用`deploy/.env.example`中的公开本地开发密码，显式指定`--env-file`，不挂载或传入根目录`.env`。这些密码绝不能用于公网。
- 示例连接字符串使用显式端口。切换端口或密码时同步宿主机配置与Compose映射；示例支持URL安全密码字符，复杂凭据需正确URL编码和调整模板。

## 2. 推荐方式：容器运行后端，宿主机运行前端

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

## 3. 后端本机调试（替代容器API，不要重复占用8000）

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

Windows原生不作为Celery正式运行环境，Worker使用Compose中的Linux容器。当前已实现验证邮件任务、Outbox和有限重试；需开启mail profile及显式配置认证/加密密钥。其他异步业务尚未实现，详见[邮件链路说明](mail-flow.md)。

## 4. 检查与预期结果

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

- `live`为200：仅表示API能响应。
- `ready`为200：三个依赖探针均成功；503时PowerShell会报告HTTP错误，可在浏览器或开发工具中查看脱敏响应。
- 没配连接URL：`not_configured`；连不上：`unavailable`；不返回真实连接地址。
- 就绪结果最多缓存5秒。未验证迁移版本、Worker消费、文件读写、模型与邮件。
- Docker引擎未运行：容器启动失败是环境问题，不要把它说成联调通过；无依赖时仍可跑单元测试与前端构建。

## 5. 自动验证与依赖更新

在`backend`目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

测试隔离真实`.env`，使用数据库替身、fakeredis/Lua和回环地址临时SMTP接收器，不调用模型或外部邮箱。模拟测试不等同真实PostgreSQL/Redis/RabbitMQ联调、性能或完整安全测试。

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

## 6. 停止与数据保留

开发终端按`Ctrl+C`结束前端或API。从仓库根目录停止容器：

```powershell
docker compose --env-file deploy/.env.example -f deploy/compose.yaml down
```

命名卷会保留。**不执行`down -v`**，该选项会删除本地数据库、队列及文件卷。当前没有自动备份/恢复实现。

## 7. 下一批代码

资源登记、预约与审批授权已接入，见[资源与预约](resources-and-reservations.md)。下一步推进实物借还和耗材库存，再连接场地图片、创作资料和拍摄安排；每批补对应依赖与交接保护。真实依赖和浏览器联调仍待补。暂不处理Docker；Diane保留到基础业务完成后。

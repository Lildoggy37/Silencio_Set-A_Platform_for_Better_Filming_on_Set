# Silencio Set — A Platform for Better Filming on Set

Diane后台提交、Celery队列执行及SSE进度已通过真实本机队列的模拟模型联调，试用已启用后台模式，见[后台与SSE说明](docs/development/diane-background-and-sse.md)。

Diane新增持久会话、近期记忆、明确偏好、PostgreSQL运行检查点及MCP业务工具通道，见[记忆与MCP实现](docs/development/diane-memory-and-mcp.md)。

寂静片场：面向学生剧组的协作与拍摄前期准备平台。

项目名称致敬大卫·林奇《穆赫兰道》中的Club Silencio；“寂静”引申为减少管理失序带来的沟通噪声，使剧组更专注拍摄。

## 当前状态

非 Agent 核心功能已接入前后端，覆盖账号与昵称、剧组／岗位／邀请、任务、档期、资源预约与借还、耗材库存、剧本与文件导入、场戏与文字分镜、演员绑定、排期发布与撤回、每日通告及 PDF、通知回执、私有勘景资料和来源复核。变更检查通过页面显式触发，自动检查暂缓。Diane 已接入首批只读查询的模型适配、业务工具和页面侧栏，默认关闭；百炼 DeepSeek 已通过虚构任务及用户授权的一页真实任务资料联调，见[Diane实现说明](docs/development/diane-read-only.md)。

认证和邮件均需要显式配置启用。验证码与邀请已通过本地 SMTP 的注册、登录及确认入组链路，实际邮箱接入见[SMTP 配置](docs/development/smtp-setup.md)。邮件请求返回 202 表示已受理。

当前迁移头 `0028_diane_background` 已在真实 PostgreSQL 执行。PostgreSQL 17.11／Redis 7.4.12 的筹备、发布、回执与来源变更 HTTP 链路，以及 RabbitMQ 4.4.0／Linux Celery／本地 SMTP 的注册和邀请链路均已通过。Edge 已覆盖登录、岗位、响应式布局、排期冲突修正、通告审核发布与 PDF 下载。记录见[数据库与缓存](docs/development/postgres-integration.md)、[浏览器](docs/development/browser-integration.md)和[消息与邮件](docs/development/mail-integration.md)。

日常仅运行改动涉及的检查，历史分批测试结果保留在各模块文档中。实际外部邮箱、故障恢复、负载指标和公开部署待相应配置与验收。

发布、联动与验证边界见[本批说明](docs/development/plan-publication.md)。排期／通告的站内通知与本人知悉已接入，见[当前通知契约](docs/development/publication-notifications.md)。启动与验证见[本地开发说明](docs/development/local-development.md)。课程设计文档是设计基线，不能直接视为代码完成情况。

## 功能范围

- 邮箱验证码注册、剧组创建、成员岗位与权限。
- 任务分配及验收、成员档期、设备与场地资源、预约借还。
- 剧本版本、场戏与文字分镜、拍摄计划及冲突检查。
- 通告单生成、审核发布与通知确认。
- 勘景照片上传并交由导演查看。
- 后期接入Diane，辅助查询、筹备检查和草稿生成；重要变更需授权确认。

现场场记与素材归档不在当前范围内；不要求云服务器、云对象存储或分布式部署。

## 技术基线

| 部分 | 方案 |
|---|---|
| 前端 | React、TypeScript、Vite、Ant Design、TanStack Query |
| 后端 | Python、FastAPI、SQLAlchemy、Alembic |
| 数据库 | PostgreSQL |
| 缓存与临时状态 | Redis |
| 异步任务 | Celery＋RabbitMQ |
| 文件 | 本地私有文件目录，后端鉴权访问 |
| 智能辅助 | LangChain＋LangGraph＋MCP，百炼 `deepseek-v4.1-flash` 兼容API |
| 本地运行 | Windows API、WSL Linux Worker 与本机 Vite，保留 Compose 配置 |

Redis不替代数据库业务校验；RabbitMQ承担消息投递；正式任务和审批状态保存在PostgreSQL。模型具体ID以账号可用服务为准。

## 目录

```text
frontend/          前端工作区
backend/           后端及Diane工作区
deploy/            本地容器与运行配置
docs/requirements/ 需求分析及配套记录
docs/design/       软件设计与技术决策
.env.example       环境变量名称示例
```

## 开发与仓库管理

本项目计划以开源方式发布，按最新课程提交安排使用公开GitHub仓库，同时同步Gitee远端；保留两边既有提交历史，不强制覆盖远端分支。

开源许可证尚待项目负责人确定，当前未添加LICENSE文件；仓库公开不等于授予任意使用、修改或分发权限。

真实配置写入被Git忽略的`.env`，不提交密钥、真实剧本、勘景照片、数据库文件或运行日志。已有`.env`不要覆盖；根目录示例用于宿主机API，`deploy/.env.example`用于本地Compose，仅含公开开发密码，不能用于公网部署。

当前需求快照见[需求文档说明](docs/requirements/README.md)，设计基线见[设计说明](docs/design/README.md)。

日常启动入口见[本地开发](docs/development/local-development.md)。在 `backend` 中执行 `.\.venv\Scripts\python.exe scripts/dev.py --help` 查看组件命令。

## 课程提交入口

现场展示可参考[本机投屏演示](docs/development/tonight-demo.md)，包含当前入口、展示顺序及恢复命令。

[软件设计文档：课程提交精简版](docs/design/00_课程提交版软件设计文档.md)覆盖需求基线、架构、模块、数据、流程、接口、界面、非功能、异常恢复、设计权衡、追溯及测试。详细分章保留在`docs/design/`；文档状态为设计方案，未声明代码已实现或测试已通过。

人物与演员绑定及排期／通告复核联动已接入，规则、权限及边界见[选角实现说明](docs/development/character-casting.md)。

正式排期撤回、关联通告失效及发布通知回执管理已接入，见[撤回与回执说明](docs/development/plan-withdrawal-and-receipts.md)。

剧本来源变更清单、场戏／分镜适用性复核及排期检查衔接已接入，范围和当前限制见[来源复核说明](docs/development/source-applicability.md)。

总览中的统一变更待处理清单及资料／排期／通告定位已接入，不新增数据库迁移，见[变更工作台说明](docs/development/change-workbench.md)。

显式变更检查、持久问题及观察历史已接入，新增迁移`0024_impact_history`；普通刷新保持只读。规则和异步边界见[问题历史说明](docs/development/impact-history.md)。

普通任务直接完成、资源可选自动预约、本人创作稿直接确认、排期同步预检和通告预览发布已接入，既有对象保留原审批策略，见[协作流程简化记录](docs/design/12_协作流程简化建议.md)。

Diane 任务提案已接入：拟定后人工审阅，确认时复用任务服务，在同一事务中创建最多 5 项任务并保存结果；重复确认不重复创建，见[任务提案说明](docs/development/diane-task-proposals.md)。

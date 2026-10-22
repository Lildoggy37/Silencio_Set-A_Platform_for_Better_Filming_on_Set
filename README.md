# Silencio Set — A Platform for Better Filming on Set

寂静片场：面向学生剧组的协作与拍摄前期准备平台。

项目名称致敬大卫·林奇《穆赫兰道》中的Club Silencio；“寂静”引申为减少管理失序带来的沟通噪声，使剧组更专注拍摄。

## 当前状态

当前已建立本地项目骨架并初始化Git，处于软件设计阶段。业务代码、容器编排和测试尚未实现，本仓库不是可运行成品。

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
| 智能辅助 | LangChain＋LangGraph＋Qwen 3.7系列API |
| 本地运行 | Docker Compose；配置在后续实现时提供 |

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

本项目计划以开源方式发布，按最新课程提交安排使用公开GitHub仓库。保留原Gitee远程作为既有记录，不自动删除或覆盖；本次提交推送至GitHub。

开源许可证尚待项目负责人确定，当前未添加LICENSE文件；仓库公开不等于授予任意使用、修改或分发权限。

真实配置写入被Git忽略的`.env`，不提交密钥、真实剧本、勘景照片、数据库文件或运行日志。示例配置当前不可直接用于运行，初始化依赖与启动命令将在实现阶段补充。

当前需求快照见[需求文档说明](docs/requirements/README.md)，设计基线见[设计说明](docs/design/README.md)。

## 课程提交入口

[软件设计文档：课程提交精简版](docs/design/00_课程提交版软件设计文档.md)覆盖需求基线、架构、模块、数据、流程、接口、界面、非功能、异常恢复、设计权衡、追溯及测试。详细分章保留在`docs/design/`；文档状态为设计方案，未声明代码已实现或测试已通过。

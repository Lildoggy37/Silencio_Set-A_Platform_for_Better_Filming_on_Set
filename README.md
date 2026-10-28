# Silencio Set — A Platform for Better Filming on Set

寂静片场：面向学生剧组的协作与拍摄前期准备平台。

项目名称致敬大卫·林奇《穆赫兰道》中的Club Silencio；“寂静”引申为减少管理失序带来的沟通噪声，使剧组更专注拍摄。

## 当前状态

已实现前端工作台、账号、剧组及岗位／邀请管理、成员退出／移除、筹备任务、站内通知、本人档期及授权只读查看；资源页已接入设备／道具／场地登记、负责人审批授权、预约申请／审批／取消、余量查询及离组责任检查。拍摄计划、实物借还、耗材、图片和Diane仍在开发；真实依赖联调、浏览器视觉与性能验收尚未完成。

账号已加入验证码申请/兑换、注册、服务端会话登录、本人资料、昵称保存及退出API，前端已按这些接口接入，并通过模拟请求验证。认证和邮件均默认关闭；邮件显式启用且任务与Outbox提交成功后才返回202，不代表已送达。流程与验证边界见[前端账号说明](docs/development/frontend-auth.md)，本地邮件配置见[邮件链路说明](docs/development/mail-flow.md)。

剧组模块见[剧组基础说明](docs/development/crews-foundation.md)，岗位替换与末位管理员保护见[岗位管理说明](docs/development/member-roles.md)，邀请、短期敏感邮件及接受恢复见[邀请链路说明](docs/development/invitations.md)，离组事务见[成员退出与移除](docs/development/member-exit.md)，任务状态机见[筹备任务说明](docs/development/tasks.md)，通知与责任清单见[通知与交接说明](docs/development/notifications-and-handover.md)，个人区间与版本规则见[档期说明](docs/development/availability.md)，本轮资源权限与容量边界见[资源与预约](docs/development/resources-and-reservations.md)。当前迁移头`0009_resources`，仅完成离线迁移验证；真实服务端到端验收仍待完成。

当前后端508项、前端96项测试及TypeScript／Vite构建通过。验证使用模拟依赖和回环SMTP，不代表真实数据库、容器或性能验收通过。本轮结果见[资源与预约验证记录](docs/development/resources-and-reservations.md)。

启动与验证见[本地开发说明](docs/development/local-development.md)。课程设计文档是设计基线，不能直接视为代码完成情况。

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
| 本地运行 | Docker Compose（本机依赖、API与Worker），前端本机Vite |

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

真实配置写入被Git忽略的`.env`，不提交密钥、真实剧本、勘景照片、数据库文件或运行日志。已有`.env`不要覆盖；根目录示例用于宿主机API，`deploy/.env.example`用于本地Compose，仅含公开开发密码，不能用于公网部署。

当前需求快照见[需求文档说明](docs/requirements/README.md)，设计基线见[设计说明](docs/design/README.md)。

## 课程提交入口

[软件设计文档：课程提交精简版](docs/design/00_课程提交版软件设计文档.md)覆盖需求基线、架构、模块、数据、流程、接口、界面、非功能、异常恢复、设计权衡、追溯及测试。详细分章保留在`docs/design/`；文档状态为设计方案，未声明代码已实现或测试已通过。

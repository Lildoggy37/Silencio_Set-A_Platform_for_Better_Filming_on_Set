# 本机投屏演示

今晚采用本机完整运行后投屏，入口为 `http://127.0.0.1:5173/`。不需要公网服务器或公网链接。GitHub／Gitee用于展示代码，仓库页面本身不运行Python后端、数据库或Agent worker。

## 开始前

- 连接电源，演示期间保持电脑及WSL运行，提前重新登录并选择准备展示的剧组。
- 打开 `http://127.0.0.1:8000/api/v1/health/ready`：数据库、Redis、RabbitMQ应全部为ok。浏览器工作台可正常打开。
- 使用已有演示账号。当前试用邮件功能关闭，邮箱验证码注册不作为现场演示入口；本地SMTP通过不等于真实收件箱配置完成。
- 提前确认模型额度与网络。真实提问会向百炼发送问题、近期对话及当前有权查询的资料；仅使用准备公开展示的剧组内容。
- 不现场执行迁移、升级依赖、重启WSL或切换整套部署方式。WSL重启会清空当前非持久Redis会话，需要重新登录并恢复依赖。

## 约6分钟的演示顺序

1. **问题与定位**：学生剧组资源少、权责不清、筹备资料分散；平台先解决协作，再由Diane辅助。
2. **剧组与任务**：切换剧组，展示岗位和任务负责人、截止时间、状态；普通任务不强制验收审批。
3. **资源与档期**：展示资源登记、预约时段和余量，说明未登记档期为未知，不能直接视作可用。
4. **拍摄筹备**：展示剧本／文字分镜、拍摄计划及通告单，挑选已有可用记录操作；需要时展示PDF下载。
5. **Diane**：询问当前待办或下一次拍摄，展示排队／查询、回答依据、耗时与token；连续追问，并收起再打开窗口读取同一结果。
6. **受控写入**：可选拟定少量任务，先审阅提案，再确认创建。说明重要业务变化由用户确认，普通聊天发送不等于批准。

没有准备好的资料页可以讲清入口与边界，不临时编造结果。当前尚未实现自动剧本分镜生成、GraphRAG、自动检查点恢复、运行取消及逐token正文输出；这些属于后续工作。已实现的MCP工具接入、会话记忆、持久状态及后台进度可按真实代码说明。

## 出现问题时

| 情况 | 现场处理 |
|---|---|
| 模型慢／失败 | 继续展示任务、资源、排期等普通业务页；不连续重复提交同一问题 |
| 聊天连接中断 | 重新展开或刷新对话读取原请求，不把重连解释成重新执行 |
| 健康检查不是ok | 先恢复对应依赖，再登录；不要删除数据库或RabbitMQ数据目录 |
| 没网 | 普通本机业务仍可展示，模型需要网络；事前录制的演示视频应明确标注为录像 |

## 这台电脑的恢复入口

当前依赖配置和试用数据在Git忽略的 `runtime/local-services`，这些文件不会推送到仓库。**克隆仓库不会获得现有数据库、演示账号或API密钥**。

仅在对应服务未运行时，从仓库根目录恢复依赖：

```powershell
backend/.venv/Scripts/python.exe backend/scripts/postgres_local.py start
backend/.venv/Scripts/python.exe backend/scripts/redis_local.py start
backend/.venv/Scripts/python.exe backend/scripts/rabbitmq_local.py start
```

从 `backend` 目录分别启动以下组件，每条命令各占一个终端；已经运行的组件不要重复启动：

```powershell
.venv/Scripts/python.exe scripts/agent_preview.py dispatcher
.venv/Scripts/python.exe scripts/agent_preview.py worker
.venv/Scripts/python.exe scripts/browser_preview.py --notifications-only
.venv/Scripts/python.exe scripts/browser_preview.py --diane --diane-background
```

前端从仓库根目录执行 `npm.cmd --prefix frontend run dev`。这些预览命令复用原试用库，仅模型字段读取根目录私有 `.env`，不要拿 `.env.example` 覆盖它。

## 展示之后的公网部署

如果需要长期在线，再准备一台Linux主机、HTTPS入口和域名，部署前端静态产物、FastAPI、通知消费者、Agent dispatcher／worker，以及PostgreSQL、Redis和RabbitMQ；文件仍可保存在主机私有目录。另行配置真实SMTP、正式账号、备份及服务重启方式，并验收HTTPS Cookie、允许来源、SSE反向代理和权限隔离。现有Compose是本地模板，不能直接当作公网配置使用。

当前不新增服务器、不购买云资源、不公开现有试用库。正式部署属于后续独立任务。

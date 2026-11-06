# 真实浏览器联调

采用 Playwright 1.64.1 和本机已安装 Microsoft Edge 的独立无界面会话，连接真实 Vite、FastAPI、PostgreSQL 17.11 和 WSL Redis 6.0.16。没有拦截接口、伪造会话或使用示例响应，不读取个人浏览器数据、根目录 `.env` 或模型密钥。

基础入口检查使用 Redis 6.0.16，排期与通告检查使用 Redis 7.4.12。RabbitMQ 与 Linux Worker 的注册邮件闭环见[消息与邮件联调](mail-integration.md)。

## 按需运行

先按 [数据库与缓存联调说明](postgres-integration.md) 启动独立 PostgreSQL、Redis，并完成一次 `postgres_smoke.py --real-redis`。浏览器测试复用最新成功联调留下的隔离数据库，不使用正式项目数据库。

在 `backend` 终端运行：

```powershell
.\.venv\Scripts\python.exe scripts/browser_preview.py
```

在 `frontend` 的另一个终端运行：

```powershell
npm.cmd run dev
```

## 人工试用

完成上述启动后可直接打开 `http://127.0.0.1:5173/`。导演账号为 `director@example.com`，演员账号为 `actor@example.com`，两者的隔离测试密码均为 `postgres integration sample password`。登录后选择「寂静剧组 · 雨停之前」。这些账号来自隔离库，试用操作会保存到该库。

需要体验发布后的站内通知时，在 `backend` 的额外终端执行 `.\.venv\Scripts\python.exe scripts/browser_preview.py --notifications-only`。消费者使用同一隔离库配置。重启 API 会换用新的认证密钥，需要重新登录。

当前试用启用现有账号登录与筹备业务。注册／邀请的实际邮件和 Diane 需要后续配置与接入。试用服务仅绑定本机。

## 按需浏览器检查

在 `frontend` 新终端显式执行：

```powershell
npm.cmd run test:browser
```

配置使用 `channel: 'msedge'`，无需下载浏览器。[Playwright 官方说明](https://playwright.dev/docs/browsers#google-chrome--microsoft-edge)。基础入口检查为每次运行创建新的种子账号，排期检查复用隔离库的导演账号和筹备数据。两个场景均通过页面真实登录，串行执行，关闭自动重试。普通 `npm test` 不收集 `*.browser.ts`。

仅复测排期与通告时执行 `npm.cmd run test:browser -- publication.browser.ts`。每次运行会新增排期及通告版本。如果初始版本被挤出版本列表首页，重新执行 PostgreSQL 联调生成隔离库并重启预览 API。

API 和 Vite 均仅绑定 `127.0.0.1`，用 `Ctrl+C` 停止对应终端，再按数据库文档停止独立 Redis 和 PostgreSQL。不需要处理 Docker 或安装后台系统服务。

## 本次通过的页面操作

- 邮箱密码登录、首次补昵称、创建剧组。
- 成员目录内兼任导演与制片，核对后保存岗位；资源与排期工作区读取。
- 刷新页面后登录会话仍有效，再从剧组列表选择已创建剧组。
- 1440 px 桌面截图中红色拍摄信息栏占工作区宽度至少 20%，本次实际布局为 22.5%。
- 390 px 手机视口下完成全页截图，页面无横向溢出；截图等待弹窗关闭完成，避免采到关闭动画。
- 页面没有未捕获 JavaScript 异常，没有非预期业务接口失败。

初始匿名账号查询的 401、未配置 RabbitMQ 导致的 `/health/ready` 503 为已声明的环境状态，不视作上述页面链路失败。数据库与 Redis 的连通状态通过真实探针展示，RabbitMQ 显示“未配置”。

截图保存于 `runtime/browser-results`，失败时另存错误截图；该目录被 Git 忽略。未开启包含页面数据的完整 trace 或录像。

## 本次调整与验证边界

修正未选择剧组时仍显示“模块尚未接入”的旧文案，改为“请选择要进入的剧组”；只读预览显示“剧组工作区布局预览”。相关 App 单元测试及前端构建通过。

## 排期与通告

- 从已撤回的排期复制草稿。切换后只显示新草稿，旧版编辑表单会退出页面。
- 将结束时间改到场地预约以外，保存并检查冲突。页面显示预约覆盖不足，发布按钮禁用。恢复原时段后，检查显示零阻断、零未知档期，确认后发布正式排期。
- 基于新排期生成通告新版本，填写集合时间、地点和注意事项，完成送审、审核通过和发布。页面显示“当前有效”，并将参与演员列为必选接收人。
- 通过浏览器下载已发布通告，校验文件名和 PDF 文件头。

修正排期编辑器与通告面板使用相同 React `key` 导致切换版本后旧版表单残留的问题。针对性的 Edge 联调和前端构建通过。本轮使用已准备好的场戏、演员档期和预约，剧本上传、演员端通告回执和性能仍待浏览器或专项验证。

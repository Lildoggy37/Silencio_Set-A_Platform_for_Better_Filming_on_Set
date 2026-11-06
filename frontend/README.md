# 前端基础框架

Diane已接入后台提交及原生EventSource进度接收；收起再展开只重新订阅，不重复提交问题，见[后台与SSE](../docs/development/diane-background-and-sse.md)。

Diane现已接入持久会话、连续追问、历史切换和明确偏好，见[记忆与MCP实现](../docs/development/diane-memory-and-mcp.md)。下文首批阶段描述以该说明为准。

使用React、TypeScript、Vite、Ant Design及TanStack Query。遵循`docs/design/ui-reference-v1`的红黑暖白、方角、黑色顶栏及四个一级导航；无侧边栏，通告位于排期中。

当前包含注册、登录、首次昵称完善、个人资料与退出页面，并按后端接口读取本人资料。只有服务端确认登录且昵称完整后才能进入工作台。前端请求模拟测试通过；真实 PostgreSQL／Redis 下的基础登录、昵称、剧组岗位及桌面／手机布局已用 Edge 验证，其他页面和邮件链路仍需补齐。

工作台已接入剧组与成员、岗位及邀请、筹备任务、任务通知、本人档期及授权只读查看。资源页支持登记／维护、负责人审批授权、余量、申请／决定／取消，离组清单支持资源跳转。实物借还、分次归还、逾期展示和经办授权已接入资源详情；耗材库存与剧本文本版本页面已接入。项目资料中的已确认正文可进入场戏拆解，再从已确认场戏进入文字分镜，支持保存、送审、确认／退回、重开和历史快照，见[本批说明](../docs/development/scenes-and-storyboards.md)。人物与资源需求关联已接入；排期页已接入[计划草稿与检查](../docs/development/plan-drafts.md)。正式发布、未知档期确认、本人相关排期与下一次拍摄已接入，见[发布说明](../docs/development/plan-publication.md)。每日通告的生成、集合与说明编辑、接收人选择、审核发布、历史快照与失效提示已接入，见[通告说明](../docs/development/call-sheets.md)。DOCX／文字型PDF／UTF-8 TXT导入、提取提示、来源页码与原件下载已接入，见[文件导入说明](../docs/development/script-file-imports.md)。通告详情已按导出权限提供[版本绑定PDF下载](../docs/development/call-sheet-pdf.md)，失败保留网页版；场地详情已接入[勘景资料与照片](../docs/development/location-scouting.md)，支持上传、说明、预览、大图及移除；演员绑定、排期撤回与回执管理列表仍待完成。排期／通告站内通知和本人知悉已接入，支持只读来源预览与失效提示，见[当前通知说明](../docs/development/publication-notifications.md)。写操作先核对再明确提交；未知结果不自动重放，带键操作冻结原内容并提供原键重试。Diane未启用。共用同源请求和CSRF，见[任务](../docs/development/tasks.md)、[档期](../docs/development/availability.md)及[资源与预约](../docs/development/resources-and-reservations.md)。

成员退出及管理员移除入口已接入，均需明确确认；移除需填写原因，未知结果须先读取状态，不自动重试。成功退出清除当前剧组选择与缓存。任务责任清单可按权限查看和跳转处理；预约/安排交接尚未实现。顶部通知入口已接入本人通知、未读/待知悉筛选、明确已读及知悉、旧版提示和当前任务跳转，见[通知与交接说明](../docs/development/notifications-and-handover.md)。

账号页以黑色`SS / Silencio Set`斜向重复文字铺满红块；桌面业务页采用通高左侧红块与右侧标题、内容并列的结构，窄屏上下排列。真实 Edge 已核对工作台桌面与手机截图，详见[浏览器联调说明](../docs/development/browser-integration.md)。

Node建议使用24 LTS（至少22.12）。在此目录执行：

```powershell
npm.cmd ci
npm.cmd run dev
npm.cmd test
npm.cmd run build
```

开发地址`http://127.0.0.1:5173`；`/api`由Vite代理至`127.0.0.1:8000`，无需放开跨域。密钥仅在后端，禁止向`VITE_*`或前端代码放入真实凭据。生产构建需另行配置同源反向代理；Vite开发服务不是上线方案。

完整步骤见[本地开发](../docs/development/local-development.md)。

Vitest用于DOM状态/权限检查，不作延迟或吞吐验收。多步骤用例总时限10秒、每步查询等待仍5秒，最多2个jsdom Worker，避免本机同时启动大量DOM环境造成超时。生产请求的超时、重试及性能指标未因此更改。

人物与演员绑定及排期／通告复核联动已接入，规则、权限及边界见[选角实现说明](../docs/development/character-casting.md)。

正式排期撤回、关联通告失效及发布通知回执管理已接入，见[撤回与回执说明](../docs/development/plan-withdrawal-and-receipts.md)。

剧本来源变更清单、场戏／分镜适用性复核及排期检查衔接已接入，范围和当前限制见[来源复核说明](../docs/development/source-applicability.md)。

总览中的统一变更待处理清单及资料／排期／通告定位已接入，不新增数据库迁移，见[变更工作台说明](../docs/development/change-workbench.md)。

显式变更检查、持久问题及观察历史已接入，新增迁移`0024_impact_history`；普通刷新保持只读。规则和异步边界见[问题历史说明](../docs/development/impact-history.md)。
# 按需浏览器联调

`npm.cmd run test:browser` 使用本机已有 Edge，检查真实登录、昵称、剧组及岗位流程和桌面／手机布局。需先启动隔离数据库、Redis、临时 API 和 Vite；普通 `npm test` 不收集这条浏览器测试。操作方式及验证边界见 [浏览器联调说明](../docs/development/browser-integration.md)。


Diane 首批只读查询侧栏已接入；后端启用时显示入口，查询限定当前剧组，呈现依据与耗时／token 用量。默认关闭，百炼 DeepSeek 已通过虚构任务及用户授权的一页真实任务资料联调，见[Diane说明](../docs/development/diane-read-only.md)。

Diane 任务提案已接入：拟定后人工审阅，确认时复用任务服务，在同一事务中创建最多 5 项任务并保存结果；重复确认不重复创建，见[任务提案说明](../docs/development/diane-task-proposals.md)。

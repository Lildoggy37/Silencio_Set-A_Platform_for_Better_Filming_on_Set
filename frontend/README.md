# 前端基础框架

使用React、TypeScript、Vite、Ant Design及TanStack Query。遵循`docs/design/ui-reference-v1`的红黑暖白、方角、黑色顶栏及四个一级导航；无侧边栏，通告位于排期中。

当前包含注册、登录、首次昵称完善、个人资料与退出页面，并按后端接口读取本人资料。只有服务端确认登录且昵称完整后才能进入工作台。前端请求模拟测试通过，真实容器联调与浏览器视觉检查尚未完成。

工作台已接入剧组与成员、岗位及邀请、筹备任务、任务通知、本人档期及授权只读查看。资源页支持登记／维护、负责人审批授权、余量、申请／决定／取消，离组清单支持资源跳转。实物借还、耗材、图片与拍摄计划仍在开发。写操作先核对再明确提交；未知结果不自动重放，带键操作冻结原内容并提供原键重试。Diane未启用。共用同源请求和CSRF，见[任务](../docs/development/tasks.md)、[档期](../docs/development/availability.md)及[资源与预约](../docs/development/resources-and-reservations.md)。

成员退出及管理员移除入口已接入，均需明确确认；移除需填写原因，未知结果须先读取状态，不自动重试。成功退出清除当前剧组选择与缓存。任务责任清单可按权限查看和跳转处理；预约/安排交接尚未实现。顶部通知入口已接入本人通知、未读/待知悉筛选、明确已读及知悉、旧版提示和当前任务跳转，见[通知与交接说明](../docs/development/notifications-and-handover.md)。

账号页以黑色`SS / Silencio Set`斜向重复文字铺满红块；桌面业务页采用通高左侧红块与右侧标题、内容并列的结构，窄屏上下排列。当前尚未完成真实浏览器视觉检查。

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

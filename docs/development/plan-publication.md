# 正式排期发布与跨计划检查

本批接通预检凭证、未知档期确认、正式发布、当前版本切换、本人相关排期和下一次拍摄。本批迁移`0016_plan_publish`；当前迁移头见[发布通知说明](publication-notifications.md)。真实PostgreSQL迁移与并发联调尚未执行。前一批[草稿说明](plan-drafts.md)的“仅草稿／不开放发布”描述保留为阶段记录，以本页为当前契约。

## 发布事务

版本允许draft／published。shooting_plans.current_published_version_id通过同剧组、同计划复合外键引用版本；版本保留published_by、published_at、publication_snapshot和review_reasons。已发布内容不可覆盖，调整需复制草稿；旧版本仍保留，但只有当前指针所指版本计入正式冲突和依赖。

新增plan_preflight_checks、plan_publish_confirmations和plan_publication_events。发布、当前指针、未知确认、审计、通知待处理记录及幂等回执在同一剧组协调事务中保存；失败不返回成功。通知记录覆盖新旧版本参与成员并保存入组时间，防止后续投递沿用失效身份；当前已接入[统一通知消费者](publication-notifications.md)，但须显式运行；pending_dispatch不表示已发送。

| 接口（剧组前缀下） | 内容 |
|---|---|
| POST `/plan-versions/{id}/preflight` | expected_lock_version；保存本次检查记录，返回检查标记及逐项未知区间 |
| POST `/plan-versions/{id}/publish` | UUID幂等键；版本、原当前版本、检查标记、逐项确认ID |
| GET `/shooting-sessions/next` | 当前可见、尚未结束的最近正式场次；没有则返回null |

预检返回原blockers／unknown_items／checked_lock_version／limitations，并新增warnings、checked_data_revision、expected_current_published_version_id、expires_at及can_publish。每个未知项包含服务器生成的item_id及具体成员、场次、起止区间。can_publish表示当前检查无阻断且当前成员具备发布能力，不能代替发布事务重检。

检查标记绑定实际检查成员及入组身份、剧组、草稿版本、原当前版本与相关数据快照。默认10分钟有效，可用PLAN_PREFLIGHT_TTL_SECONDS配置60—3600秒。服务器仅保存标记摘要；快照包含空集合、成员状态、来源及工作稿、档期集合版本与区间、资源及预约、其他当前计划的相关场次和未归还实物。发布重读并比较，数据新增、删除或状态变化都会要求重检；不只比较原有行。

发布输入：expected_lock_version、必填且可空expected_current_published_version_id、checked_data_revision、confirmed_unknown_item_ids。确认集合必须与本次未知项完全一致，无未知时为空数组；不允许重复、遗漏或额外项，不接受force绕过冲突。发布要求plan.publish、plan.edit、availability.read_all、resource.read，重要授权在回执重放前也重新验证。

成功返回published_version_id、status=published、lock_version、previous_published_version_id、affected_call_sheet_version_ids（受本次排期变化影响的当前通告版本ID）及notification_status=pending_dispatch。已成功的同键重试先返回原回执，不因检查过期或版本已发布误判为新请求；同键不同内容或重新入组拒绝重放。

错误：版本冲突VERSION_CONFLICT，原当前版本变化CURRENT_VERSION_CHANGED，依据失效PREFLIGHT_STALE，未完整确认UNKNOWN_CONFIRMATION_REQUIRED，存在阻断PLAN_BLOCKED，非草稿INVALID_STATE；均为409。非法字段422，权限403或隐藏404。

## 检查与联动

- 排除本计划原当前版本，只比较其他计划的当前发布版本，草稿和历史版本不重复占用。
- 检查跨计划同一人员及转场重叠、资源同时分配峰值和共享预约超量；分配不重复计入预约占用。
- 耗材按当前及其他当前计划尚未结束的场次累计需求核对库存，不扣库存、不创建预约。
- 其他预约仍有未归还实物时，未来场次提示归还风险；正在进行的场次若实际数量不足则阻断。当前使用预约自身的借出不重复当成外部占用。
- 正式场次仍使用的预约不能取消；已用于正式排期的资源不能直接换单位。先发布替代安排，再处理旧依赖。
- 未来或进行中的正式场次仍有成员参与时，离组／移除被阻断，提示联系制片调整。当前尚无独立安排交接或排期撤回入口。
- 本人档期修改照常保存，相关正式版本标记availability_changed；切换剧本工作稿或耗材库存减少时标记script_changed／stock_changed。标记不改写已发布内容，也不表示已经完成影响复核；通过调整版本重新检查发布。

预约DTO的dependency_check_status与档期DTO的plan_review_status现为current_published_plans；客户端兼容旧not_implemented值，以保留历史回执的原结果。普通资源余量查询的physical_inventory_status仍为not_implemented，不能把发布时实物风险检查理解成所有资源查询已返回实时实物库存。

## 可见性与页面

完整计划仍要求plan.read_all或plan.edit。只有plan.read_related者只看到当前正式版本中与本人本次入组身份相关的场次，不开放草稿、旧版或无关场次。正式版本详情使用发布快照，资源改名和成员改昵称不改写历史呈现。下一次拍摄按同样范围过滤，首页红块显示北京时间、场地、场戏及待复核提示，不展示示例数据。

页面提供逐项未知确认，默认均不勾选；发布前再次核对，结果不明时冻结原检查、确认集合和幂等键，由用户使用原请求重试。检查失效后关闭确认窗口重检，旧确认不沿用。发布成功立即停止编辑，后台重新读取正式详情。[通告生成与审核发布](call-sheets.md)及来源变化联动已接入；发布通知消费与本人知悉已接入；排期撤回、完整影响问题清单仍待完成。

## 本批验证

相关后端269项分批验证通过：覆盖发布原子性／回执、权限、检查过期和数据变化、精确未知确认、当前版本切换、历史快照、本人范围、共享预约、实物风险、既有资源／库存／剧本／档期／退出／借还和配置回归。其中一项状态说明断言更新后仅重跑档期34项，未重复其余已通过用例。

前端排期与预览11项、资源／档期接口13项通过；TypeScript／Vite构建通过。未重跑全量测试，未调用模型、真实数据库或外部邮箱。数据库锁竞争、真实通知投递、浏览器视觉与性能仍待验证。

后续`0023_source_reviews`接入来源变更清单、历史适用性复核及正式排期预检，见[来源复核说明](source-applicability.md)。上文未接入项为原批次记录，跨来源自动复制及通用问题池仍未实现。

# 每日通告生成与审核发布

每日通告前后端已接入排期页。本批迁移为`0017_call_sheets`，当前迁移头`0018_publication_notices`见[通知说明](publication-notifications.md)；本批仅验证离线迁移，未执行真实数据库迁移。

## 业务规则

- 一个拍摄计划、一个北京时间日期对应一份通告，可保留多个版本。从当前且无需复核的正式排期生成，按场次开始日期归组；跨夜场次不复制到次日，只显示延续提醒。恰好结束于午夜的不算次日延续。
- 生成时保存项目、计划、场次时间、场地、人员与资源快照。集合时间与地点由用户补充。集合可早于拍摄当天，但不能晚于当天第一场开始。草稿可分次保存，集合信息缺失或时间晚于首场时，页面提示修改并禁用送审。联络说明与注意事项可选。
- 当天拍摄参与人员为必选接收人；具备成员查看权限的编辑者可增加其他有效成员。接收集合保存成员及本次入组时间，不沿用离组前的身份。
- 状态为`draft → pending_review → approved → published`。待审核时不能改正文或接收集合；审核退回需说明。已审核后修改正文或接收人回到草稿，旧审核记录保留；已发布版本不可覆盖，调整必须创建新版本。
- 审核保存当时正文与接收人的完整快照、摘要及依据版本。发布重新检查来源、版本、当前正式通告指针、集合信息、接收身份和审核摘要；审核后未经确认的变化不能直接发布。
- 按当前排期生成新版本会重建安排、默认保留当天必选人员；可以复制原通告的集合与补充说明，额外接收人须重新选择。
- 新排期发布时，同计划旧来源的当前通告若日期仍有拍摄则标为`review_required`，若当天已无拍摄则标为`withdrawn`。来源排期因档期、工作剧本或库存变化待复核时，相关当前有效通告同步待复核；历史正文与审核记录保持原样。
- 新版通告正式发布后，原正式通告标为`superseded`。自动撤回、待复核和替代记录均保留；当前没有手工撤回入口。

## 数据与事务

`call_sheets`保存稳定日期与当前正式版本；`call_sheet_versions`保存来源和内容；`call_sheet_draft_recipients`保存编辑集合，`call_sheet_published_recipients`保存不可覆盖的发布接收集合；`call_sheet_reviews`保存历次审核；`call_sheet_action_receipts`保存幂等结果；`call_sheet_events`保存待消费事件。

复合外键限定同剧组、同计划的来源和当前指针，计划／日期唯一，版本号唯一。日期列表与来源检索有配套索引。写入使用现有剧组协调事务；通告版本、当前指针、接收集合、事件、审计和成功回执一并提交，提交失败不返回成功。每次带UUID幂等键，先检查当前权限再重放原回执；同键不同内容或入组身份改变拒绝重放。

## 接口

以下均位于`/api/v1/crews/{crew_id}`下，写入需要会话、CSRF与UUID `Idempotency-Key`。

| 接口 | 输入或用途 |
|---|---|
| GET /call-sheets | 必填plan_id；按日期分页，每页最多20 |
| POST /call-sheets | source_plan_version_id、shooting_date |
| GET /call-sheets/{id}/versions | 通告版本列表 |
| POST /call-sheets/{id}/versions | source_plan_version_id、可选copy_from |
| GET /call-sheet-versions/{id} | 正文、来源状态、接收集合与当前通告指针 |
| PATCH /call-sheet-versions/{id} | expected_lock_version及集合时间／地点、联络说明、注意事项 |
| PUT /call-sheet-versions/{id}/recipients | expected_lock_version、member_ids |
| GET /call-sheet-versions/{id}/recipient-options | 可选有效成员分页 |
| POST /call-sheet-versions/{id}/submit | expected_lock_version |
| GET /call-sheet-versions/{id}/reviews | 历史审核快照分页 |
| POST /call-sheet-versions/{id}/reviews | expected_lock_version、decision、review_note |
| POST /call-sheet-versions/{id}/publish | expected_lock_version、必填且可空expected_current_published_version_id |

编辑、审核、发布分别要求`call_sheet.edit/review/publish`。生成还需要完整来源排期权限；额外选择接收人需要`member.read`。完整查看随通告编辑／审核／发布授权提供，只有`call_sheet.read_related`的成员仅可读本人本次入组身份收到的已发布版本，不开放草稿和审核历史。

## 页面与验证边界

页面支持日期选择、版本列表、集合与说明编辑、跨页接收人选择、送审、审核、发布及审核快照。必选接收人不可取消；发布后只读，撤回与待复核状态直接提示。已知版本冲突可读取最新资料并保留输入；网络结果不明时冻结原内容和幂等键，提供原请求手动重试，不自动重放。

相关后端150项分批通过（通告33、排期及依赖回归94、发布23），前端通告与计划18项分批通过，TypeScript／Vite构建通过。测试覆盖日期与跨夜、审核快照、来源变化、必选人员、入组身份、权限与可见性、幂等重放和提交失败；使用模拟依赖，不代表数据库锁竞争或真实端到端验收已完成。

站内通知消费与本人知悉现已接入，见[当前通知契约](publication-notifications.md)。消费者须显式运行，`pending_dispatch`不表示已经通知；[版本绑定PDF导出](call-sheet-pdf.md)已接入；其他成员回执管理列表尚未接入。真实PostgreSQL／Redis／RabbitMQ、浏览器视觉与性能验证仍待完成；未调用模型或外部邮箱。

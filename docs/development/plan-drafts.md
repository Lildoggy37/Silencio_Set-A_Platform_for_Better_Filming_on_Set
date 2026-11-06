# 拍摄计划草稿与检查

后续进展：预检凭证与正式发布已在`0016_plan_publish`接入，当前契约见[正式排期发布](plan-publication.md)。下文保留草稿阶段的接口和验证记录。

排期页已接入计划、分页草稿版本、空白／复制草稿、场次整体保存和只读检查。当前只开放草稿；未实现正式发布、预检凭证及确认、跨计划占用、通告、发布通知或依赖交接。检查结果始终can_publish=false，不构成发布许可。

## 数据与接口

迁移`0015_plan_drafts`创建shooting_plans、shooting_plan_versions、shooting_sessions、session_members、session_resource_needs和plan_action_receipts。版本保存复制来源source_version_id；状态数据库约束为draft。场次按数组次序生成sequence_no，保存时整体替换；成员保存精确入组时间，资源与预约使用同剧组复合外键。草稿场次删除只级联其成员和资源关联，不删除预约、人物或资源。

前缀`/api/v1/crews/{crew_id}`：

| 方法与路径 | 内容 |
|---|---|
| GET／POST `/shooting-plans` | 分页计划／创建，name为1—100字；创建返回201 |
| GET／POST `/shooting-plans/{id}/versions` | 分页版本／空白或copy_from本计划版本创建草稿，返回201 |
| GET `/plan-versions/{id}` | 明确版本、lock_version及完整场次 |
| PUT `/plan-versions/{id}/sessions` | expected_lock_version及完整sessions，返回新版本详情 |
| POST `/plan-versions/{id}/preflight` | expected_lock_version；只读检查，不持久化检查凭证 |
| GET `/shooting-plan-options/{kind}` | 分页scenes／members／resources／reservations；预约必须指定resource_id |

列表每页最多20。场次输入为scene_revision_id、starts_at、ends_at、transition_minutes、note、可空location_resource_id／location_reservation_id，以及members／resources数组。时间须带时区、整秒、结束晚于开始；显示使用Asia/Shanghai，支持跨日。每份最多100场，拍摄时间跨度最多31天，超出需拆计划；转场0—1440分钟，备注最多2000字。

每场最多100名成员，输入member_id及membership_joined_at，须不重复且身份仍有效；资源最多100项，输入resource_id、quantity（1—1000000）和可空reservation_id。场地独立分配1单位，不重复写入resources。预约须属于所选资源；暂时没有场地、成员或预约可以保留草稿，检查会列为阻断。

读取完整草稿需plan.read_all或plan.edit，plan.read_related不开放未发布内容。创建／复制／保存需plan.edit，保存另需member.read和resource.read；检查需plan.edit、availability.read_all及resource.read。管理员不隐式取得业务权限。可选场戏只返回已确认来源的名称／版本／编号／地点摘要，不开放完整剧本文本；成员仅返回有效成员及入组时间；资源仅可用项；预约仅返回同组已批准记录的时间和数量，不暴露申请人私人信息或授予审批能力。

写操作带UUID Idempotency-Key并检查Origin／CSRF；回执绑定用户、剧组、内容和入组时间，重放前复核权限。同键不同内容或重新入组后拒绝重放。草稿、关联、审计及回执同事务提交，沿用剧组协调锁。复制不创建预约，并保留旧关联，允许在新草稿中修复已经失效的人员／资源。plan-versions JSON体上限2 MiB，普通创建请求仍16 KiB。

## 本次检查规则

检查返回checked_lock_version、blockers、unknown_items、can_publish=false和limitations，没有checked_data_revision或可用于发布的确认项ID。

- 空计划、缺场地／人员、未确认场戏、来源不对应当前剧本工作版本，列为阻断。
- 人员离组、账号失效或入组时间变化列为阻断；本草稿内同一人的拍摄及后续转场区间不得重叠。
- 本人已登记不可用区间与场次相交时阻断；登记空隙精确返回未知区间，旧入组身份的档期不沿用。区间采用左闭右开，相邻场次无转场要求时可以衔接。
- 场戏未关联的需求、分配不足、资源停用、需求类型／单位与当前资源不一致，列为阻断。
- 场地和可复用资源须有覆盖实际拍摄时段及数量的已批准预约；同一预约同时分配的峰值不能超过批准量，资源同时使用峰值不能超过总量。资源分配不再次叠加为预约占用。
- 耗材按本草稿所有场次累计数量核对当前库存，不使用时间预约，也不自动扣减。

人员检查包含本场结束后的transition_minutes；资源预约检查实际拍摄区间。尚未检查其他正式计划、实物逾期未归还、跨剧组安排或人物与演员关系。未知档期尚无正式风险确认入口；预检结果也未与后续数据变动绑定，因此本阶段不开放发布。

## 页面与验证

页面保留原四个一级导航及左侧红块，排期中使用计划与版本入口。支持人物／资源／预约按需选择、场次增删和上移、跨日时间、备注及明确保存。未保存修改时禁用本面板的计划／版本切换和检查；关闭外层页面或切组的统一离开提醒仍待补。

结果不明时冻结原请求并提供原键重试；版本冲突保留输入，可读取最新版本后重新核对。检查结果随本地编辑清除，不展示假成功或自动发布。

本批相关后端50项、前端7项测试通过，另对版本编辑调整重跑本模块6项；TypeScript／Vite构建通过。未重复全量测试。迁移只离线生成SQL，真实数据库、浏览器视觉和性能仍待验证。后续实现发布事务／凭证／未知确认、跨计划冲突、预约变更依赖保护与安排交接后，再接通告；Diane仍在基础业务后加入。

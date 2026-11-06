# 场戏拆解与文字分镜

本批接通人工创作流程的后端、迁移及页面，不调用模型。顶部“项目资料”进入剧本库，选择已确认正文后可以建立场戏拆解；确认整份拆解后，选择其中一个场戏修订进入文字分镜。

## 来源与状态

拆解稿绑定一个已确认的剧本版本；每个场戏记录稳定场戏ID、编号、剧情地点、日夜、原文起止段落和制作备注。原文范围必须全部存在于来源正文，同一拆解内场次编号、稳定场戏ID分别唯一。用户明确选择已有场戏ID时才沿用身份，不根据相同编号自动合并。每份最多100场。

文字分镜绑定已确认拆解中的具体场戏修订，包含镜号、景别、画面、运镜、对白／声音及预计秒数。镜号在本版内唯一；顺序使用输入数组顺序及服务器生成的sort_order，不按镜号字符串排序。秒数为空表示未填写，填写时必须是正整数。每版最多100镜。

两类内容共用状态流转：

```text
draft → submit → pending_review → accept → confirmed
                             └→ reject → rejected → reopen → draft
```

仅草稿可保存；空稿不能送审；待审内容不可修改；退回必须填写原因，显式重新打开才恢复编辑；已确认版本不可覆盖或重新打开，可复制为同一来源下的新草稿。来源变化时另建新稿，当前尚无跨来源复制和自动影响复核。

审核状态、审核人、时间和内容快照与业务状态在同一事务保存。重新打开不删除旧审核记录；历史快照保留当时的场戏来源、段落范围或有序镜头内容。草稿按整份替换保存，所属场戏修订ID可能变化；只有确认后的修订才允许被分镜引用。

## HTTP契约

前缀`/api/v1/crews/{crew_id}`，以下`{kind}`仅允许`breakdowns`或`storyboards`，不匹配其他模块路径。

| 方法与路径 | 输入与结果 |
|---|---|
| GET `/{kind}` | 必填source_id，page≥1，page_size为1—20；返回分页版本摘要，不含正文items |
| POST `/{kind}` | source_id，可选copy_from（必须同一来源）；201返回新草稿详情 |
| GET `/{kind}/{id}` | 200返回明确版本与完整items |
| PUT `/breakdowns/{id}` | expected_lock_version、scenes数组；整份保存草稿 |
| PUT `/storyboards/{id}` | expected_lock_version、shots数组；整份保存草稿 |
| POST `/{kind}/{id}/submit` | expected_lock_version；提交审核 |
| POST `/{kind}/{id}/reviews` | expected_lock_version、decision=accept/reject、review_note；退回说明必填，最多500字 |
| GET `/{kind}/{id}/reviews` | 分页审核历史，每页至多20；包含当时内容快照 |
| POST `/{kind}/{id}/reopen` | expected_lock_version；显式重新打开退回稿 |

场戏输入为`scene_id? / scene_number / location_text / time_of_day / source_start / source_end / production_note?`。编号最多20字、地点100字、备注2000字；日夜取day/night/other/unknown，段落编号1—100000且结束不早于开始。

镜头输入为`shot_number / shot_size / visual_content / camera_movement? / dialogue_sound? / duration_seconds?`。镜号最多20字、景别30字、画面2000字、运镜200字、对白／声音2000字；必填文本不接受纯空白。

详情返回`id/kind/source_id/version_no/review_status/lock_version/reviewed_by/reviewed_at/items`。场戏items额外包含修订id和稳定scene_id；分镜items包含sort_order。所有写接口要求UUID `Idempotency-Key`，除创建外均返回200。

典型错误：来源未确认`409 SOURCE_NOT_CONFIRMED`、原文范围不存在`409 INVALID_SOURCE_RANGE`、非草稿修改`409 CREATIVE_IMMUTABLE`、空稿送审`409 EMPTY_CREATIVE`、状态不符`409 INVALID_STATE`、过期版本`409 VERSION_CONFLICT`；输入错误422，越权403或隐藏404，依赖失败503。

## 权限、事务与页面

均要求登录、昵称完整、当前有效成员及`script.read`。场戏另要求`scene.read`，编辑／送审／重新打开需scene.edit，审核需scene.review；分镜对应storyboard.read/edit/review。管理员不自动拥有创作权限。内部CreativeService独立于HTTP路由；未来工具应调用业务服务，不直接写表。

复用剧组协调锁，检查来源、版本、当前权限；内容、审核、审计及幂等回执同事务提交。回执绑定剧组、用户、动作、目标、正文摘要与精确入组时间；同键异内容拒绝，重新入组不重放旧回执。成功审计只记录状态和版本，不记录创作正文。Cookie、Origin／CSRF、限流及禁止缓存沿用公共规则；脚本、拆解、分镜JSON体上限2 MiB，其他普通JSON仍16 KiB。

页面提供分页版本、正文编辑、保存、送审、决定、退回重开、确认后复制及历史快照。场戏确认前不显示分镜创建入口。未保存修改时禁止切换本面板版本或新建版本；未知写入结果冻结原内容并提供原键重试，不自动重放。版本冲突可读取新版本并保留输入。跨剧本／切组／关闭资料窗口的统一离开提醒仍待补，不能据此认定全部交互验收通过。

## 当前物理结构与设计差异

迁移头为`0013_creative`，创建scene_breakdowns、scenes、scene_revisions、storyboard_versions、creative_reviews、creative_action_receipts六张表。同剧组来源使用复合外键，拆解号／分镜版本号及同版场次编号／身份使用唯一约束。

本阶段以整份拆解为并发和审核单位：scene_revisions保存来源段落列和content JSONB，未增加每条修订独立的状态、revision_no或lock_version；来源起止段落直接存列，未建立scene_source_refs。scenes只保存稳定身份与剧本关系，创建记录从所属拆解取得。

分镜按已确认scene_revision_id分组版本，镜头保存为有序JSONB数组，尚无独立storyboards或shots表；两类审核历史合并为creative_reviews，保存互斥目标及完整快照。后续如需独立镜头引用或查询，再通过明确迁移拆表，不虚构当前已有这些实体或接口。

人物／资源需求关联见下文后续实现。来源变更影响检查、逐条修订接口、跨来源新稿复用及完整交互仍是待办；本批未取消这些范围。拍摄计划与通告尚未接入，不能声明已完成正式发布前的来源检查。

## 验证边界

后端新增22项测试，覆盖来源状态、稳定场戏身份、段落范围、镜头顺序、状态流转、审核快照、权限、版本／回执、提交失败、路由及离线DDL约束。前端新增8项测试，覆盖编辑、只读、审核原因、复制版本、原键重试、分镜字段、确认场戏来源传递及旧审核快照。

测试使用模拟依赖和DOM环境，迁移仅生成离线SQL。真实PostgreSQL迁移、锁竞争、跨服务端到端、浏览器视觉、性能及用户试用尚未完成。按当前安排先完成其余非Agent前后端与联调，再开发Diane；暂不处理Docker。

## 人物与资源需求关联

迁移`0014_scene_needs`追加`scene_characters`和`scene_resource_needs`，均通过同剧组复合外键关联场戏修订，随草稿修订替换删除；资源引用使用同剧组外键并禁止级联删除资源。人物按场戏修订内名称唯一，登记的是剧中人物，不代表已分配演员。演员与人物绑定已通过独立的剧本级选角表接入，见[选角实现说明](character-casting.md)；不改写场戏审核快照。

场戏输入与详情增加两个数组，省略输入数组表示空集合，保存时随整份草稿替换：

| 字段 | 内容与约束 |
|---|---|
| characters | 每项name，1—100字；每场最多100人，名称去首尾空白后不重复 |
| resource_needs | 每项resource_type、name、quantity、unit、可空resource_id；每场最多100项 |
| resource_type | equipment／prop／location／consumable |
| name／unit | 分别1—100字、1—10字；同场需求类型＋名称不重复 |
| quantity | 1—1000000的整数；表示需求，不自动扣库存 |
| resource_id | 可空；有值时，同一场戏不可重复绑定同一资源，类型和单位必须匹配 |

绑定须有resource.read且目标属于当前剧组并可用；跨组或未知ID隐藏为404，类型／单位不匹配409 RESOURCE_NEED_MISMATCH，停用409 RESOURCE_DISABLED。保存、送审和审核通过时重新检查关联；审核退回仍可执行，以便修复失效关联。未绑定资源不阻止创作审核，界面保留“待选资源”，不当作已经落实。确认后复制新草稿会复制人物和需求到新的修订，审核快照保存当时完整集合。

页面支持增删人物及需求、填写类型／数量／单位、从分页资源目录选择或解除关联；耗材使用库存目录，只有打开选择器时才请求目录。停用和类型不匹配的选项不可选择，选定后同步单位；历史快照显示当时人物及需求。不创建预约、不扣库存、不推断演员，也不提前判断尚未指定拍摄时段的设备余量。

本批只执行相关模块测试，结果记录于根README；上节测试数保留为最初场戏／分镜批次记录。数据库迁移仍仅离线验证。

后续`0023_source_reviews`接入来源变更清单、历史适用性复核及正式排期预检，见[来源复核说明](source-applicability.md)。上文未接入项为原批次记录，跨来源自动复制及通用问题池仍未实现。

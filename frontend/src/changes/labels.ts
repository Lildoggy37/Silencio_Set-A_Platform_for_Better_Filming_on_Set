import type { ChangeCategory } from '../api/changes'
import { planReviewNames } from '../plans/reviewLabels'
export const categoryNames: Record<ChangeCategory, string> = { breakdowns: '场戏来源', storyboards: '分镜来源', plans: '正式排期', call_sheets: '每日通告' }
export const changeReasonNames: Record<string, string> = { ...planReviewNames, working_version_missing: '尚未设置剧本工作稿', source_needs_revision: '已确认需要修订资料', call_sheet_review_required: '通告待复核', source_plan_changed: '来源排期已被替代或撤回', source_plan_review_required: '来源排期待复核' }
export const transitionNames: Record<string, string> = { detected: '首次发现', updated: '依据更新', resolved: '不再待处理', reopened: '再次发现' }
export const resolutionNames: Record<string, string> = { source_matches_working: '固定来源已与工作稿一致', source_still_applicable: '已复核仍适用', plan_withdrawn: '原排期已撤回', plan_superseded: '原排期已被新版本替代', shooting_finished: '相关拍摄及转场已结束', plan_review_flags_cleared: '排期复核标记已解除', call_sheet_superseded: '原通告已被新版本替代', call_sheet_withdrawn: '原通告已撤回', call_sheet_source_current: '通告来源已恢复有效' }

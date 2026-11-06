"""Read-only checks; unknown availability never means available."""
from collections import defaultdict
from datetime import timedelta

from silencio_set.resources.domain import peak_quantity
from silencio_set.plans.schemas import CheckIssue, PreflightView


def missing_intervals(start, end, intervals):
    cursor = start
    result = []
    for left, right in sorted(intervals):
        left, right = max(left, start), min(right, end)
        if right <= cursor or left >= end:
            continue
        if left > cursor:
            result.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        result.append((cursor, end))
    return result


def check_plan(items, *, sources, members, resources, reservations, calendars, scene_needs, lock_version, now=None):
    blockers, unknown = [], []
    member_usage, resource_usage, booking_usage = defaultdict(list), defaultdict(list), defaultdict(list)
    consumables = defaultdict(int)

    def issue(code, message, item=None, **extra):
        blockers.append(CheckIssue(code=code, message=message, session_id=item['id'] if item else None, **extra))

    if not items:
        issue('EMPTY_PLAN', '计划尚无拍摄场次')
    for item in items:
        start, end = item['starts_at'], item['ends_at']
        occupied_end = end + timedelta(minutes=item['transition_minutes'])
        source = sources[item['scene_revision_id']]
        if source['status'] != 'confirmed':
            issue('SOURCE_NOT_CONFIRMED', '来源场戏未确认', item)
        if source['script_version_id'] != source['current_version_id'] and not source.get('source_review_applicable', False):
            issue('SOURCE_REVIEW_REQUIRED', '来源剧本不是当前工作版本，需重新核对来源', item)
        if not item['members']:
            issue('MEMBER_REQUIRED', '尚未安排参与人员', item)
        for link in item['members']:
            member_id = link['member_id']
            member = members[member_id]
            if member['status'] != 'active' or member['joined_at'] != link['membership_joined_at']:
                issue('MEMBER_INACTIVE', '参与人员已离组、账号失效或入组身份已变化', item, member_id=member_id)
                continue
            member_usage[member_id].append((start, occupied_end, item))
            intervals = calendars.get(member_id, [])
            for entry in intervals:
                left, right = max(start, entry.starts_at), min(occupied_end, entry.ends_at)
                if left < right and entry.availability == 'unavailable':
                    issue('MEMBER_UNAVAILABLE', '人员已登记不可用档期', item, member_id=member_id, starts_at=left, ends_at=right)
            for left, right in missing_intervals(start, occupied_end, [(row.starts_at, row.ends_at) for row in intervals]):
                unknown.append(CheckIssue(code='AVAILABILITY_UNKNOWN', message='此时段未登记档期', session_id=item['id'], member_id=member_id, starts_at=left, ends_at=right))

        allocations = list(item['resources'])
        if item['location_resource_id'] is None:
            issue('LOCATION_REQUIRED', '尚未选择拍摄场地', item)
        else:
            allocations.append({'resource_id': item['location_resource_id'], 'quantity': 1, 'reservation_id': item['location_reservation_id']})
        assigned = {link['resource_id']: link['quantity'] for link in allocations}
        for need in scene_needs.get(item['scene_revision_id'], []):
            if need.resource_id is None:
                issue('SCENE_RESOURCE_UNLINKED', f'场戏需求“{need.name}”尚未关联资源', item)
            elif assigned.get(need.resource_id, 0) < need.quantity:
                issue('SCENE_RESOURCE_UNALLOCATED', f'场戏需求“{need.name}”的分配数量不足', item, resource_id=need.resource_id)
            elif resources[need.resource_id].resource_type != need.resource_type or resources[need.resource_id].unit != need.unit:
                issue('RESOURCE_NEED_MISMATCH', f'场戏需求“{need.name}”与当前资源的类型或单位不一致', item, resource_id=need.resource_id)
        for link in allocations:
            resource_id, quantity = link['resource_id'], link['quantity']
            resource = resources[resource_id]
            if resource.status != 'available':
                issue('RESOURCE_DISABLED', '资源已停用', item, resource_id=resource_id)
            if resource.resource_type == 'consumable':
                if now is None or end > now:
                    consumables[resource_id] += quantity
                if link['reservation_id'] is not None:
                    issue('CONSUMABLE_RESERVATION', '耗材不使用时间预约', item, resource_id=resource_id)
                continue
            resource_usage[resource_id].append((start, end, quantity))
            booking = reservations.get(link['reservation_id'])
            if booking is None or booking.resource_id != resource_id or booking.status != 'confirmed' or booking.starts_at > start or booking.ends_at < end or booking.quantity < quantity:
                issue('RESERVATION_NOT_COVERED', '缺少覆盖本场时段及数量的已批准预约', item, resource_id=resource_id)
            else:
                booking_usage[booking.id].append((start, end, quantity))

    for member_id, usage in member_usage.items():
        usage.sort(key=lambda value: value[0])
        for index, (start, end, item) in enumerate(usage):
            for other_start, other_end, other in usage[index + 1:]:
                if other_start >= end:
                    break
                issue('MEMBER_SESSION_OVERLAP', '人员场次重叠或未预留足够转场时间', other, member_id=member_id, starts_at=other_start, ends_at=min(end, other_end))
    if items:
        start = min(item['starts_at'] for item in items)
        end = max(item['ends_at'] for item in items)
        for resource_id, usage in resource_usage.items():
            if peak_quantity(usage, start, end) > resources[resource_id].total_quantity:
                issue('RESOURCE_CAPACITY_CONFLICT', '同时使用的资源数量超过登记总量', resource_id=resource_id)
        for booking_id, usage in booking_usage.items():
            booking = reservations[booking_id]
            if peak_quantity(usage, start, end) > booking.quantity:
                issue('RESERVATION_ALLOCATION_CONFLICT', '同一预约的同时分配量超过批准数量', resource_id=booking.resource_id)
    for resource_id, quantity in consumables.items():
        if quantity > resources[resource_id].stock_quantity:
            issue('CONSUMABLE_SHORTAGE', '本计划耗材需求总量超过当前库存', resource_id=resource_id)
    return PreflightView(checked_lock_version=lock_version, blockers=blockers, unknown_items=unknown, limitations=[
        '检查覆盖本剧组已录入安排；其他剧组和系统外安排需人工核对。',
        '系统按已登记人物检查演员安排；未登记的人物需人工核对。发布不会自动预约或扣库存。',
    ])


def allocations(item):
    values = list(item['resources'])
    if item['location_resource_id']:
        values.append({'resource_id': item['location_resource_id'], 'quantity': 1, 'reservation_id': item['location_reservation_id']})
    return values


def extend_cross_plan(report, items, others, resources, reservations, checkouts, now):
    def add(code, message, item=None, resource_id=None, member_id=None, warning=False):
        issue = CheckIssue(code=code, message=message, session_id=item['id'] if item else None, resource_id=resource_id, member_id=member_id)
        (report.warnings if warning else report.blockers).append(issue)

    for item in items:
        people = {(link['member_id'], link['membership_joined_at']) for link in item['members']}
        for other in others:
            if item['starts_at'] < other['ends_at'] + timedelta(minutes=other['transition_minutes']) and other['starts_at'] < item['ends_at'] + timedelta(minutes=item['transition_minutes']):
                shared = people & {(link['member_id'], link['membership_joined_at']) for link in other['members']}
                for member_id, _ in sorted(shared, key=lambda value: str(value[0])):
                    add('OTHER_PLAN_MEMBER_CONFLICT', '与其他当前已发布计划的人员安排或转场时间冲突', item, member_id=member_id)
        for link in allocations(item):
            resource = resources[link['resource_id']]
            if resource.resource_type == 'consumable' or item['ends_at'] <= now:
                continue
            held = sum(row.quantity - row.returned_quantity for row in checkouts
                       if row.resource_id == link['resource_id'] and row.reservation_id != link['reservation_id'])
            if not held:
                continue
            if item['starts_at'] <= now and held + link['quantity'] > resource.total_quantity:
                add('PHYSICAL_CAPACITY_CONFLICT', '当前其他预约仍有未归还实物，本场实际余量不足', item, resource_id=link['resource_id'])
            else:
                add('PHYSICAL_CUSTODY_RISK', '其他预约仍有未归还实物，开拍前须核对归还情况', item, resource_id=link['resource_id'], warning=True)

    if not items:
        return
    start, end = min(row['starts_at'] for row in items), max(row['ends_at'] for row in items)
    own_usage, other_usage = defaultdict(list), defaultdict(list)
    own_bookings, other_bookings = defaultdict(list), defaultdict(list)
    own_stock, other_stock = defaultdict(int), defaultdict(int)
    for rows, usage, bookings, stock in ((items, own_usage, own_bookings, own_stock), (others, other_usage, other_bookings, other_stock)):
        for item in rows:
            for link in allocations(item):
                resource_id = link['resource_id']
                if resource_id not in resources:
                    continue
                if resources[resource_id].resource_type == 'consumable':
                    if item['ends_at'] > now:
                        stock[resource_id] += link['quantity']
                    continue
                value = (item['starts_at'], item['ends_at'], link['quantity'])
                usage[resource_id].append(value)
                if link['reservation_id'] in reservations:
                    bookings[link['reservation_id']].append(value)
    for resource_id in sorted(own_usage.keys() & other_usage.keys(), key=str):
        if peak_quantity(own_usage[resource_id] + other_usage[resource_id], start, end) > resources[resource_id].total_quantity:
            add('OTHER_PLAN_RESOURCE_CONFLICT', '与其他当前已发布计划的资源分配合计超量', resource_id=resource_id)
    for booking_id in sorted(own_bookings.keys() & other_bookings.keys(), key=str):
        if peak_quantity(own_bookings[booking_id] + other_bookings[booking_id], start, end) > reservations[booking_id].quantity:
            add('OTHER_PLAN_RESERVATION_CONFLICT', '同一预约被多个当前计划同时超量分配', resource_id=reservations[booking_id].resource_id)
    for resource_id in sorted(own_stock.keys() & other_stock.keys(), key=str):
        if own_stock[resource_id] + other_stock[resource_id] > resources[resource_id].stock_quantity:
            add('OTHER_PLAN_CONSUMABLE_SHORTAGE', '本计划与其他当前计划的未结束耗材需求合计超过库存', resource_id=resource_id)

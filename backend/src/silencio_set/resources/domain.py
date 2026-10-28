"""Pure reservation rules. Time intervals are half open: [start, end)."""
from collections import defaultdict

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.organization import MAX_VERSION


def peak_quantity(intervals, start, end):
    changes = defaultdict(int)
    for left, right, quantity in intervals:
        left, right = max(left, start), min(right, end)
        if left < right:
            changes[left] += quantity
            changes[right] -= quantity
    current = peak = 0
    # Aggregate simultaneous releases/acquisitions before checking occupancy.
    for instant in sorted(changes):
        current += changes[instant]
        peak = max(peak, current)
    return peak


def check_version(row, expected):
    if row.lock_version != expected:
        raise AuthError(409, "VERSION_CONFLICT", "数据已变化，请重新读取并确认")
    if row.lock_version >= MAX_VERSION:
        raise AuthError(409, "VERSION_EXHAUSTED", "版本达到上限")


def require_capacity(intervals, start, end, quantity, total):
    if peak_quantity(intervals, start, end) + quantity > total:
        raise AuthError(409, "RESOURCE_CAPACITY_CONFLICT", "该时段资源余量不足，请重新选择数量或时间")

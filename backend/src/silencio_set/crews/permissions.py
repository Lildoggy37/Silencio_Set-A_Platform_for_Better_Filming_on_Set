"""Fixed catalog from D10. Object/state checks remain mandatory in each service."""
ROLE_NAMES = {
    "admin": "管理员", "director": "导演", "producer": "制片", "writer": "编剧",
    "cinematographer": "摄影", "art": "美术", "staff": "工作人员", "actor": "演员",
}
BASE_PERMISSIONS = frozenset({
    "crew.read", "member.read", "task.read", "task.work", "task.review", "availability.self",
    "resource.read", "reservation.apply", "plan.read_related", "call_sheet.read_related",
    "notification.read_self", "notification.acknowledge_self", "diane.use",
})
ORGANIZATION_PERMISSIONS = frozenset({"member.invite", "member.remove", "member.authorize"})
CREATIVE_PERMISSIONS = frozenset({
    "script.read", "script.edit", "script.confirm", "scene.read", "scene.edit", "storyboard.read", "storyboard.edit",
})
ROLE_PERMISSIONS = {
    "admin": ORGANIZATION_PERMISSIONS,
    "director": CREATIVE_PERMISSIONS | {"task.manage", "scene.review", "storyboard.review", "availability.read_all", "plan.read_all"},
    "producer": frozenset({"task.manage", "availability.read_all", "resource.manage", "plan.read_all", "plan.edit", "plan.publish",
                           "call_sheet.edit", "call_sheet.review", "call_sheet.publish", "call_sheet.export"}),
    "writer": CREATIVE_PERMISSIONS,
    "cinematographer": frozenset(), "art": frozenset(), "staff": frozenset(), "actor": frozenset(),
}
PERMISSIONS = BASE_PERMISSIONS | frozenset().union(*ROLE_PERMISSIONS.values()) | {
    "reservation.review", "resource.checkout", "resource.return", "stock.manage", "notification.receipts",
}


def effective_permissions(roles, overrides) -> frozenset[str]:
    """Fail closed on unknown codes; deny wins regardless of row order.

    Organization authority comes only from admin, never from overrides.
    This is capability calculation, not permission to skip object ownership.
    """
    granted = set(BASE_PERMISSIONS)
    for role in roles:
        granted.update(ROLE_PERMISSIONS.get(role, ()))
    denied = set()
    for code, effect in overrides:
        if code not in PERMISSIONS or code in ORGANIZATION_PERMISSIONS:
            continue
        if effect == "allow":
            granted.add(code)
        elif effect == "deny":
            denied.add(code)
    return frozenset(granted - denied)

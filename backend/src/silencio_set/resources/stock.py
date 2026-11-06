"""Consumables share resource identity; balances change only with a ledger entry."""
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, StrictStr, field_validator, model_validator
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from silencio_set.accounts.errors import AuthError
from silencio_set.crews.models import AuditLog
from silencio_set.crews.schemas import clean_text
from silencio_set.infrastructure.database import Base
from silencio_set.resources import repository as resources
from silencio_set.resources.custody_schemas import CustodyAuthorization
from silencio_set.resources.domain import check_version
from silencio_set.resources.models import Resource, ResourceReceipt
from silencio_set.resources.schemas import ResourceCreate
from silencio_set.tasks.repository import request_digest
from silencio_set.tasks.schemas import VersionInput


class ConsumableMovement(Base):
    __tablename__ = "consumable_movements"
    __table_args__ = (
        ForeignKeyConstraint(["crew_id", "resource_id"], ["resources.crew_id", "resources.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["crew_id", "operated_by"], ["crew_members.crew_id", "crew_members.id"], ondelete="RESTRICT"),
        CheckConstraint("quantity_delta <> 0 AND balance_after BETWEEN 0 AND 2147483647", name="quantity_valid"),
        CheckConstraint("(movement_type IN ('initial','restock','return') AND quantity_delta > 0) OR (movement_type = 'issue' AND quantity_delta < 0) OR movement_type = 'adjustment'", name="type_direction_valid"),
        Index("ix_consumable_movements_resource", "crew_id", "resource_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    crew_id: Mapped[UUID]
    resource_id: Mapped[UUID]
    quantity_delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    movement_type: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(500))
    operated_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockCreate(ResourceCreate):
    resource_type: Literal["consumable"] = "consumable"
    total_quantity: None = None
    initial_quantity: int = Field(strict=True, ge=0, le=1_000_000)


class StockChange(VersionInput):
    movement_type: Literal["restock", "issue", "return", "adjustment"]
    quantity_delta: int = Field(strict=True, ge=-1_000_000, le=1_000_000)
    reason: StrictStr

    @field_validator("reason")
    @classmethod
    def reason_valid(cls, value): return clean_text(value, 500)

    @model_validator(mode="after")
    def direction(self):
        if self.quantity_delta == 0 or self.movement_type in {"restock", "return"} and self.quantity_delta < 0 or self.movement_type == "issue" and self.quantity_delta > 0:
            raise ValueError("Movement direction does not match quantity")
        return self


class StockAuthorization(CustodyAuthorization):
    permission_codes: list[Literal["stock.manage"]] = Field(max_length=1)
    transfer_management: bool = Field(default=False, strict=True)


class StockView(BaseModel):
    id: UUID
    crew_id: UUID
    name: str
    unit: str
    stock_quantity: int
    manager_member_id: UUID
    status: str
    lock_version: int
    allowed_actions: list[Literal["change", "authorize"]] = Field(default_factory=list)


class MovementView(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    resource_id: UUID
    quantity_delta: int
    balance_after: int
    movement_type: str
    reason: str
    operated_by: UUID
    created_at: datetime


class StockResult(BaseModel):
    resource: StockView
    movement: MovementView | None


class StockPage(BaseModel):
    items: list[StockView]
    total: int
    page: int
    page_size: int


class MovementPage(BaseModel):
    items: list[MovementView]
    total: int
    page: int
    page_size: int


def stock_view(row, actor=None, scopes=()):
    actions = []
    if actor and "member.authorize" in actor.permissions: actions.append("authorize")
    if actor and "stock.manage" in actor.permissions and "stock.manage" in scopes: actions.append("change")
    return StockView(**{field: getattr(row, field) for field in StockView.model_fields if field != "allowed_actions"}, allowed_actions=actions)


class StockRepository:
    def __init__(self, sessions): self.sessions = sessions

    async def get(self, user_id, crew_id, resource_id):
        async with self.sessions() as session, session.begin():
            actor = await resources.resource_actor(session, user_id, crew_id, writing=True)
            resources.require(actor, "resource.read")
            row = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == resource_id, Resource.resource_type == "consumable"))
            if row is None: raise resources.not_found()
            return stock_view(row, actor, await resources.scope_codes(session, row, actor))

    async def list(self, user_id, crew_id, page, page_size):
        async with self.sessions() as session:
            actor = await resources.resource_actor(session, user_id, crew_id)
            resources.require(actor, "resource.read")
            query = select(Resource).where(Resource.crew_id == crew_id, Resource.resource_type == "consumable")
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(Resource.created_at, Resource.id).offset((page-1)*page_size).limit(page_size))).all()
            return StockPage(items=[stock_view(row, actor, await resources.scope_codes(session, row, actor)) for row in rows], total=total, page=page, page_size=page_size)

    async def movements(self, user_id, crew_id, resource_id, page, page_size):
        async with self.sessions() as session:
            actor = await resources.resource_actor(session, user_id, crew_id)
            resources.require(actor, "stock.manage")
            row = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == resource_id, Resource.resource_type == "consumable"))
            if row is None: raise resources.not_found()
            if "stock.manage" not in await resources.scope_codes(session, row, actor): raise AuthError(403, "FORBIDDEN", "缺少本耗材库存范围")
            query = select(ConsumableMovement).where(ConsumableMovement.crew_id == crew_id, ConsumableMovement.resource_id == resource_id)
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await session.scalars(query.order_by(ConsumableMovement.created_at.desc(), ConsumableMovement.id).offset((page-1)*page_size).limit(page_size))).all()
            return MovementPage(items=[MovementView.model_validate(row) for row in rows], total=total, page=page, page_size=page_size)

    async def mutate(self, user_id, crew_id, action, body, request_id, *, idempotency_key, resource_id=None):
        async with self.sessions() as session, session.begin():
            actor = await resources.resource_actor(session, user_id, crew_id, writing=True)
            resources.require(actor, "resource.read")
            if action == "stock_create":
                resources.require(actor, "resource.manage")
                if "producer" not in actor.roles: raise AuthError(403, "FORBIDDEN", "由制片登记耗材")
            else: resources.require(actor, "stock.manage")
            digest = request_digest(action, resource_id, body)
            key = sha256(idempotency_key.encode()).hexdigest()
            receipt = await session.scalar(select(ResourceReceipt).where(ResourceReceipt.crew_id == crew_id, ResourceReceipt.user_id == user_id, ResourceReceipt.key_digest == key))
            if receipt:
                if receipt.membership_joined_at != actor.joined_at: raise AuthError(409, "MEMBERSHIP_CHANGED", "入组身份已变化")
                if receipt.request_digest != digest: raise AuthError(409, "IDEMPOTENCY_KEY_REUSED", "同键不能用于不同内容")
            row = await session.scalar(select(Resource).where(Resource.crew_id == crew_id, Resource.id == resource_id,
                Resource.resource_type == "consumable").with_for_update()) if resource_id else None
            if action != "stock_create":
                if row is None: raise resources.not_found()
                if "stock.manage" not in await resources.scope_codes(session, row, actor): raise AuthError(403, "FORBIDDEN", "缺少本耗材库存范围")
            if receipt: return StockResult.model_validate(receipt.response_snapshot)
            now = await session.scalar(select(func.clock_timestamp()))
            if action == "stock_create":
                await resources.active_member(session, crew_id, body.manager_member_id)
                row = Resource(id=uuid4(), crew_id=crew_id, name=body.name, resource_type="consumable", unit=body.unit,
                    total_quantity=None, stock_quantity=body.initial_quantity, description=body.description,
                    manager_member_id=body.manager_member_id, created_by_member_id=actor.member_id, status="available", lock_version=1, created_at=now, updated_at=now)
                session.add(row)
                delta, kind, reason, before = body.initial_quantity, "initial", "初始化库存", 0
            else:
                check_version(row, body.expected_lock_version)
                if row.status == "disabled" and body.movement_type == "issue": raise AuthError(409, "RESOURCE_UNAVAILABLE", "停用耗材不能领用")
                delta, kind, reason, before = body.quantity_delta, body.movement_type, body.reason, row.stock_quantity
                balance = before + delta
                if not 0 <= balance <= 2_147_483_647: raise AuthError(409, "STOCK_QUANTITY_CONFLICT", "库存不足或超出可存储范围")
                row.stock_quantity = balance; row.lock_version += 1; row.updated_at = now
                if delta < 0:
                    from silencio_set.plans.dependencies import mark_plan_review
                    await mark_plan_review(session, crew_id, 'stock_changed', now=now, resource_id=row.id)
            await session.flush()
            movement = ConsumableMovement(id=uuid4(), crew_id=crew_id, resource_id=row.id, quantity_delta=delta,
                balance_after=row.stock_quantity, movement_type=kind, reason=reason, operated_by=actor.member_id, created_at=now) if delta else None
            if movement: session.add(movement)
            result = StockResult(resource=stock_view(row), movement=MovementView.model_validate(movement) if movement else None)
            session.add(AuditLog(crew_id=crew_id, actor_type="user", actor_id=user_id, action=action, target_type="resource", target_id=row.id,
                result="success", request_id=request_id, before_summary={"stock_quantity": before}, after_summary=result.model_dump(mode="json")))
            session.add(ResourceReceipt(crew_id=crew_id, user_id=user_id, resource_id=row.id, membership_joined_at=actor.joined_at,
                key_digest=key, request_digest=digest, response_snapshot=result.model_dump(mode="json")))
            await session.flush()
        return result

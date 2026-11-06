"""The queued turn is its own transactional outbox; RabbitMQ carries only its ID."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from silencio_set.accounts.errors import AuthError
from silencio_set.accounts.models import User
from silencio_set.diane.memory_models import Conversation, ConversationTurn
from silencio_set.diane.memory_schemas import TurnView

AGENT_TASK = 'silencio.diane.answer'


@dataclass(frozen=True)
class DispatchClaim:
    turn_id: UUID
    dispatch_after: datetime


@dataclass(frozen=True)
class Execution:
    user: User
    crew_id: UUID
    conversation_id: UUID
    turn: TurnView
    token: UUID
    request_id: str


class AgentJobs:
    def __init__(self, conversations):
        self.conversations, self.sessions = conversations, conversations.sessions

    async def outbox(self):
        async with self.sessions() as session, session.begin():
            now = await session.scalar(select(func.clock_timestamp()))
            rows = (await session.scalars(select(ConversationTurn).where(
                ConversationTurn.status == 'queued', ConversationTurn.dispatched_at.is_(None),
                ConversationTurn.dispatch_after <= now, ConversationTurn.lease_expires_at > now)
                .order_by(ConversationTurn.created_at).limit(10).with_for_update(skip_locked=True))).all()
            for row in rows: row.dispatch_after = now+timedelta(seconds=10)
            await session.flush()
            return [DispatchClaim(row.id, row.dispatch_after) for row in rows]

    async def published(self, claim):
        async with self.sessions() as session, session.begin():
            await session.execute(update(ConversationTurn).where(ConversationTurn.id == claim.turn_id,
                ConversationTurn.dispatch_after == claim.dispatch_after).values(dispatched_at=func.clock_timestamp()))

    async def claim(self, turn_id, timeout_seconds):
        async with self.sessions() as session, session.begin():
            conversation_id = await session.scalar(select(ConversationTurn.conversation_id).where(ConversationTurn.id == turn_id))
            if conversation_id is None: return None
            conversation = await session.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
            await self.conversations.expire(session, conversation.id)
            turn = await session.get(ConversationTurn, turn_id, populate_existing=True)
            if turn.status != 'queued': return None
            user = await session.get(User, conversation.user_id)
            try:
                await self.conversations.conversation(session, user, conversation.crew_id, conversation.id)
            except AuthError:
                self.terminal(turn, 'failed', 'access_denied', await session.scalar(select(func.clock_timestamp())))
                return None
            now = await session.scalar(select(func.clock_timestamp()))
            token = uuid4()
            turn.status, turn.phase, turn.execution_token = 'running', 'model', token
            turn.progress_version += 1
            turn.lease_expires_at = now+timedelta(seconds=timeout_seconds+30)
            await session.flush()
            return Execution(user, conversation.crew_id, conversation.id, TurnView.model_validate(turn),
                token, turn.request_id or uuid4().hex)

    async def progress(self, execution, phase):
        async with self.sessions() as session, session.begin():
            turn = await session.scalar(select(ConversationTurn).where(ConversationTurn.id == execution.turn.id).with_for_update())
            now = await session.scalar(select(func.clock_timestamp()))
            if turn.status != 'running' or turn.execution_token != execution.token or turn.lease_expires_at <= now:
                raise AuthError(409, 'RUN_NO_LONGER_ACTIVE', '本次执行已中断。')
            turn.phase = phase
            turn.progress_version += 1
            await session.flush()
            return turn.progress_version

    async def complete(self, execution, result=None):
        async with self.sessions() as session, session.begin():
            await session.scalar(select(Conversation).where(Conversation.id == execution.conversation_id).with_for_update())
            turn = await session.get(ConversationTurn, execution.turn.id, populate_existing=True)
            now = await session.scalar(select(func.clock_timestamp()))
            if turn.status != 'running' or turn.execution_token != execution.token: return None
            if turn.lease_expires_at <= now:
                self.terminal(turn, 'interrupted', 'interrupted', now)
            else:
                try:
                    await self.conversations.conversation(session, execution.user, execution.crew_id, execution.conversation_id)
                except AuthError:
                    self.terminal(turn, 'failed', 'access_denied', now)
                else:
                    turn.result = result.model_dump(mode='json') if result is not None else None
                    status = 'completed' if result is not None and result.status == 'completed' else 'failed'
                    self.terminal(turn, status, status, now)
            await session.flush()
            return TurnView.model_validate(turn)

    @staticmethod
    def terminal(turn, status, phase, now):
        turn.status, turn.phase, turn.finished_at = status, phase, now
        turn.progress_version += 1

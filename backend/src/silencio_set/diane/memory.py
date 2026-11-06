from datetime import timedelta

from sqlalchemy import func, select
from langchain_core.messages import AIMessage, HumanMessage

from silencio_set.accounts.errors import AuthError
from silencio_set.diane.memory_models import Conversation, ConversationTurn, Preference
from silencio_set.diane.memory_schemas import ConversationHistory, ConversationView, Preferences, TurnView
from silencio_set.resources.repository import resource_actor
from silencio_set.tasks.repository import require_permission


class ConversationRepository:
    def __init__(self, sessions): self.sessions = sessions

    async def actor(self, session, user, crew_id):
        actor = await resource_actor(session, user.id, crew_id)
        require_permission(actor, 'crew.read')
        return actor

    @staticmethod
    def scoped(model, user, crew_id, actor):
        return select(model).where(model.user_id == user.id, model.crew_id == crew_id,
            model.member_id == actor.member_id, model.membership_joined_at == actor.joined_at)

    async def conversation(self, session, user, crew_id, conversation_id, *, lock=False):
        actor = await self.actor(session, user, crew_id)
        query = self.scoped(Conversation, user, crew_id, actor).where(Conversation.id == conversation_id)
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None: raise AuthError(404, 'NOT_FOUND', '对话不存在或无权访问。')
        return row

    async def create(self, user, crew_id):
        async with self.sessions() as session, session.begin():
            actor = await self.actor(session, user, crew_id)
            row = Conversation(crew_id=crew_id, user_id=user.id, member_id=actor.member_id,
                membership_joined_at=actor.joined_at, title='新对话')
            session.add(row); await session.flush()
            return ConversationView.model_validate(row)

    async def list(self, user, crew_id):
        async with self.sessions() as session:
            actor = await self.actor(session, user, crew_id)
            rows = (await session.scalars(self.scoped(Conversation, user, crew_id, actor)
                .order_by(Conversation.updated_at.desc(), Conversation.id).limit(20))).all()
            return [ConversationView.model_validate(row) for row in rows]

    async def history(self, user, crew_id, conversation_id, before=None):
        async with self.sessions() as session, session.begin():
            row = await self.conversation(session, user, crew_id, conversation_id, lock=True)
            await self.expire(session, row.id)
            query = select(ConversationTurn).where(ConversationTurn.conversation_id == row.id)
            if before is not None: query = query.where(ConversationTurn.ordinal < before)
            turns = (await session.scalars(query.order_by(ConversationTurn.ordinal.desc()).limit(51))).all()
            return ConversationHistory(conversation=ConversationView.model_validate(row),
                turns=[TurnView.model_validate(item) for item in reversed(turns[:50])], has_earlier=len(turns)>50)

    async def expire(self, session, conversation_id):
        now = await session.scalar(select(func.clock_timestamp()))
        stale = (await session.scalars(select(ConversationTurn).where(
            ConversationTurn.conversation_id == conversation_id, ConversationTurn.status.in_(['queued','running']),
            ConversationTurn.lease_expires_at <= now))).all()
        for turn in stale:
            turn.status, turn.phase, turn.finished_at = 'interrupted', 'interrupted', now
            turn.progress_version += 1
        await session.flush()

    async def begin(self, user, crew_id, conversation_id, body, timeout_seconds, *, queued=False, request_id=None):
        async with self.sessions() as session, session.begin():
            row = await self.conversation(session, user, crew_id, conversation_id, lock=True)
            await self.expire(session, row.id)
            existing = await session.get(ConversationTurn, body.turn_id)
            if existing is not None:
                if existing.conversation_id != row.id or existing.question != body.question:
                    raise AuthError(409, 'IDEMPOTENCY_CONFLICT', '请求标识已使用，请重新读取对话。')
                return TurnView.model_validate(existing), False
            running = await session.scalar(select(ConversationTurn.id).where(
                ConversationTurn.conversation_id == row.id, ConversationTurn.status.in_(['queued','running'])))
            if running: raise AuthError(409, 'CONVERSATION_BUSY', '当前对话仍在处理上一条消息。')
            now = await session.scalar(select(func.clock_timestamp()))
            turn = ConversationTurn(id=body.turn_id, conversation_id=row.id, ordinal=row.next_ordinal,
                question=body.question, status='queued' if queued else 'running', created_at=now,
                phase='queued' if queued else 'model', progress_version=1, request_id=request_id,
                dispatch_after=now if queued else None,
                lease_expires_at=now+timedelta(seconds=300 if queued else timeout_seconds+15))
            row.next_ordinal += 1
            row.updated_at = now
            if turn.ordinal == 1: row.title = body.question[:80]
            session.add(turn); await session.flush()
            return TurnView.model_validate(turn), True

    async def context(self, user, crew_id, conversation_id, ordinal):
        async with self.sessions() as session:
            await self.conversation(session, user, crew_id, conversation_id)
            rows = (await session.scalars(select(ConversationTurn).where(
                ConversationTurn.conversation_id == conversation_id, ConversationTurn.ordinal < ordinal,
                ConversationTurn.status == 'completed').order_by(ConversationTurn.ordinal.desc()).limit(6))).all()
            messages, size = [], 0
            for row in rows:
                answer = row.result['answer']
                pair_size = len((row.question+answer).encode('utf-8'))
                if size+pair_size > 12000: break
                messages[0:0] = [HumanMessage(content=row.question), AIMessage(content=answer)]
                size += pair_size
            return messages

    async def finish(self, user, crew_id, conversation_id, turn_id, result=None):
        async with self.sessions() as session, session.begin():
            row = await self.conversation(session, user, crew_id, conversation_id, lock=True)
            turn = await session.scalar(select(ConversationTurn).where(
                ConversationTurn.conversation_id == row.id, ConversationTurn.id == turn_id))
            if turn.status != 'running': return TurnView.model_validate(turn)
            turn.status = 'completed' if result is not None and result.status == 'completed' else 'failed'
            turn.phase = turn.status
            turn.progress_version += 1
            turn.result = result.model_dump(mode='json') if result is not None else None
            turn.finished_at = await session.scalar(select(func.clock_timestamp()))
            row.updated_at = turn.finished_at
            await session.flush()
            return TurnView.model_validate(turn)

    async def get_turn(self, user, crew_id, conversation_id, turn_id):
        async with self.sessions() as session, session.begin():
            row = await self.conversation(session, user, crew_id, conversation_id, lock=True)
            await self.expire(session, row.id)
            turn = await session.scalar(select(ConversationTurn).where(
                ConversationTurn.conversation_id == row.id, ConversationTurn.id == turn_id))
            if turn is None: raise AuthError(404, 'NOT_FOUND', '消息不存在或无权访问。')
            return TurnView.model_validate(turn)

    async def preferences(self, user, crew_id, body=None):
        async with self.sessions() as session, session.begin():
            # Serialize preference edits with membership changes and other edits.
            actor = await resource_actor(session, user.id, crew_id, writing=body is not None)
            require_permission(actor, 'crew.read')
            row = await session.scalar(self.scoped(Preference, user, crew_id, actor))
            if body is not None:
                if row is None:
                    row = Preference(crew_id=crew_id, user_id=user.id, member_id=actor.member_id,
                        membership_joined_at=actor.joined_at)
                    session.add(row)
                row.response_style, row.focus = body.response_style, body.focus
                await session.flush()
            return Preferences.model_validate(row) if row is not None else Preferences()

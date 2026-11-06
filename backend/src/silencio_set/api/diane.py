from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from silencio_set.accounts.errors import AuthError
from silencio_set.api.accounts import SESSION_COOKIE, auth_service, client_ip, completed_user, csrf_browser
from silencio_set.core.http import Envelope
from silencio_set.diane.schemas import DianeStatus, QueryInput, QueryResult
from silencio_set.diane.proposal_schemas import DraftRequest, ProposalDraftResult, ProposalDecision, ProposalView, ProposalPage
from silencio_set.diane.memory_schemas import ConversationHistory, ConversationQuery, ConversationView, Preferences, TurnView

router = APIRouter(prefix='/api/v1/crews/{crew_id}/diane', tags=['diane'])


@router.get('/conversations', response_model=Envelope[list[ConversationView]])
async def conversations(crew_id: UUID, request: Request, user=Depends(completed_user)):
    return Envelope(data=await request.app.state.diane_conversations.list(user, crew_id), request_id=request.state.request_id)


@router.post('/conversations', response_model=Envelope[ConversationView], dependencies=[Depends(csrf_browser)], status_code=201)
async def create_conversation(crew_id: UUID, request: Request, user=Depends(completed_user)):
    return Envelope(data=await request.app.state.diane_conversations.create(user, crew_id), request_id=request.state.request_id)


@router.get('/conversations/{conversation_id}', response_model=Envelope[ConversationHistory])
async def conversation_history(crew_id: UUID, conversation_id: UUID, request: Request,
        before: int | None = Query(default=None, ge=1), user=Depends(completed_user)):
    data = await request.app.state.diane_conversations.history(user, crew_id, conversation_id, before)
    return Envelope(data=data, request_id=request.state.request_id)


@router.post('/conversations/{conversation_id}/queries', response_model=Envelope[TurnView], dependencies=[Depends(csrf_browser)])
async def converse(crew_id: UUID, conversation_id: UUID, body: ConversationQuery, request: Request,
        user=Depends(completed_user), auth=Depends(auth_service)):
    service = request.app.state.diane
    if service is None: raise AuthError(503, 'DIANE_DISABLED', 'Diane 尚未启用。')
    await auth.cache.limit('diane-query', client_ip(request), str(user.id), ip_limit=12, identity_limit=6)
    data = await service.converse(user, crew_id, conversation_id, body, request.state.request_id)
    return Envelope(data=data, request_id=request.state.request_id)


@router.post('/conversations/{conversation_id}/runs', response_model=Envelope[TurnView],
    dependencies=[Depends(csrf_browser)], status_code=202)
async def enqueue(crew_id: UUID, conversation_id: UUID, body: ConversationQuery, request: Request,
        user=Depends(completed_user), auth=Depends(auth_service)):
    if not request.app.state.settings.diane_background_enabled:
        raise AuthError(503, 'DIANE_BACKGROUND_DISABLED', 'Diane 后台执行尚未启用。')
    await auth.cache.limit('diane-query', client_ip(request), str(user.id), ip_limit=12, identity_limit=6)
    data, _ = await request.app.state.diane_conversations.begin(user, crew_id, conversation_id, body,
        request.app.state.settings.diane_timeout_seconds, queued=True, request_id=request.state.request_id)
    return Envelope(data=data, request_id=request.state.request_id)


@router.get('/conversations/{conversation_id}/runs/{turn_id}', response_model=Envelope[TurnView])
async def run_status(crew_id: UUID, conversation_id: UUID, turn_id: UUID, request: Request, user=Depends(completed_user)):
    data = await request.app.state.diane_conversations.get_turn(user, crew_id, conversation_id, turn_id)
    return Envelope(data=data, request_id=request.state.request_id)


@router.get('/conversations/{conversation_id}/runs/{turn_id}/events')
async def run_events(crew_id: UUID, conversation_id: UUID, turn_id: UUID, request: Request,
        user=Depends(completed_user), auth=Depends(auth_service)):
    await auth.cache.limit('diane-events', client_ip(request), str(user.id), ip_limit=60, identity_limit=30)
    repository = request.app.state.diane_conversations
    await repository.get_turn(user, crew_id, conversation_id, turn_id)
    from silencio_set.diane.events import ProgressEvents
    events = ProgressEvents(request.app.state.dependencies.redis)
    token = request.cookies.get(SESSION_COOKIE)
    async def reauthorize():
        current = await auth.current_user(token)
        if current.id != user.id: raise AuthError(401, 'SESSION_EXPIRED', '请重新登录。')
    return StreamingResponse(events.stream(repository, user, crew_id, conversation_id, turn_id, reauthorize),
        media_type='text/event-stream', headers={'Cache-Control':'no-store', 'X-Accel-Buffering':'no'})


@router.get('/preferences', response_model=Envelope[Preferences])
async def preferences(crew_id: UUID, request: Request, user=Depends(completed_user)):
    data = await request.app.state.diane_conversations.preferences(user, crew_id)
    return Envelope(data=data, request_id=request.state.request_id)


@router.put('/preferences', response_model=Envelope[Preferences], dependencies=[Depends(csrf_browser)])
async def save_preferences(crew_id: UUID, body: Preferences, request: Request, user=Depends(completed_user)):
    data = await request.app.state.diane_conversations.preferences(user, crew_id, body)
    return Envelope(data=data, request_id=request.state.request_id)


@router.get('', response_model=Envelope[DianeStatus])
async def status(crew_id: UUID, request: Request, user=Depends(completed_user)):
    await request.app.state.crews.execute(user, 'crew.read', request.state.request_id, crew_id=crew_id)
    return Envelope(data=DianeStatus(enabled=request.app.state.settings.diane_enabled,
        model=request.app.state.settings.llm_model if request.app.state.settings.diane_enabled else None,
        execution_mode='background' if request.app.state.settings.diane_background_enabled else 'synchronous',
        timeout_seconds=request.app.state.settings.diane_timeout_seconds), request_id=request.state.request_id)


@router.post('/queries', response_model=Envelope[QueryResult], dependencies=[Depends(csrf_browser)])
async def query(crew_id: UUID, body: QueryInput, request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    service = request.app.state.diane
    if service is None: raise AuthError(503, 'DIANE_DISABLED', 'Diane 尚未启用。')
    await auth.cache.limit('diane-query', client_ip(request), str(user.id), ip_limit=12, identity_limit=6)
    return Envelope(data=await service.query(user, crew_id, body.question, request.state.request_id), request_id=request.state.request_id)


@router.post('/task-proposals/draft', response_model=Envelope[ProposalDraftResult], dependencies=[Depends(csrf_browser)])
async def draft_tasks(crew_id: UUID, body: DraftRequest, request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    if request.app.state.diane is None: raise AuthError(503, 'DIANE_DISABLED', 'Diane 尚未启用。')
    await auth.cache.limit('diane-query', client_ip(request), str(user.id), ip_limit=12, identity_limit=6)
    result = await request.app.state.diane.draft_tasks(user, crew_id, body, request.state.request_id, request.app.state.task_proposals)
    return Envelope(data=result, request_id=request.state.request_id)


@router.get('/task-proposals', response_model=Envelope[ProposalPage])
async def proposals(crew_id: UUID, request: Request, user=Depends(completed_user)):
    return Envelope(data=ProposalPage(items=await request.app.state.task_proposals.list(user, crew_id)), request_id=request.state.request_id)


@router.post('/task-proposals/{proposal_id}/decisions', response_model=Envelope[ProposalView], dependencies=[Depends(csrf_browser)])
async def decide(crew_id: UUID, proposal_id: UUID, body: ProposalDecision, request: Request, user=Depends(completed_user), auth=Depends(auth_service)):
    await auth.cache.limit('diane-decision', client_ip(request), str(user.id), ip_limit=30, identity_limit=15)
    result = await request.app.state.task_proposals.decide(user, crew_id, proposal_id, body, request.state.request_id)
    return Envelope(data=result, request_id=request.state.request_id)

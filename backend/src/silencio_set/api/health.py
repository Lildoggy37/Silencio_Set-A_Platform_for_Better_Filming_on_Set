from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from silencio_set.core.http import Envelope, error_response
from silencio_set.infrastructure.health import DependencyStatus

router = APIRouter(prefix="/api/v1/health", tags=["health"])


class LiveStatus(BaseModel):
    status: Literal["alive"] = "alive"


class ReadyStatus(BaseModel):
    status: Literal["ready"] = "ready"
    dependencies: dict[str, DependencyStatus]


async def dependency_report(request: Request):
    return await request.app.state.dependencies.report()


@router.get("/live", response_model=Envelope[LiveStatus])
async def live(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=LiveStatus(), request_id=request.state.request_id)


@router.get("/ready", response_model=Envelope[ReadyStatus], responses={503: {"description": "Dependencies not ready"}})
async def ready(request: Request, response: Response, report=Depends(dependency_report)):
    if set(report) != {"database", "redis", "rabbitmq"} or not all(state == "ok" for state in report.values()):
        failure = error_response(
            request.state.request_id, 503, "DEPENDENCIES_NOT_READY",
            "依赖服务尚未就绪", {"dependencies": report},
        )
        failure.headers["Cache-Control"] = "no-store"
        return failure
    response.headers["Cache-Control"] = "no-store"
    return Envelope(data=ReadyStatus(dependencies=report), request_id=request.state.request_id)

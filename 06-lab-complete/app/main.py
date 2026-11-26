"""
Production AI Agent — Part 6 Final Project.

Chạy: uvicorn app.main:app --workers 2
Kiểm tra: python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_cost
from app.rate_limiter import check_rate_limit
from app import redis_client
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log là một JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__
        # default=str: giá trị lạ (datetime, Exception...) không làm hỏng dòng log
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers[:] = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan: startup / graceful shutdown (tiêu chí 11)
#
# Khi nhận SIGTERM, uvicorn ngừng nhận kết nối mới, chờ các request đang chạy
# xong, rồi mới chạy phần sau `yield`. Không tự đăng ký signal handler để
# không chặn mất handler của uvicorn.
# ─────────────────────────────────────────────────────────
START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", version=settings.app_version, environment=settings.environment,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              api_key_configured=bool(settings.agent_api_key))
    if redis_client.config_error:
        log_event("config_error", logging.ERROR, variable="REDIS_URL",
                  error=redis_client.config_error)
    yield
    log_event("shutdown", uptime_seconds=round(time.time() - START_TIME, 1))
    redis_client.close()


app = FastAPI(title="Production AI Agent", version=settings.app_version, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log_event("request", method=request.method, path=request.url.path, status=status,
                  ms=round((time.perf_counter() - started) * 1000, 1), request_id=request_id)


# ─────────────────────────────────────────────────────────
# Xử lý lỗi: trả JSON rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_unavailable(request: Request, exc: redis.RedisError):
    # Không đếm được rate limit / budget thì từ chối (fail closed), không cho gọi LLM tự do.
    log_event("redis_error", logging.ERROR, path=request.url.path,
              error=redis_client.sanitize_error(exc))
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/")
def root():
    return {"app": "Production AI Agent", "version": settings.app_version,
            "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness (tiêu chí 4): process còn chạy là 200. KHÔNG kiểm Redis —
    Redis chết mà restart agent cũng không giúp gì."""
    return {"status": "ok", "version": settings.app_version,
            "environment": settings.environment,
            "uptime_seconds": round(time.time() - START_TIME, 1)}


@app.get("/ready")
def ready():
    """Readiness (tiêu chí 5): Redis không kết nối được → 503 để load balancer ngừng gửi traffic."""
    error = redis_client.ping_error()
    if error:
        log_event("redis_unavailable", logging.WARNING, error=error)
        return JSONResponse(status_code=503, content={"ready": False, "redis": "unavailable"})
    return {"ready": True, "redis": "ok"}


def _estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))  # ~4 ký tự / token


# `def` (không phải `async def`): FastAPI chạy hàm trong threadpool, nên lời gọi LLM
# chặn luồng không làm đứng event loop — /health và SIGTERM vẫn được xử lý ngay.
@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)  # 429
    check_budget()             # 402
    answer = llm_ask(body.question)
    cost = record_cost(_estimate_tokens(body.question), _estimate_tokens(answer))
    log_event("llm_call", question_chars=len(body.question), answer_chars=len(answer),
              cost_usd=round(cost, 8))
    return {"question": body.question, "answer": answer}

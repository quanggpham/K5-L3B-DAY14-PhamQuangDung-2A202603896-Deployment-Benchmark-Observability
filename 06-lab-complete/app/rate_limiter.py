"""
Rate limiting (tiêu chí 7) — sliding window log trên Redis sorted set.

Mỗi API key có một sorted set: member = một request, score = thời điểm gọi.
Mọi worker / instance dùng chung Redis nên giới hạn đúng cho cả hệ thống,
và state vẫn còn sau khi restart agent.
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(api_key: str) -> str:
    # Không lưu API key gốc trong Redis, chỉ lưu hash.
    return "ratelimit:" + hashlib.sha256(api_key.encode()).hexdigest()[:24]


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    # Ghi nhận request trước rồi mới đếm, trong một transaction (MULTI/EXEC):
    # hai request đồng thời không thể cùng thấy "còn 1 chỗ" rồi cùng lọt qua.
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)
    pipe.zadd(bucket, {member: now})
    pipe.zcard(bucket)
    pipe.expire(bucket, WINDOW_SECONDS + 1)
    _, _, count, _ = pipe.execute()

    if count <= limit:
        return

    # Vượt giới hạn: bỏ request vừa ghi để nó không chiếm chỗ trong cửa sổ.
    r.zrem(bucket, member)
    oldest = r.zrange(bucket, 0, 0, withscores=True)
    retry_after = math.ceil(oldest[0][1] + WINDOW_SECONDS - now) if oldest else WINDOW_SECONDS
    retry_after = max(1, retry_after)
    raise HTTPException(
        status_code=429,
        detail={"error": "Rate limit exceeded", "limit": limit,
                "window_seconds": WINDOW_SECONDS, "retry_after_seconds": retry_after},
        headers={"Retry-After": str(retry_after),
                 "X-RateLimit-Limit": str(limit),
                 "X-RateLimit-Remaining": "0"},
    )

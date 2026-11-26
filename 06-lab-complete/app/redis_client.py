"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

`redis.from_url` chỉ tạo connection pool, chưa mở kết nối, nên app vẫn khởi động
được khi Redis chưa sẵn sàng. Timeout ngắn để /ready và /ask không treo khi Redis chết.

REDIS_URL sai định dạng (rỗng, hoặc tham chiếu biến của platform chưa được thay) cũng
không được làm worker chết lúc import: uvicorn sẽ khởi động lại worker mãi mãi trong
khi dashboard vẫn báo deploy thành công. Thay vào đó coi như Redis không dùng được:
/ready → 503, /ask → 503, và lifespan ghi log lỗi cấu hình.
"""
import re
from urllib.parse import unquote, urlsplit

import redis

from app.config import settings

_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]*@")


def _redis_passwords() -> set[str]:
    """Mật khẩu ở cả dạng trong URL (percent-encoded) lẫn dạng đã giải mã mà redis-py dùng."""
    try:
        raw = urlsplit(settings.redis_url).password or ""
    except ValueError:
        return set()
    return {p for p in (raw, unquote(raw)) if p}


def sanitize_error(exc: BaseException) -> str:
    """Mô tả lỗi an toàn để ghi log: bỏ user:password trong mọi URL và mật khẩu Redis
    nếu nó xuất hiện ở bất kỳ đâu. Giữ lại host:port để vẫn chẩn đoán được."""
    text = _USERINFO.sub(r"\1***@", f"{type(exc).__name__}: {exc}")
    for password in sorted(_redis_passwords(), key=len, reverse=True):
        text = text.replace(password, "***")
    return text[:200]


class _InvalidRedisConfig:
    """Thay cho client khi REDIS_URL không hợp lệ: mọi lệnh đều báo lỗi kết nối."""

    def __getattr__(self, name):
        raise redis.ConnectionError("REDIS_URL is not a valid redis:// URL")


try:
    r = redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=2,
        health_check_interval=30,
    )
    config_error: str | None = None
except ValueError as exc:
    r = _InvalidRedisConfig()
    config_error = sanitize_error(exc)


def ping_error() -> str | None:
    """None nếu Redis trả lời PING; ngược lại là mô tả lỗi (không chứa mật khẩu)."""
    try:
        r.ping()
        return None
    except redis.RedisError as exc:
        return sanitize_error(exc)


def redis_healthy() -> bool:
    return ping_error() is None


def close() -> None:
    try:
        r.close()
    except redis.RedisError:
        pass

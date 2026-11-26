"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

`redis.from_url` chỉ tạo connection pool, chưa mở kết nối, nên app vẫn khởi động
được khi Redis chưa sẵn sàng. Timeout ngắn để /ready và /ask không treo khi Redis chết.

REDIS_URL sai định dạng (rỗng, hoặc tham chiếu biến của platform chưa được thay) cũng
không được làm worker chết lúc import: uvicorn sẽ khởi động lại worker mãi mãi trong
khi dashboard vẫn báo deploy thành công. Thay vào đó coi như Redis không dùng được:
/ready → 503, /ask → 503, và lifespan ghi log lỗi cấu hình.
"""
import redis

from app.config import settings


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
except ValueError as exc:  # không đưa URL vào thông báo: URL chứa mật khẩu Redis
    r = _InvalidRedisConfig()
    config_error = str(exc)


def ping_error() -> str | None:
    """None nếu Redis trả lời PING; ngược lại là mô tả lỗi (không chứa mật khẩu)."""
    try:
        r.ping()
        return None
    except redis.RedisError as exc:
        return f"{type(exc).__name__}: {exc}"[:200]


def redis_healthy() -> bool:
    return ping_error() is None


def close() -> None:
    try:
        r.close()
    except redis.RedisError:
        pass

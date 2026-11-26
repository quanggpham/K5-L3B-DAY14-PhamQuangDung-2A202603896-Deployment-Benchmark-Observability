"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

Chạy local: `cp .env.example .env` rồi sửa giá trị; file .env chỉ bổ sung biến còn
thiếu, không ghi đè biến môi trường đã có (platform cloud luôn được ưu tiên).
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv(override=False)


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))
    environment: str = field(default_factory=lambda: _env("ENVIRONMENT", "development"))
    app_version: str = field(default_factory=lambda: _env("APP_VERSION", "1.0.0"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())

    # Secret: không có giá trị mặc định. Thiếu key thì /ask từ chối mọi request (401).
    agent_api_key: str = field(default_factory=lambda: _env("AGENT_API_KEY", ""))

    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))

    rate_limit_per_minute: int = field(
        default_factory=lambda: int(_env("RATE_LIMIT_PER_MINUTE", "10")))
    daily_budget_usd: float = field(
        default_factory=lambda: float(_env("DAILY_BUDGET_USD", "1.0")))
    monthly_budget_usd: float = field(
        default_factory=lambda: float(_env("MONTHLY_BUDGET_USD", "10.0")))

    def validate(self) -> "Settings":
        if self.environment == "production" and not self.agent_api_key:
            raise RuntimeError("AGENT_API_KEY must be set when ENVIRONMENT=production")
        if self.rate_limit_per_minute < 1:
            raise RuntimeError("RATE_LIMIT_PER_MINUTE must be >= 1")
        return self


settings = Settings().validate()

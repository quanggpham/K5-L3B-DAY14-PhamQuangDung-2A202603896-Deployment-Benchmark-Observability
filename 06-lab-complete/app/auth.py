"""
API Key authentication (tiêu chí 6).

Client gửi header `X-API-Key: <key>`; key phải trùng AGENT_API_KEY.
Thiếu hoặc sai key → 401. Không phân biệt hai trường hợp để không lộ thông tin.
"""
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho FastAPI — dùng trong /ask: Depends(verify_api_key). Trả về key hợp lệ."""
    expected = settings.agent_api_key
    # compare_digest: thời gian so sánh không phụ thuộc vào vị trí ký tự sai đầu tiên.
    if not expected or not api_key or not hmac.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key

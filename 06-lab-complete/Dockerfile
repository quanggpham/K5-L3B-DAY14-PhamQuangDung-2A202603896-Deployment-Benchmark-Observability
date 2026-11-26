# ============================================================
# Production image cho AI agent — multi-stage
#   builder : cài dependencies vào một virtualenv riêng
#   runtime : base slim sạch, chỉ copy venv + code, chạy bằng user non-root
# ============================================================

# ── Stage 1: builder ─────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# requirements.txt trước code → layer cài đặt được cache khi chỉ sửa code
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=3)"]

# `exec` thay shell bằng uvicorn → uvicorn là PID 1, nhận thẳng SIGTERM từ `docker stop`.
# Shell chỉ cần để đọc ${PORT} do Railway/Render cấp.
# Graceful timeout 8s < 10s mặc định của `docker stop` trước khi SIGKILL.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout-graceful-shutdown 8"]

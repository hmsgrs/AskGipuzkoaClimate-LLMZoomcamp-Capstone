FROM ghcr.io/astral-sh/uv:0.11.19 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY .streamlit ./.streamlit
COPY data/snapshots ./data/snapshots

RUN DATA_MODE=snapshot \
    SQLITE_DATABASE=/app/data/snapshots/gipuzkoa-demo-2026-07-22/snapshot.sqlite \
    RETRIEVAL_BACKEND=sqlite_fts5 \
    OPENAI_API_KEY=build-smoke-test \
    python -c "from streamlit.testing.v1 import AppTest; app = AppTest.from_file('/app/app/streamlit_app.py'); app.run(timeout=20); assert not app.exception, app.exception"

CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0"]

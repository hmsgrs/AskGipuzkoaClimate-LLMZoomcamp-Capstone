FROM ghcr.io/astral-sh/uv:0.11.19 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY .streamlit ./.streamlit
COPY data/snapshots ./data/snapshots

CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0"]

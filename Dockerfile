FROM ghcr.io/astral-sh/uv:0.9 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv venv /opt/venv && uv pip install --python /opt/venv/bin/python .

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --create-home app
COPY --from=builder /opt/venv /opt/venv
USER app
EXPOSE 8000
CMD ["polymarket-mcp"]

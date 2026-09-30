FROM ghcr.io/astral-sh/uv:0.11.23 AS uv

FROM python:3.13-slim
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000
USER 10001:10001
EXPOSE 8000
CMD ["concierge-agent"]


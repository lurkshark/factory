FROM node:24-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /usr/local/bin/

# PROJECT TOOLCHAIN: install the project's language tools and dependencies here.

RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code @openai/codex \
    && userdel node \
    && useradd --uid 1000 --create-home --home-dir /home/worker --shell /bin/bash worker \
    && git config --system --add safe.directory /workspace

COPY --chown=worker:worker tools/pyproject.toml tools/uv.lock /opt/specflow-tools/

USER worker
ENV HOME=/home/worker
# Keep Linux dependencies outside the mounted checkout's host environment.
ENV UV_PROJECT_ENVIRONMENT=/opt/specflow-tools/.venv
RUN uv sync --project /opt/specflow-tools --locked --no-dev
WORKDIR /workspace

FROM node:24-bookworm

# PROJECT TOOLCHAIN: install the project's language tools and dependencies here.

RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code @openai/codex \
    && userdel node \
    && useradd --uid 1000 --create-home --home-dir /home/worker --shell /bin/bash worker

USER worker
ENV HOME=/home/worker
WORKDIR /workspace

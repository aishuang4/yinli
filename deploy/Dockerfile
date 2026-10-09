FROM python:3.11-slim-bookworm

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g; s/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --default-index https://mirrors.aliyun.com/pypi/simple

COPY backend/src ./src
COPY config/product-features.json /app/config/product-features.json

# 对话里的 Shell 在这个容器内执行。slim 镜像没有这些命令时，会报 /bin/sh: curl: not found。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        file \
        git \
        jq \
        less \
        procps \
        ripgrep \
        unzip \
        zip \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    AGENT_WORKSTATION_PRODUCT_FEATURES_PATH=/app/config/product-features.json

CMD ["uv", "run", "python", "-m", "uvicorn", "src.server.main:app", "--host", "127.0.0.1", "--port", "8765", "--no-proxy-headers"]

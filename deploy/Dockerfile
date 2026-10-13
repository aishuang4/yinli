FROM python:3.11-slim-bookworm

# 伺服器建置維持 1，走阿里雲。本機 Apple Silicon 連不上該鏡像時設 0。
ARG USE_CN_MIRROR=1

RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      sed -i 's/deb.debian.org/mirrors.aliyun.com/g; s/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && if [ "$USE_CN_MIRROR" = "1" ]; then \
         pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv; \
       else \
         pip install --no-cache-dir uv; \
       fi

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
# 鎖檔裡的 wheel 網址是 files.pythonhosted.org。--frozen 直接抓這些網址，
# --default-index 不會改寫它們。伺服器映像內換成阿里雲的同一路徑，倉庫裡的鎖檔不動。
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      sed -i \
        -e 's|https://files.pythonhosted.org|https://mirrors.aliyun.com/pypi|g' \
        -e 's|https://pypi.org/simple|https://mirrors.aliyun.com/pypi/simple|g' \
        uv.lock; \
    fi \
    && uv sync --frozen --no-dev

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

CMD ["uv", "run", "python", "-m", "uvicorn", "src.server.main:app", "--host", "127.0.0.1", "--port", "8765", "--workers", "1", "--no-proxy-headers"]

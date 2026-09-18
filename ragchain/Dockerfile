# syntax=docker/dockerfile:1
FROM python:3.12-slim

# 依赖在镜像内，**代码不在镜像内**（与仓库根 docker/dev.Dockerfile 同一模式）：
#   - 镜像只装 ragchain/requirements.txt；
#   - 运行时由 ragchain/compose.yaml 把 ragchain/app bind mount 到 /app/app；
#   - 所以改代码 → 容器里重启进程即可（bash scripts/dev.sh ragchain restart），
#     **不需要 rebuild 镜像**；只有 requirements.txt 变化才需要重建。
# build context = ragchain/ 自身，不依赖根 .dockerignore 白名单。
ARG PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_INDEX_URL=${PYPI_INDEX} \
    PATH=/opt/venv/bin:${PATH}

WORKDIR /app

RUN pip install --no-cache-dir uv \
 && uv venv /opt/venv --python 3.12

COPY requirements.txt /tmp/requirements.txt
# 装完顺手记录依赖指纹：`bash scripts/dev.sh ragchain check` 用它判断
# 「requirements.txt 变了、镜像该重建了」。代码不进镜像，所以代码改动不影响它。
RUN uv pip install --python /opt/venv/bin/python --no-cache \
        -r /tmp/requirements.txt --index-url "${PYPI_INDEX}" \
 && sha256sum /tmp/requirements.txt | cut -c1-12 > /opt/venv/.requirements-hash \
 && rm -f /tmp/requirements.txt

# 非 root 运行。/app/app 是 bind mount 的挂载点：镜像里只建空目录并授权，
# 真正的代码由 compose 挂进来（宿主侧 world-readable，appuser 可直接读）。
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 appuser \
 && mkdir -p /app/app \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]

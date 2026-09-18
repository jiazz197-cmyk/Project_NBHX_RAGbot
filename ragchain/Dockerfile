# syntax=docker/dockerfile:1
FROM python:3.12-slim

# 依赖 + 代码全部在镜像内；build context = ragchain/ 自身，不依赖根 .dockerignore 白名单
ARG PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_INDEX_URL=${PYPI_INDEX} \
    PATH=/opt/venv/bin:${PATH}

WORKDIR /app

RUN pip install --no-cache-dir uv \
 && uv venv /opt/venv --python 3.12

COPY requirements.txt /tmp/requirements.txt
RUN uv pip install --python /opt/venv/bin/python --no-cache \
        -r /tmp/requirements.txt --index-url "${PYPI_INDEX}" \
 && rm -f /tmp/requirements.txt

COPY app /app/app

# 非 root 运行
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]

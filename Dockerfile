FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 使用精确版本锁文件，避免镜像构建时解析浮动依赖版本。
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# 复制源码 + 汉化数据
COPY src/ src/
COPY translate/translation_map.py translate/translation_map.py
COPY translate/translation_map.json translate/translation_map.json

# 以非 root 用户运行，并将运行时写入限制在数据卷。
RUN addgroup --system monitor \
    && adduser --system --ingroup monitor --no-create-home monitor \
    && mkdir -p /app/data/logs /app/data/saves \
    && chown -R monitor:monitor /app

# 数据卷
VOLUME ["/app/data"]

USER monitor

# 健康检查
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1

ENTRYPOINT ["python", "-m", "src.main"]

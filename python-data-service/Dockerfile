FROM python:3.11-slim

# A 股业务时间按北京的日历走（CN_TZ 是固定 +8 偏移，不依赖 tzdata，这里再钉一层）
ENV TZ=Asia/Shanghai

# 布局说明：构建上下文是**仓库根**（见 docker-compose 的 python-data 服务）。
# ths_client.py 按"自身位置的上一级/vendor"解析 vendored 依赖，
# 所以镜像内必须保持 python-data-service/ 与 vendor/ 的相对结构：
#   /srv/python-data-service/...   ← WORKDIR
#   /srv/vendor/thspypc_src|qrcode_lib
WORKDIR /srv/python-data-service

# 安装依赖
COPY python-data-service/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码（vendor 在仓库根，此前 COPY . . 的上下文里没有它 →
# 容器内 THS 路由首次调用即 RuntimeError，这是那次断链的修复）
COPY python-data-service/ ./
COPY vendor/ /srv/vendor/

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

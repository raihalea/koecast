# 段階6-2-d: koecast ゲートウェイ本体のイメージ。
#
# uv が Python 3.14 を引いてセットアップする (devenv 環境の挙動と揃える)。
# bind は 0.0.0.0、ポートは 8000。compose 側で外部公開する。
#
# Riva NIM と LLM はそれぞれ別 compose / 既存 docker サービスを参照する:
#   - Riva NIM: service 名 parakeet (riva-nim.compose.yml 経由) → grpc 50051
#   - LLM (qwen36-mtp): host 側 127.0.0.1:8080 → コンテナからは host.docker.internal:8080
#                       (compose の extra_hosts: host.docker.internal:host-gateway で解決)

FROM python:3.14-slim

# uv を入れる。バージョンは固定しない (lockfile が真実)。
RUN pip install --no-cache-dir uv

WORKDIR /app

# 依存解決を先にやってレイヤーキャッシュを効かせる
COPY pyproject.toml uv.lock ./
# uv は project install のためにソースも必要 (hatchling wheel ビルド)
COPY src/ ./src/

# devenv 環境と同じく Python 3.14 を uv で引いて、本番依存のみインストール
RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "python", "-m", "dictation_gateway"]

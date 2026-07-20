# Build from repo root (dn-innov-a2ui) so widgets/ is in context:
#   gcloud builds submit .. --tag REGION-docker.pkg.dev/PROJECT/cloud-run-source-deploy/SERVICE:TAG -f employ_verification/Dockerfile

FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.13 \
  && useradd --create-home --uid 1000 app

WORKDIR /code

COPY employ_verification/pyproject.toml employ_verification/uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

COPY employ_verification/agents ./agents
COPY employ_verification/tools ./tools
COPY employ_verification/config ./config
COPY employ_verification/server ./server
COPY widgets ./widgets

RUN uv sync --frozen --no-dev \
  && chown -R app:app /code

ENV PYTHONPATH=/code
ENV PORT=8080

USER app

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "server.a2a_app:app", "--host", "0.0.0.0", "--port", "8080"]

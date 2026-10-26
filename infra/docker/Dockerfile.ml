FROM python:3.12-slim

# One image for both roles: the MLflow tracking server and the one-shot
# benchmark/training runs (same dependency set, no duplicate builds).
# prophet wheels bundle the compiled CmdStan model, so no compiler needed.
# postgresql-client: the mlflow service's entrypoint self-provisions its
# database via psql before `mlflow db upgrade`. libgomp1: LightGBM's OpenMP
# runtime, absent from the slim base image.
# sqlalchemy <2.1: mlflow 2.19 imports FallbackAsyncAdaptedQueuePool, removed
# in 2.1 (the ops API image is unaffected - it never imports mlflow).
# psycopg2: mlflow 2.19's tracking backend does NOT support the psycopg3
# dialect (integer=varchar cast errors on Postgres) - the server and the
# harness use +psycopg2 / +psycopg URIs respectively.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
    "mlflow==2.19.*" \
    "lightgbm>=4.5" \
    "prophet>=1.1.6" \
    "pandas>=2.2" \
    "sqlalchemy>=2.0,<2.1" \
    "psycopg[binary]>=3.3.6" \
    "psycopg2-binary>=2.9" \
    httpx

WORKDIR /ml
COPY ml/ /ml/

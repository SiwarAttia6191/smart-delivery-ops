FROM python:3.12-slim

# One image for both roles: the MLflow tracking server and the one-shot
# benchmark/training runs (same dependency set, no duplicate builds).
# prophet wheels bundle the compiled CmdStan model, so no compiler needed.
RUN pip install --no-cache-dir \
    "mlflow==2.19.*" \
    "lightgbm>=4.5" \
    "prophet>=1.1.6" \
    "pandas>=2.2" \
    "sqlalchemy>=2.1" \
    "psycopg[binary]>=3.3.6" \
    httpx

WORKDIR /ml
COPY ml/ /ml/

FROM python:3.13-slim
WORKDIR /app

RUN adduser --disabled-password --gecos "" myuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=myuser:myuser . .

ENV PATH="/home/myuser/.local/bin:$PATH"

USER myuser

# Agent Engine injects $PORT. main:app is the Starlette ASGI app mounted
# under /api (see main.py) — required for the V2 ingress OAuth-propagation
# path to Gemini Enterprise.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]

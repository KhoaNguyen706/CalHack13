FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
# SPACETIME_TOKEN: set it on a host (Railway, Maincloud publisher token); under docker compose it comes
# from the spacetime container through the shared volume. Hosts pass the port in PORT.
CMD ["sh", "-c", "export SPACETIME_TOKEN=\"${SPACETIME_TOKEN:-$(cat /shared/spacetime_token 2>/dev/null)}\"; exec uvicorn coordinator.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

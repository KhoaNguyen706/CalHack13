FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
# SPACETIME_TOKEN comes from the spacetime container through the shared volume.
CMD ["sh", "-c", "SPACETIME_TOKEN=$(cat /shared/spacetime_token 2>/dev/null) exec uvicorn coordinator.main:app --host 0.0.0.0 --port 8000"]

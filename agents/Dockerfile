FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8100
CMD ["uvicorn", "agents.main:app", "--host", "0.0.0.0", "--port", "8100"]

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN addgroup --system app && adduser --system --ingroup app app
USER app

CMD ["python", "main.py"]

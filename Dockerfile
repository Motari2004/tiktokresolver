# Playwright official Python image (includes Chromium + all system deps)
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

EXPOSE 10000

# Start FastAPI (Render requires binding to 0.0.0.0)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]
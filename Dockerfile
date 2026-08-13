FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY app/ ./app/
COPY static/ ./static/

# Create persistent data + resource directories. The data folder holds the
# SQLite DB and is mounted at runtime. The resorce folder is also mounted at
# runtime so operators can drop their own template.xlsx in without rebuilding
# the image — see docker-compose.yml and resorce/README.md.
RUN mkdir -p /app/data /app/resorce

# Expose port
EXPOSE 8000

# Run
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
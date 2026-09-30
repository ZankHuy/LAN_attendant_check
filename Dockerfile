FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code.
# Host layout: <repo>/app/app/main.py — i.e. the Python package `app` lives
# at <repo>/app/app/, with its own __init__.py.
# To match `uvicorn app.main:app` (which expects /app/app/main.py), we
# COPY app/app into /app/app — NOT `COPY app/app/ ./app/` (which would
# flatten the package and produce /app/main.py with no /app/app/ dir).
COPY app/app /app/app
COPY static/ /app/static

# Create persistent data + resource directories. The data folder holds the
# SQLite DB and is mounted at runtime. The resorce folder is also mounted at
# runtime so operators can drop their own template.xlsx in without rebuilding
# the image — see docker-compose.yml and resorce/README.md.
RUN mkdir -p /app/data /app/resorce

# Expose port
EXPOSE 8000

# Run uvicorn from /app so that the `app` python package (i.e. /app/app/) is
# discoverable on the import path. This is what makes `app.main:app` resolve.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
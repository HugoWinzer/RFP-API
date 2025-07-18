FROM python:3.11-slim

# 1) Install dependencies
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 2) Copy your app and FAISS index
COPY main.py .
COPY faiss_index/ ./faiss_index/

# 3) Expose and run
ENV PORT 8080
EXPOSE 8080
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8080"]

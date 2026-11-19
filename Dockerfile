FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python ml/train.py && python scripts/make_sample_data.py
EXPOSE 8501 8000
# Streamlit demo by default; for the API run: uvicorn api.main:app --host 0.0.0.0 --port 8000
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]

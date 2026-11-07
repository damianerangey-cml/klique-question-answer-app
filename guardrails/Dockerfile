FROM python:3.11
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config.tmpl.yml start.sh ./
EXPOSE 8000
CMD ["sh", "/app/start.sh"]

FROM python:3.11-buster

WORKDIR /scripts

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY oci_usage_cost.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "oci_usage_cost.py"]

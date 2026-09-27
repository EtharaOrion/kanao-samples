FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir pytest==8.4.1 defusedxml==0.7.1

RUN mkdir -p /app /var/log/svc /logs/agent /logs/verifier

COPY grader/ /grader/
COPY . /tests/
RUN chmod 0755 /tests/test.sh

WORKDIR /

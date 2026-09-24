# The verifier box for separate mode. Harbor builds this from tests/, never
# from environment/, and never uploads tests/ into it -- /tests/test.sh has to
# be in the image.
#
# Grading reads only the two artefacts the estate left behind, which Harbor
# uploads onto this box: /app/verdict.txt and /app/monitor_evidence.json. The
# services themselves are not rerun here, so the box carries the same base the
# estate is built from plus pytest, and nothing else.
FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
# defusedxml: the shared scorer (tests/score_reward.py, synced from the
# harness) parses pytest's junit xml with it.
RUN pip install --no-cache-dir pytest==8.4.1 defusedxml==0.7.1

# The paths the collected artefacts land on, created before the upload so a
# missing directory cannot turn an ungraded estate into a scored zero.
RUN mkdir -p /app /var/log/svc

COPY . /tests/
RUN chmod 0755 /tests/test.sh

WORKDIR /

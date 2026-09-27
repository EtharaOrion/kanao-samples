FROM python:3.12-slim AS pydeps
RUN pip install --no-cache-dir --target /pydeps defusedxml==0.7.1

FROM 426628337772.dkr.ecr.ap-south-1.amazonaws.com/kanao:58081829-marrowfleet-crew-roster-env@sha256:70a05e7ad77352f167e0dc8bd96ebc5fe599171ea0baff71ce116c04a325874e

RUN /usr/local/bin/task-start.sh

RUN mkdir -p /logs/agent /logs/verifier
COPY grader/ /grader/

COPY . /tests/
COPY --from=pydeps /pydeps/defusedxml /tests/defusedxml
RUN chmod 0755 /tests/test.sh

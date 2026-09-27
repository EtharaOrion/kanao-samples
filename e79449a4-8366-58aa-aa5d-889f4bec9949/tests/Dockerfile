FROM 426628337772.dkr.ecr.ap-south-1.amazonaws.com/kanao:e79449a4-iac_guardrails_paymesh-policy-env@sha256:7062d0e69bb6f8c4c23bb2a04c2f4cf72e9bc223149536d6c73d522d3d5cd4b0

RUN pip3 install --break-system-packages --no-cache-dir defusedxml==0.7.1

RUN mkdir -p /logs/agent /logs/verifier
COPY grader/ /grader/

COPY . /tests/
RUN chmod 0755 /tests/test.sh

WORKDIR /

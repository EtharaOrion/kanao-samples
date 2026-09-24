# The verifier box for separate mode. Harbor never uploads tests/ into it --
# /tests/test.sh has to be in the image -- and never builds this file at run
# time either: task.toml pins the pushed image ([verifier.environment]
# docker_image), so every machine pulls the same grader.
#
# Grading renders the policy catalogue with terraform console and runs the
# pipeline's own render steps, so the box carries terraform, the vendored
# provider mirror, python3, pytest, pyyaml and the seeded tree the collected
# artifacts are uploaded onto: it is built FROM the task's own pinned
# environment image, by digest, so the grader and the agent see the same
# bytes. The FROM line and the pins are maintained by scripts/build_push_pin.py
# in the harness; edit environment/Dockerfile.build or this file, then re-run
# it to rebuild, push and re-pin.
FROM 426628337772.dkr.ecr.ap-south-1.amazonaws.com/kanao:e79449a4-iac_guardrails_paymesh-policy-env@sha256:7062d0e69bb6f8c4c23bb2a04c2f4cf72e9bc223149536d6c73d522d3d5cd4b0

# defusedxml: the shared scorer (tests/score_reward.py, synced from the
# harness) parses pytest's junit xml with it. Same install shape as the
# base image's pytest (system python, hence the override flag).
RUN pip3 install --break-system-packages --no-cache-dir defusedxml==0.7.1

COPY . /tests/
RUN chmod 0755 /tests/test.sh

# The base image works from envs/prod, which the uploaded artifacts replace.
WORKDIR /

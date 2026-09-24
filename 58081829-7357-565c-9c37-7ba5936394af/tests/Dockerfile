# The verifier box for separate mode. Harbor never uploads tests/ into it --
# /tests/test.sh has to be in the image -- and never builds this file at run
# time either: task.toml pins the pushed image ([verifier.environment]
# docker_image), so every machine pulls the same grader.
#
# Grading reruns the roster run and reads the bureau's own tables, so the box
# carries marrowfleet and the tree the run is made from: it is built FROM the
# task's own pinned environment image, by digest, so the grader and the agent
# see the same bytes. The FROM line and the pins are maintained by
# scripts/build_push_pin.py in the harness; edit environment/Dockerfile.build
# or this file, then re-run it to rebuild, push and re-pin.
# The shared scorer (tests/score_reward.py, synced byte-for-byte from the
# harness) imports defusedxml, which this image cannot install: it has no
# pip and no package sources. So a throwaway stage that does have pip
# fetches the package, and only the resulting files are copied into /tests/,
# where python3 resolves them first (the script's own directory leads
# sys.path). Nothing else from this stage reaches the image.
FROM python:3.12-slim AS pydeps
RUN pip install --no-cache-dir --target /pydeps defusedxml==0.7.1

FROM 426628337772.dkr.ecr.ap-south-1.amazonaws.com/kanao:58081829-marrowfleet-crew-roster-env@sha256:70a05e7ad77352f167e0dc8bd96ebc5fe599171ea0baff71ce116c04a325874e

# The period the bureau left, seeded into the image rather than at container
# start. Harbor uploads the collected artifacts after the box is up, so a seed
# that ran at start would race them and overwrite the agent's work with the
# baseline. Baked here, the artifacts are the last thing written, and the
# readiness marker test.sh waits for is already in place.
RUN /usr/local/bin/task-start.sh

COPY . /tests/
COPY --from=pydeps /pydeps/defusedxml /tests/defusedxml
RUN chmod 0755 /tests/test.sh

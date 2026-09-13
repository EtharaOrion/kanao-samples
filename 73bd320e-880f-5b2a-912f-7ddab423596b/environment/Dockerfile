FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates coreutils diffutils file findutils grep less procps \
      python3 python3-pytest sed tzdata vim-tiny \
    && rm -rf /var/lib/apt/lists/* \
    && (apt-get purge -y binutils || true) \
    && : > /etc/apt/sources.list

RUN mkdir -p /usr/local/lib/tfmock
COPY src/tfmock/tfmock.py /usr/local/lib/tfmock/tfmock.py
RUN printf '#!/bin/sh\nexec python3 /usr/local/lib/tfmock/tfmock.py "$@"\n' > /usr/local/bin/tfmock \
    && chmod 0755 /usr/local/bin/tfmock

RUN mkdir -p /workspace /var/run
COPY workspace/ /workspace/
COPY task-start.sh /usr/local/bin/task-start.sh
RUN chmod +x /usr/local/bin/task-start.sh /workspace/bin/run-reconcile

WORKDIR /workspace
ENV TEST_DIR=/tests
CMD ["/usr/local/bin/task-start.sh"]

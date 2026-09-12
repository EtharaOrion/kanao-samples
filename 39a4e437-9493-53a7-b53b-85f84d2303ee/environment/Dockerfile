FROM node:22-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates coreutils diffutils file findutils grep less procps \
      python3 python3-pytest sed tzdata vim-tiny \
    && rm -rf /var/lib/apt/lists/* \
    && (apt-get purge -y binutils || true) \
    && : > /etc/apt/sources.list

COPY engine/engine.js /usr/local/lib/cicd/engine.js
RUN printf '#!/bin/sh\nexec node /usr/local/lib/cicd/engine.js "$@"\n' > /usr/local/bin/cicd \
    && chmod 0755 /usr/local/bin/cicd

RUN mkdir -p /workspace /var/run
COPY workspace/ /workspace/
COPY task-start.sh /usr/local/bin/task-start.sh
RUN chmod +x /usr/local/bin/task-start.sh /workspace/bin/run-pipeline

WORKDIR /workspace
ENV TEST_DIR=/tests
CMD ["/usr/local/bin/task-start.sh"]

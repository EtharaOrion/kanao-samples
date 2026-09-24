FROM golang:1.23-bookworm AS build

WORKDIR /src
COPY src/marrowfleet/main.go /src/marrowfleet/main.go
RUN cd /src/marrowfleet && go mod init marrowfleet >/dev/null \
    && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out-marrowfleet . \
    && rm -rf /src

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        asciinema tmux ca-certificates coreutils cron diffutils file findutils \
        grep less procps python3 python3-pytest sed tzdata vim-tiny \
    && rm -rf /var/lib/apt/lists/*

RUN (apt-get purge -y binutils || true) \
    && (apt-get autoremove -y || true) \
    && : > /etc/apt/sources.list \
    && rm -rf /etc/apt/sources.list.d/* /var/lib/apt/lists/*

COPY --from=build /out-marrowfleet /usr/local/bin/marrowfleet
RUN chmod 0755 /usr/local/bin/marrowfleet

RUN groupadd -r marrowfleet \
    && mkdir -p /var/lib/marrowfleet /var/log/marrowfleet /etc/marrowfleet/keys \
                /srv/bases \
    && : > /var/lib/marrowfleet/roster.register \
    && chmod 0644 /var/lib/marrowfleet/roster.register \
    && chgrp -R marrowfleet /etc/marrowfleet \
    && chmod 0750 /etc/marrowfleet/keys

COPY workspace/ /workspace/
COPY task-start.sh /usr/local/bin/task-start.sh
RUN chmod +x /usr/local/bin/task-start.sh /workspace/bin/run-roster \
    && chmod 0755 /workspace/tools/rostercheck/rostercheck.py

WORKDIR /workspace
ENV TEST_DIR=/tests

COPY policy-apply /usr/local/bin/policy-apply
RUN chmod 0755 /usr/local/bin/policy-apply

FROM golang:1.23-bookworm AS build
WORKDIR /src/relx
COPY src/relx/main.go ./main.go
RUN go mod init relx \
 && CGO_ENABLED=0 go build -trimpath -ldflags '-s -w' -o /out/relx ./... \
 && rm -rf /src

FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      bash ca-certificates coreutils diffutils file findutils grep less \
      procps python3 python3-pytest sed tzdata vim-tiny \
 && apt-get purge -y binutils \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && : > /etc/apt/sources.list

COPY --from=build /out/relx /usr/local/bin/relx
RUN chmod 0755 /usr/local/bin/relx

RUN mkdir -p /workspace /var/run
COPY workspace/ /workspace/
COPY task-start.sh /usr/local/bin/task-start.sh
RUN chmod 0755 /usr/local/bin/task-start.sh /workspace/bin/run-release

WORKDIR /workspace
ENV TEST_DIR=/tests
CMD ["/usr/local/bin/task-start.sh"]

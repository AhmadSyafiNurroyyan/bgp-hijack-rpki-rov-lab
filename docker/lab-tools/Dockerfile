# One image for every non-router node: targets, rpki placeholder client tooling, clients and gateway.
# Debian 12 ships tayga, unbound and nsd as packages.
FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      iproute2 iputils-ping curl dnsutils tcpdump procps python3 \
      tayga unbound nsd \
    && rm -rf /var/lib/apt/lists/*
# Stop the packaged services from interfering; the lab starts what it needs explicitly.
CMD ["sleep", "infinity"]

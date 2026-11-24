FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
# FIX: without this, Python buffers output and "docker logs" lags or shows nothing
ENV PYTHONUNBUFFERED=1

# FIX: dropped docker.io (installs the whole Docker daemon, ~300 MB, unused -
#      monitor.py talks to the socket through the Python SDK).
#      Add docker.io back only if you want to run "docker" by hand in here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    iputils-ping \
    net-tools \
    nmap \
    traceroute \
    tcpdump \
    curl \
    ca-certificates \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip3 install --no-cache-dir -r /requirements.txt

COPY scripts/entrypoint.sh /entrypoint.sh
COPY scripts/monitor.py    /monitor.py
RUN chmod +x /entrypoint.sh /monitor.py

CMD ["/entrypoint.sh"]

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    iproute2 \
    iputils-ping \
    net-tools \
    nmap \
    traceroute \
    tcpdump \
    curl \
    docker.io \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip3 install --no-cache-dir -r /requirements.txt

COPY scripts/entrypoint.sh /entrypoint.sh
COPY scripts/monitor.py    /monitor.py
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]

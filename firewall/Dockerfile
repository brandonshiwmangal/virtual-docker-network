FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    iproute2 \
    iputils-ping \
    net-tools \
    tcpdump \
    iptables \
    iptables-persistent \
    isc-dhcp-server \
    curl \
    lsb-release \
    gnupg \
    vlan \
    bridge-utils \
    kmod \
    && rm -rf /var/lib/apt/lists/*

# Install FRR from official repository
RUN curl -s https://deb.frrouting.org/frr/keys.gpg \
        | tee /usr/share/keyrings/frrouting.gpg > /dev/null \
    && echo "deb [signed-by=/usr/share/keyrings/frrouting.gpg] \
        https://deb.frrouting.org/frr $(lsb_release -s -c) frr-stable" \
        | tee /etc/apt/sources.list.d/frr.list \
    && apt-get update \
    && apt-get install -y frr frr-pythontools \
    && rm -rf /var/lib/apt/lists/*

# FRR configuration
COPY config/frr/daemons      /etc/frr/daemons
COPY config/frr/frr.conf     /etc/frr/frr.conf
COPY config/frr/vtysh.conf   /etc/frr/vtysh.conf
RUN chown -R frr:frr /etc/frr/ && chmod 640 /etc/frr/frr.conf

# DHCP configuration
COPY config/dhcpd.conf /etc/dhcp/dhcpd.conf

# Entrypoint
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]

"""Network-level security policy for outbound asset downloads.

Checks that resolved IP addresses are not in private or loopback ranges
before allowing a download to proceed.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Sequence

logger = logging.getLogger(__name__)

# Private and special-purpose networks that must never be contacted.
PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),       # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),      # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),      # link-local
    ipaddress.ip_network("0.0.0.0/8"),           # current network
    ipaddress.ip_network("100.64.0.0/10"),       # CGNAT
    ipaddress.ip_network("198.18.0.0/15"),       # benchmark
    ipaddress.ip_network("::1/128"),             # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),            # IPv6 unique-local
    ipaddress.ip_network("fd00::/8"),            # IPv6 unique-local
]


class NetworkPolicyError(ValueError):
    """Raised when a host resolves to a forbidden IP."""

    def __init__(self, hostname: str, ip: str):
        self.hostname = hostname
        self.ip = ip
        super().__init__(f"host resolves to private IP: {hostname} -> {ip}")


def resolve_and_check(hostname: str) -> list[str]:
    """Resolve *hostname* and verify all addresses are public.

    Returns:
        List of resolved IP address strings.

    Raises:
        NetworkPolicyError: If any resolved IP is in a private range.
    """
    try:
        info = socket.getaddrinfo(hostname, 443)
    except socket.gaierror as e:
        raise NetworkPolicyError(hostname, f"dns resolution failed: {e}") from e

    seen: set[str] = set()
    addrs: list[str] = []

    for family, _type, _proto, _name, sockaddr in info:
        ip = sockaddr[0]
        if ip in seen:
            continue
        seen.add(ip)

        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            raise NetworkPolicyError(hostname, f"invalid address: {ip}")

        for net in PRIVATE_NETWORKS:
            if addr in net:
                raise NetworkPolicyError(hostname, ip)

        addrs.append(ip)

    return addrs


def check_redirect_ip(hostname: str) -> list[str]:
    """Alias for resolve_and_check, for redirect targets."""
    return resolve_and_check(hostname)

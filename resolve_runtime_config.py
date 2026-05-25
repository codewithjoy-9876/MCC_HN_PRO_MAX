#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shutil
import socket
import sys
from pathlib import Path

SERVER_RE = re.compile(r'Server\s*=\s*\{\s*Host\s*=\s*"([^"]+)"\s*,\s*Port\s*=\s*(\d+)\s*\}')
SECTION_RE = re.compile(r'^\[(.+?)\]\s*$')


def resolve_ip(host: str, port: int) -> str | None:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception:
        return None
    ipv4 = []
    other = []
    for info in infos:
        ip = info[4][0]
        if ':' in ip:
            other.append(ip)
        else:
            ipv4.append(ip)
    for ip in ipv4 + other:
        if ip:
            return ip
    return None


def main() -> int:
    template_path = Path(os.environ.get('MCC_TEMPLATE_CONFIG', 'MCC_HN_PRO_MAX.ini')).resolve()
    runtime_path = Path(os.environ.get('MCC_RUNTIME_CONFIG', 'MCC_HN_PRO_MAX.runtime.ini')).resolve()

    if not template_path.exists():
        print(f'[resolver] template missing: {template_path}', file=sys.stderr)
        return 2

    lines = template_path.read_text(encoding='utf-8').splitlines()
    in_main_general = False
    found = False
    original_host = None
    original_port = None

    for idx, line in enumerate(lines):
        section_match = SECTION_RE.match(line.strip())
        if section_match:
            in_main_general = section_match.group(1).strip() == 'Main.General'
            continue
        if not in_main_general:
            continue
        match = SERVER_RE.search(line)
        if not match:
            continue
        original_host = match.group(1)
        original_port = int(match.group(2))
        resolved_ip = resolve_ip(original_host, original_port)
        target_host = resolved_ip or original_host
        replacement = f'Server = {{ Host = "{target_host}", Port = {original_port} }} # Runtime target; template host={original_host}'
        lines[idx] = replacement
        found = True
        status = 'resolved' if resolved_ip else 'fallback-template-host'
        print(f'[resolver] {status} template_host={original_host} target_host={target_host} port={original_port}')
        break

    if not found:
        shutil.copyfile(template_path, runtime_path)
        print(f'[resolver] server line not found in {template_path.name}; copied template unchanged')
        return 0

    runtime_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

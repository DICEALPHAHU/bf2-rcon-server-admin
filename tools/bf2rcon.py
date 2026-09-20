#!/usr/bin/env python3
"""
bf2rcon.py -- minimal Battlefield 2 RCON client.

WHY THIS EXISTS
    The live probe commands (pmtest etc.) had to be typed into the in-game
    console, which means waiting for a human to join the server. But the BF2
    admin module also listens on TCP 4711, so the same commands can be issued
    from outside the game -- which makes it possible to run probes
    autonomously.

PROTOCOL (from the stock Admin/default.py in the BF2 dedicated server)
    On connect the server sends:
        ### Battlefield 2 default RCON/admin ready.
        ### Digest seed: <16 random chars>
        <blank>
    An in-game client authenticates with the plaintext password. A TCP client
    must instead send  md5(seed + password)  as hex.

Usage:
    python bf2rcon.py <command> [command ...]
    python bf2rcon.py --stdin          # read commands from stdin, one per line
    python bf2rcon.py --raw <command>  # do not strip the end-of-message marker

Options:
    --host HOST   (default 127.0.0.1)
    --port PORT   (default 4711)
    --pass PASS   (default deepseek)
"""

import argparse
import hashlib
import re
import socket
import sys
import time

EOM = b'\x04'


class RconError(Exception):
    pass


def connect(host, port, password, timeout=8.0):
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)

    # read the greeting (ends with a blank line)
    buf = b''
    deadline = time.time() + timeout
    while b'\n\n' not in buf and time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk

    m = re.search(rb'Digest seed:\s*(\S+)', buf)
    if not m:
        s.close()
        raise RconError('no digest seed in greeting; got: %r' % buf[:200])
    seed = m.group(1).decode('ascii', 'replace')

    digest = hashlib.md5((seed + password).encode('ascii', 'replace')).hexdigest()
    # IMPORTANT: the TCP client must send "login <digest>", not the bare digest.
    # AdminServer.onRemoteCommand splits the line and only skips the
    # authentication check when the sub-command is literally 'login'; a bare
    # digest becomes the sub-command itself and is rejected as unknown.
    s.sendall(b'login ' + digest.encode('ascii') + b'\n')

    resp = s.recv(4096)
    if b'Authentication successful' not in resp:
        s.close()
        raise RconError('authentication failed: %r' % resp[:200])

    return s


def send(sock, command, timeout=8.0):
    """Send one command. Prefix with 0x02 to ask for an end-of-message marker.

    default.py: 'To get end-of-message markers (0x04 hex) after each reply,
    begin your commands with an ascii 0x02 code.'
    """
    sock.sendall(b'\x02' + command.encode('utf-8', 'replace') + b'\n')

    buf = b''
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        if buf.endswith(EOM) or EOM in buf:
            break
    if buf.endswith(EOM):
        buf = buf[:-1]
    return buf.decode('utf-8', 'replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('commands', nargs='*')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=4711)
    ap.add_argument('--pass', dest='password', default='deepseek')
    ap.add_argument('--timeout', type=float, default=8.0)
    ap.add_argument('--stdin', action='store_true')
    ap.add_argument('--raw', action='store_true')
    args = ap.parse_args()

    try:
        sock = connect(args.host, args.port, args.password, args.timeout)
    except Exception as e:
        print('CONNECT FAILED: %s' % e, file=sys.stderr)
        return 2

    rc = 0
    try:
        if args.stdin:
            for line in sys.stdin:
                line = line.rstrip('\r\n')
                if not line:
                    continue
                if line.startswith('#'):
                    continue
                out = send(sock, line, args.timeout)
                print('=== %s' % line)
                print(out if args.raw else out.strip())
                sys.stdout.flush()
        else:
            if not args.commands:
                print('no command given', file=sys.stderr)
                return 2
            for cmd in args.commands:
                out = send(sock, cmd, args.timeout)
                print('=== %s' % cmd)
                print(out if args.raw else out.strip())
                sys.stdout.flush()
    except Exception as e:
        print('ERROR: %s' % e, file=sys.stderr)
        rc = 1
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return rc


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""
bf2http.py -- HTTP bridge between a chat bot and the BF2 server's RCON.

ARCHITECTURE
    QQ group -> bot -> HTTP -> [this bridge] -> RCON :4711 -> BF2 server

WHY A SEPARATE PROCESS
    The BF2 server embeds CPython 2.3.4 with a crippled `os` module and no
    usable networking. It must not be asked to serve HTTP. This bridge is
    ordinary Python 3 on the same machine, where it can reach both the bot and
    the RCON port.

SECURITY
    The RCON port gives full control of the game server, so this bridge is
    deliberately conservative:

    * binds to 127.0.0.1 by default (use --host 0.0.0.0 only if you must)
    * a generic "run any rcon command" endpoint exists but is DISABLED unless
      --allow-raw is passed on the command line
    * if --token is given, every request must carry it; without --token the
      bridge refuses to bind to anything other than loopback

ENDPOINTS
    GET /status                 server + map + population summary
    GET /players                player list
    GET /map                    map name / mode / size
    GET /player?name=X          details and coordinates of one player
    GET /healthz                liveness probe (no RCON traffic)
    POST /cmd                   { "cmd": "pmmap" }   named pmadmin command
                                only allow-listed commands are accepted
    GET /raw?cmd=...            arbitrary rcon command; needs --allow-raw

    Every response is JSON. ?token=... or header X-Token: ...

USAGE
    python bf2http.py                                   # localhost:8099, read-only
    python bf2http.py --token SECRET                    # require a token
    python bf2http.py --allow-raw --token SECRET        # also allow /raw
    python bf2http.py --port 8099 --rcon-pass deepseek
"""

import argparse
import hashlib
import json
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

EOM = b'\x04'

# pmadmin commands a bot is allowed to invoke by name. Deliberately excludes
# pmban/pmkick/pmkill: destructive actions should be explicitly enabled, not
# reachable by guessing a name.
SAFE_COMMANDS = {
    'pmmap': 'map information',
    'pmplayers': 'player list',
    'pmpos': 'player details (needs arg=name)',
    'pmbanlist': 'ban list',
    'pmcheck': 'self check',
}

# destructive ones, only reachable when --allow-dangerous is set
DANGEROUS_COMMANDS = {
    'pmkick': 'kick (needs arg=name)',
    'pmban': 'ban (needs arg=name)',
    'pmkill': 'kill (needs arg=name)',
    'pmveh': 'spawn vehicle (needs arg=name)',
}


class RconError(Exception):
    pass


class RconClient:
    """One persistent RCON connection, serialised with a lock.

    Reconnecting per request would be slower and would hammer the game server's
    single-threaded admin socket.
    """

    def __init__(self, host, port, password, timeout=8.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sock = None
        self.lock = threading.Lock()

    def _connect(self):
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        buf = b''
        deadline = time.time() + self.timeout
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
            raise RconError('no digest seed in greeting')
        seed = m.group(1).decode('ascii', 'replace')
        digest = hashlib.md5((seed + self.password).encode('ascii', 'replace')).hexdigest()
        # the TCP client must send "login <digest>", not a bare digest
        s.sendall(b'login ' + digest.encode('ascii') + b'\n')
        resp = s.recv(4096)
        if b'Authentication successful' not in resp:
            s.close()
            raise RconError('authentication failed')
        self.sock = s

    def send(self, command):
        with self.lock:
            if self.sock is None:
                self._connect()
            try:
                self.sock.sendall(b'\x02' + command.encode('utf-8', 'replace') + b'\n')
                buf = b''
                deadline = time.time() + self.timeout
                while time.time() < deadline:
                    try:
                        chunk = self.sock.recv(65536)
                    except socket.timeout:
                        break
                    if not chunk:
                        raise RconError('server closed the connection')
                    buf += chunk
                    if EOM in buf:
                        break
            except RconError:
                self.sock = None
                raise
            except Exception as e:
                self.sock = None
                raise RconError(str(e))
            if buf.endswith(EOM):
                buf = buf[:-1]
            return buf.decode('utf-8', 'replace').strip()

    def close(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None


# ---------------------------------------------------------------------------
# parsing helpers -- the pmadmin commands emit human-readable text; turn the
# parts a bot cares about into structured data.
# ---------------------------------------------------------------------------

def parse_kv(text):
    """Parse '  key : value' lines into a dict."""
    out = {}
    for line in text.split('\n'):
        if ':' not in line:
            continue
        k, _, v = line.partition(':')
        k = k.strip()
        v = v.strip()
        if k and v:
            out[k] = v
    return out


def parse_players(text):
    """Parse the pmplayers table into a list of dicts."""
    players = []
    for line in text.split('\n'):
        s = line.strip()
        # rows look like: 0      2     SomeName
        m = re.match(r'^(\d+)\s+(\d+)\s+(.+)$', s)
        if m:
            players.append({
                'index': int(m.group(1)),
                'team': int(m.group(2)),
                'name': m.group(3).strip(),
            })
    return players


def summarise(rcon_client):
    """Build the payload used by /status."""
    map_text = rcon_client.send('pmmap')
    info = parse_kv(map_text)
    players_text = rcon_client.send('pmplayers')
    players = parse_players(players_text)
    return {
        'map': info.get('name'),
        'world_size': info.get('world_size'),
        'game_mode': info.get('game_mode'),
        'max_players': info.get('max_players'),
        'server_name': info.get('server_name'),
        'player_count': len(players),
        'players': players,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = 'bf2http/1.0'

    # injected by the server factory
    rcon_client = None
    token = None
    allow_raw = False
    allow_dangerous = False

    def log_message(self, fmt, *args):
        sys.stderr.write('[bf2http] %s - %s\n' % (self.address_string(), fmt % args))

    # -- helpers ----------------------------------------------------------

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _auth_ok(self, qs, headers):
        if not self.token:
            return True
        supplied = None
        if 'token' in qs and qs['token']:
            supplied = qs['token'][0]
        if not supplied:
            supplied = headers.get('X-Token')
        return supplied == self.token

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        qs = parse_qs(parsed.query)
        headers = {k: v for k, v in self.headers.items()}

        if not self._auth_ok(qs, headers):
            self._json(401, {'ok': False, 'error': 'unauthorized'})
            return

        try:
            if path == '/healthz':
                self._json(200, {'ok': True, 'service': 'bf2http'})
                return

            if path == '/status':
                self._json(200, {'ok': True, 'data': summarise(self.rcon_client)})
                return

            if path == '/players':
                text = self.rcon_client.send('pmplayers')
                self._json(200, {'ok': True, 'data': {
                    'players': parse_players(text),
                    'raw': text,
                }})
                return

            if path == '/map':
                text = self.rcon_client.send('pmmap')
                self._json(200, {'ok': True, 'data': parse_kv(text), 'raw': text})
                return

            if path == '/player':
                name = (qs.get('name') or [''])[0]
                if not name:
                    self._json(400, {'ok': False, 'error': 'name is required'})
                    return
                text = self.rcon_client.send('pmpos ' + name)
                self._json(200, {'ok': True, 'data': parse_kv(text), 'raw': text})
                return

            if path == '/cmd' and method == 'POST':
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b'{}'
                try:
                    body = json.loads(raw.decode('utf-8'))
                except Exception:
                    self._json(400, {'ok': False, 'error': 'body must be JSON'})
                    return
                cmd = (body.get('cmd') or '').strip()
                arg = (body.get('arg') or '').strip()
                if not cmd:
                    self._json(400, {'ok': False, 'error': 'cmd is required'})
                    return
                allowed = dict(SAFE_COMMANDS)
                if self.allow_dangerous:
                    allowed.update(DANGEROUS_COMMANDS)
                if cmd not in allowed:
                    self._json(403, {
                        'ok': False,
                        'error': 'command not allowed',
                        'allowed': sorted(allowed.keys()),
                    })
                    return
                full = cmd if not arg else (cmd + ' ' + arg)
                text = self.rcon_client.send(full)
                self._json(200, {'ok': True, 'cmd': full, 'raw': text})
                return

            if path == '/raw':
                if not self.allow_raw:
                    self._json(403, {
                        'ok': False,
                        'error': 'raw command execution is disabled; start the bridge with --allow-raw',
                    })
                    return
                cmd = (qs.get('cmd') or [''])[0]
                if not cmd:
                    self._json(400, {'ok': False, 'error': 'cmd is required'})
                    return
                text = self.rcon_client.send(cmd)
                self._json(200, {'ok': True, 'cmd': cmd, 'raw': text})
                return

            self._json(404, {
                'ok': False,
                'error': 'unknown endpoint',
                'endpoints': ['/status', '/players', '/map', '/player?name=',
                              '/healthz', 'POST /cmd', '/raw?cmd='],
            })

        except RconError as e:
            self._json(502, {'ok': False, 'error': 'rcon failure: %s' % e})
        except Exception as e:
            self._json(500, {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)})

    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8099)
    ap.add_argument('--rcon-host', default='127.0.0.1')
    ap.add_argument('--rcon-port', type=int, default=4711)
    ap.add_argument('--rcon-pass', dest='rcon_password', default='deepseek')
    ap.add_argument('--token', default=None,
                    help='require this token on every request')
    ap.add_argument('--allow-raw', action='store_true',
                    help='enable /raw (arbitrary rcon command)')
    ap.add_argument('--allow-dangerous', action='store_true',
                    help='allow pmkick / pmban / pmkill / pmveh through /cmd')
    args = ap.parse_args()

    if args.host not in ('127.0.0.1', 'localhost', '::1') and not args.token:
        print('REFUSING: binding to %s without --token would expose full server '
              'control to the network.' % args.host, file=sys.stderr)
        return 2

    Handler.rcon_client = RconClient(args.rcon_host, args.rcon_port, args.rcon_password)
    Handler.token = args.token
    Handler.allow_raw = args.allow_raw
    Handler.allow_dangerous = args.allow_dangerous

    # verify the RCON side works before accepting traffic
    try:
        Handler.rcon_client.send('pmmap')
        print('rcon: connected to %s:%s' % (args.rcon_host, args.rcon_port))
    except Exception as e:
        print('rcon: WARNING cannot reach %s:%s (%s)'
              % (args.rcon_host, args.rcon_port, e), file=sys.stderr)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print('listening on http://%s:%d' % (args.host, args.port))
    print('  endpoints : /status /players /map /player?name=X /healthz POST /cmd')
    print('  raw       : %s' % ('ENABLED' if args.allow_raw else 'disabled'))
    print('  dangerous : %s' % ('ENABLED' if args.allow_dangerous else 'disabled'))
    print('  token     : %s' % ('required' if args.token else 'NOT REQUIRED'))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nshutting down')
    finally:
        Handler.rcon_client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

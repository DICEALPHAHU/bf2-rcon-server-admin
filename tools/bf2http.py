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
    GET /bans                   banned IPs and CD-keys
    GET /events                 drain the event queue (join/leave/killed/round)
    GET /events?mode=peek|clear same queue without draining / drop it
    GET /events/wait?timeout=25 long-poll: hold until an event arrives or timeout
    GET /join?name=X            the answer to "is X on the server right now?"
    POST /leave?name=X          replace the answer to /join for X
    GET /healthz                liveness probe (no RCON traffic)
    POST /cmd                   { "cmd": "pmmap" }   named pmadmin command
                                only allow-listed commands are accepted
    GET /raw?cmd=...            arbitrary rcon command; needs --allow-raw

    Every response is JSON. ?token=... or header X-Token: ...

WHY /join AND /leave EXIST
    The BF2 event queue only reports player joins. There is no PlayerDisconnect
    payload carrying a name we can trust after the player object is gone, and
    polling the player list is a poor substitute at 5-second intervals (a player
    who joins and quits inside one interval is never seen). So each successful
    /join is remembered in-process; /leave answers "who was on last time we
    looked, minus who is on now" and updates the memory. A bot polls /join to
    learn about arrivals and /leave to learn about departures.

USAGE
    python bf2http.py                                   # localhost:8099, read-only
    python bf2http.py --token SECRET                    # require a token
    python bf2http.py --allow-raw --token SECRET        # also allow /raw
    python bf2http.py --port 8099 --rcon-pass deepseek
"""

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

EOM = b'\x04'

# pmadmin/bf2admin commands a bot is allowed to invoke by name. Deliberately
# excludes kick/ban/kill: destructive actions should be explicitly enabled, not
# reachable by guessing a name.
SAFE_COMMANDS = {
    'bf2player': 'player list with cdkey and ip',
    'bf2nowmap': 'map / mode / size',
    'bf2banlist': 'ban lists',
    'bf2banrecord': 'machine readable ban records (name / ip / key / minutes / reason)',
    'bf2check': 'self check',
    'bf2events': 'drain the event queue',
    'bf2events peek': 'read the event queue without draining it',
    'bf2events status': 'event queue counters',
    'bf2events chat off': 'stop queueing chat lines',
    'bf2events chat on': 'start queueing chat lines',
    'pmmap': 'map information (legacy)',
    'pmplayers': 'player list (legacy)',
    'pmpos': 'player details (needs arg=name)',
}

# destructive ones, only reachable when --allow-dangerous is set
DANGEROUS_COMMANDS = {
    'bf2kick': 'kick (needs arg=name)',
    'bf2ban': 'ban, needs arg="name minutes reason"',
    'bf2unban': 'unban (needs arg=name)',
    'pmkill': 'kill (needs arg=name)',
    'pmveh': 'spawn vehicle (needs arg=name) -- KNOWN TO CRASH THE SERVER',
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
    """Parse '  key : value' lines into a dict.

    pmpos/pmveh print their fields as '  soldier_pos   -> 790.55/163.07/27.14',
    i.e. with an arrow rather than a colon, so both separators are accepted.
    """
    out = {}
    for line in text.split('\n'):
        s = line.strip()
        if not s or s.startswith('==='):
            continue
        if '->' in s:
            k, _, v = s.partition('->')
        elif ':' in s:
            k, _, v = s.partition(':')
        else:
            continue
        k = k.strip()
        v = v.strip()
        if k and v:
            out[k] = v
    return out


def parse_bf2players(text):
    """Parse the bf2player table.

    Verified output shape:
        id | Playername | CDKey | IP
        ----------------------------------------
        0 | defaultPlayer | 0cecca... | 192.168.43.51
    """
    players = []
    for line in text.split('\n'):
        s = line.strip()
        if not s or s.startswith('-') or s.startswith('id |'):
            continue
        parts = [p.strip() for p in s.split('|')]
        if len(parts) < 4:
            continue
        if not parts[0].isdigit():
            continue
        players.append({
            'index': int(parts[0]),
            'name': parts[1],
            'key': parts[2],
            'ip': parts[3],
        })
    return players


def count_online(text):
    """How many players bf2player reported."""
    return len(parse_bf2players(text))


def parse_v3(text):
    """Parse a BF2 vector string 'x/y/z' into floats.

    The server prints positions as slash separated triples for both players and
    vehicles, e.g. '812.3/31.0/-1032.7'. Anything else (including the
    '(none: in free camera, not spawned)' placeholder) yields None.
    """
    if text is None:
        return None
    parts = str(text).strip().split('/')
    if len(parts) != 3:
        return None
    values = []
    for part in parts:
        try:
            values.append(float(part.strip()))
        except ValueError:
            return None
    return {'x': values[0], 'y': values[1], 'z': values[2]}


def parse_bans(text):
    """Parse the ban lists into structured records.

    listBannedAddresses -> 'IP: 1.2.3.4 Time Left: 1234'
    listBannedKeys      -> 'Key: <hash> Time Left: 1234'
    Either list may be empty, and unbanning rewrites these lines, so nothing is
    assumed about ordering.
    """
    ips, keys = [], []
    for line in text.split('\n'):
        s = line.strip()
        if not s:
            continue
        entry = {'value': '', 'time_left': 0, 'line': s}
        if s.startswith('IP:'):
            rest = s[3:].strip()
            entry['value'] = rest.split(' ')[0]
            ips.append(entry)
        elif s.startswith('Key:') or s.lower().startswith('key'):
            rest = s[4:].strip()
            entry['value'] = rest.split(' ')[0]
            keys.append(entry)
        else:
            continue
        m = re.search(r'Time Left:\s*(\d+)', s)
        if m:
            entry['time_left'] = int(m.group(1))
    return {'ips': ips, 'keys': keys}


def parse_bf2bans(text):
    """Parse the records printed by the `bf2banrecord` command.

    Fixed shape, one line per ban, with a count banner first:
        bf2banrecord 2
        defaultplayer | 192.168.43.51 | 0cecca... | 30 | test_ban_reason
    """
    records = []
    for line in text.split('\n'):
        s = line.rstrip()
        if '|' not in s:
            continue
        parts = [p.strip() for p in s.split('|')]
        if len(parts) < 4:
            continue
        # skip the banner and anything that is not a record
        if parts[0].lower().startswith('bf2banrecord'):
            continue
        records.append({
            'name': parts[0],
            'ip': parts[1],
            'key': parts[2],
            'minutes': parts[3],
            'reason': parts[4] if len(parts) > 4 else '',
            'line': s,
        })
    return records


def parse_nowmap(text):
    """Parse bf2nowmap output: 'Dalian_Plant | gpm_cq | 64'."""
    for line in text.split('\n'):
        s = line.strip()
        if not s or '|' not in s:
            continue
        parts = [p.strip() for p in s.split('|')]
        if len(parts) >= 3:
            return {'map': parts[0], 'game_mode': parts[1], 'size': parts[2]}
    return {}


def parse_events(text):
    """Parse bf2events output into structured events.

    Lines are tab separated: <seq> <epoch> <kind> <payload...>
    The first line is a count such as "3 event(s)", which is skipped.
    """
    events = []
    for line in text.split('\n'):
        raw = line.rstrip()
        if not raw or '\t' not in raw:
            continue
        parts = raw.split('\t')
        if len(parts) < 3:
            continue
        if not parts[0].strip().isdigit():
            continue
        body = parts[3:]
        ev = {
            'seq': int(parts[0]),
            'time': int(parts[1]) if parts[1].strip().isdigit() else 0,
            'kind': parts[2],
            'fields': body,
            'line': raw,
        }
        events.append(ev)
    return events


def parse_players(text):
    """Parse the old pmplayers table into a list of dicts (kept for /status)."""
    players = []
    for line in text.split('\n'):
        s = line.strip()
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
    info = parse_nowmap(rcon_client.send('bf2nowmap'))
    players = parse_bf2players(rcon_client.send('bf2player'))
    return {
        'map': info.get('map'),
        'game_mode': info.get('game_mode'),
        'size': info.get('size'),
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
    # last roster seen by /join, used by /leave to work out who went away
    online = {}
    online_lock = threading.Lock()

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
                text = self.rcon_client.send('bf2player')
                self._json(200, {'ok': True, 'data': {
                    'players': parse_bf2players(text),
                    'raw': text,
                }})
                return

            if path == '/map':
                text = self.rcon_client.send('bf2nowmap')
                self._json(200, {'ok': True, 'data': parse_nowmap(text), 'raw': text})
                return

            if path == '/events':
                # Drain (default) or peek the bf2events queue. Exists so a chat
                # bot does not need --allow-raw merely to collect events.
                mode = (qs.get('mode') or ['drain'])[0].lower()
                if mode == 'peek':
                    cmd = 'bf2events peek'
                elif mode == 'clear':
                    mode = 'clear'
                    cmd = 'bf2events clear'
                else:
                    mode = 'drain'
                    cmd = 'bf2events'
                text = self.rcon_client.send(cmd)
                self._json(200, {
                    'ok': True,
                    'mode': mode,
                    'events': parse_events(text),
                    'raw': text,
                })
                return

            if path == '/player':
                name = (qs.get('name') or [''])[0]
                if not name:
                    self._json(400, {'ok': False, 'error': 'name is required'})
                    return
                text = self.rcon_client.send('pmpos ' + name)
                details = parse_kv(text)
                soldier = parse_v3(details.get('soldier_pos'))
                vehicle = parse_v3(details.get('vehicle_pos'))
                alive = str(details.get('alive', '')).strip() in ('1', 'True', 'true')
                self._json(200, {'ok': True, 'data': {
                    'name': name,
                    'found': 'soldier_pos' in details or 'alive' in details,
                    'online': alive,
                    # spawn state: 'in free camera' means joined but not spawned
                    'spawned': soldier is not None,
                    'soldier_pos': soldier,
                    'vehicle_pos': vehicle,
                    'vehicle_tpl': details.get('vehicle_tpl', ''),
                    'address': details.get('address', ''),
                    'team': details.get('team', ''),
                    'details': details,
                }, 'raw': text})
                return

            if path == '/bans':
                ips = self.rcon_client.send('admin.listBannedAddresses')
                keys = self.rcon_client.send('admin.listBannedKeys')
                records_text = self.rcon_client.send('bf2banrecord')
                self._json(200, {'ok': True, 'data': {
                    'addresses': parse_bans(ips)['ips'],
                    'keys': parse_bans(keys)['keys'],
                    'records': parse_bf2bans(records_text),
                    'total': len(parse_bans(ips)['ips']) + len(parse_bans(keys)['keys']),
                }, 'raw': {'addresses': ips, 'keys': keys, 'records': records_text}})
                return

            if path == '/events/wait':
                # Long poll. RCON is a single serialised socket, so holding it is
                # a real cost: keep the default modest and let the caller decide.
                try:
                    timeout = float((qs.get('timeout') or ['25'])[0])
                except ValueError:
                    timeout = 25.0
                timeout = max(1.0, min(timeout, 110.0))
                poll = 0.6
                deadline = time.time() + timeout
                events = []
                while True:
                    text = self.rcon_client.send('bf2events')
                    events = parse_events(text)
                    if events or time.time() >= deadline:
                        break
                    time.sleep(poll)
                self._json(200, {
                    'ok': True,
                    'waited': round(max(0.0, timeout - (deadline - time.time())), 1),
                    'events': events,
                    'raw': '',
                })
                return

            if path in ('/join', '/leave'):
                # /join remembers the current roster and reports arrivals since the
                # last call; /leave reports departures by diffing against that
                # memory. See the module docstring for why both exist.
                text = self.rcon_client.send('bf2player')
                current = {}
                for player in parse_bf2players(text):
                    if player['name']:
                        current[player['name'].lower()] = player
                with Handler.online_lock:
                    known = dict(Handler.online)
                    Handler.online = current
                if path == '/join':
                    changed = [current[k] for k in current if k not in known]
                else:
                    changed = [known[k] for k in known if k not in current]
                self._json(200, {
                    'ok': True,
                    'data': changed,
                    'count': len(changed),
                    'online': count_online(text),
                    'players': list(current.values()),
                })
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
                              '/bans', '/healthz', '/events', '/events/wait?timeout=',
                              '/join', '/leave', 'POST /cmd', '/raw?cmd='],
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
    print('  endpoints : /status /players /map /player?name=X /bans /healthz')
    print('              /events /events/wait?timeout=25 /join /leave POST /cmd')
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

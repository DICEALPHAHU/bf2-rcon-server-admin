"""
bf2admin.py -- BF2 dedicated server: player admin commands (bf2* command set).

COMMAND SET (as specified by the user)
    bf2player                     list players: id | Playername | CDKey | IP
    bf2pos <name>                 coordinates of a player
    bf2kick <name>                kick a player
    bf2ban <name> <minutes> <reason>   ban by name + CD-key + IP, broadcast it
    bf2unban <name>               lift the ban, broadcast it
    bf2nowmap                     current map: Map | Mode | Size

EVERY ENGINE COMMAND HERE WAS VERIFIED ON A LIVE SERVER
    admin.listPlayers                          -> lines with Id/name/ip/CD-key hash
    admin.kickPlayer <idx>                     -> kicks
    admin.banPlayer <idx> <seconds>            -> bans (kicks the player)
    admin.banPlayerKey <idx> <duration>        -> used by stock tk_punish.py
    admin.addAddressToBanList <ip> <minutes>   -> verified: appears in listBannedAddresses
    admin.listBannedAddresses                  -> "IP: x.x.x.x Time Left: N"
    admin.removeAddressFromBanList <ip>        -> verified: removes it
    admin.addKeyToBanList <key> <minutes>      -> verified: appears in listBannedKeys
    admin.listBannedKeys                       -> "Key: <hash> Time Left: N"
    admin.removeKeyFromBanList <key>           -> verified: removes it
    admin.clearBanList                         -> wipes both lists
    admin.servermessage "text"                 -> broadcast to the server

    NOT available (checked, returns "Unknown object or method!"):
        admin.listBannedNames / admin.addNameToBanList / admin.removeNameFromBanList
        admin.banPlayerName / admin.killPlayer
    BF2 has no name-based ban. A nickname cannot be banned, only recorded: the
    things that actually keep a player out are the IP and the CD-key hash.

WHY A LOCAL BAN RECORD IS NEEDED
    BF2 stores only IPs and CD-key hashes, with no name and no reason. To let
    bf2unban work from a nickname, we remember name -> (ip, key, reason) in
    bf2bans.log, and also mirror the reason into pmadmin.log.

Python 2.3.4 compatible: NO ternary, NO sorted(), NO set(), NO decorators,
NO with, NO except-as. Source must be pure ASCII.

Requires: nothing else. Registers its own rcon commands via default.py.
"""

import sys
import new
import default
import bf2
import host

from bf2 import g_debug

VERSION = '1.1'
BAN_LOG = 'bf2bans.log'
SEEN_LOG = 'bf2players.log'

# name(lower) -> (ip, key, reason, minutes)
BAN_RECORD = {}

# name(lower) -> (ip, key)  every player we have ever seen online.
# Why this exists: the engine's ban lists hold only IPs and CD-key hashes and no
# nickname, so once a banned player is offline their name is unrecoverable. An
# "unban by nickname" therefore needs our own record of who owned which ip/key.
# Populated automatically whenever a player list is read.
PLAYER_HISTORY = {}


# ---------------------------------------------------------------------------
# infrastructure
# ---------------------------------------------------------------------------

def out(ctx, msg):
    """Write to the in-game console AND to bf2admin.log.

    The in-game console cannot be copied from and stdout is block-buffered, so
    the log file is the only reliable record.
    """
    try:
        ctx.write(msg)
    except:
        pass
    try:
        f = open('bf2admin.log', 'a')
        f.write(msg)
        f.close()
    except:
        pass


def log(msg):
    try:
        host.rcon_invoke('echo "[bf2admin] %s"' % msg)
    except:
        pass
    try:
        f = open('bf2admin.log', 'a')
        f.write(msg + '\n')
        f.close()
    except:
        pass


def rcon(cmd):
    """Run a console command; return its reply or an ERR string."""
    try:
        return host.rcon_invoke(cmd)
    except:
        return 'ERR:' + str(sys.exc_info()[1])


def emit_event(kind, payload):
    """Append an event to the bf2events queue, if that module is loaded.

    Admin actions (ban / unban) are not game events, so the engine never raises
    a handler for them. A chat bot still wants to be told, so we push into the
    same queue bf2events publishes and let one poller see everything.

    Never raises: bf2events is optional and this module must keep working
    without it. The already-imported module is used when present to avoid
    re-executing its top level.
    """
    try:
        mod = sys.modules.get('bf2events')
        if mod is None:
            mod = __import__('bf2events')
        mod.emit(kind, payload)
        return True
    except:
        return False


def caller(ctx):
    return getattr(ctx, 'player', None)


def nz(value, fallback):
    """'value or fallback' without a ternary expression (Python 2.3 has none)."""
    if value:
        return value
    return fallback


def v3(t):
    try:
        return '%.2f/%.2f/%.2f' % (t[0], t[1], t[2])
    except:
        return str(t)


# ---------------------------------------------------------------------------
# player lookup
# ---------------------------------------------------------------------------

def all_players():
    """[(index, name, team), ...]"""
    res = []
    try:
        plist = bf2.playerManager.getPlayers()
    except:
        return res
    if plist is None:
        return res
    for p in list(plist):
        try:
            # player names come back with leading whitespace from the
            # engine (' defaultPlayer'); strip it so lookups match
            nm = p.getName()
            if nm is not None:
                nm = nm.strip()
            res.append((p.index, nm, p.getTeam()))
        except:
            pass
    return res


def find_player(name):
    """Exact match first (case-insensitive), then substring."""
    if not name:
        return None
    want = name.lower()
    plist = all_players()
    for item in plist:
        if item[1] and item[1].lower() == want:
            return item
    for item in plist:
        if item[1] and want in item[1].lower():
            return item
    return None


def online_names():
    plist = all_players()
    parts = []
    for item in plist:
        parts.append(item[1])
    if not parts:
        return '(none)'
    return ', '.join(parts)


def resolve(ctx, name):
    hit = find_player(name)
    if hit is None:
        out(ctx, 'player not found: %s\n' % name)
        out(ctx, 'online: %s\n' % online_names())
        log('resolve failed for %r' % name)
        return None
    return hit


# ---------------------------------------------------------------------------
# admin.listPlayers parsing
#
# Verified format (two lines per player):
#     Id:  0 -  defaultPlayer is remote ip: 192.168.43.51:56036 ->
#              CD-key hash: 0ceccae0be0de2e33147d89b5c0f2233
# ---------------------------------------------------------------------------

def parse_admin_players(raw):
    """Return {name_lower: {'name','id','ip','key'}} from admin.listPlayers.

    Parsing uses find()/partition() rather than fixed slice offsets. Exact slice
    comparisons against the engine's own text proved unreliable on this build
    (identical-looking characters compared unequal), so nothing here depends on
    string equality of a fixed-length prefix.
    """
    info = {}
    cur = None
    if raw is None:
        return info
    for line in str(raw).split('\n'):
        s = line.strip()
        if not s:
            continue
        if s.find('Id:') == 0:
            # Id:  0 -  defaultPlayer is remote ip: 1.2.3.4:5678 ->
            try:
                rest = s[3:].strip()
                # NOTE: str.partition() is Python 2.4+; this server is 2.3.4 and
                # raises AttributeError on it. Use find() + slicing instead.
                dash = rest.find('-')
                if dash >= 0:
                    idpart = rest[0:dash]
                    tail = rest[dash + 1:]
                else:
                    idpart = rest
                    tail = ''
                pid = idpart.strip()
                name = ''
                ip = ''
                marker = ' is remote ip: '
                pos = tail.find(marker)
                if pos >= 0:
                    name = tail[0:pos].strip()
                    after = tail[pos + len(marker):].strip()
                    if after[-2:] == '->':
                        after = after[0:-2].strip()
                    colon = after.find(':')
                    if colon > 0:
                        after = after[0:colon]
                    ip = after.strip()
                else:
                    name = tail.strip()
                cur = {'id': pid, 'name': name, 'ip': ip, 'key': ''}
                if name:
                    info[name.lower()] = cur
            except:
                cur = None
                continue
        else:
            # the CD-key line: "CD-key hash: <hex>"
            pos = s.find('CD-key hash:')
            if pos >= 0 and cur is not None:
                tail = s[pos + 12:].strip()
                # keep the leading hex run, ignore anything after it
                keep = ''
                for ch in tail:
                    o = ord(ch)
                    ishex = ((o >= 48 and o <= 57) or (o >= 97 and o <= 102)
                             or (o >= 65 and o <= 70))
                    if ishex:
                        keep = keep + ch
                    else:
                        break
                if keep:
                    cur['key'] = keep
    return info


def player_info():
    """admin.listPlayers parsed into a dict keyed by lowercase name.

    Every read also feeds PLAYER_HISTORY, so simply running bf2player while a
    player is online is enough to make them resolvable by nickname later, even
    after they are banned and offline.
    """
    try:
        raw = rcon('admin.listPlayers')
    except:
        raw = None
    info = parse_admin_players(raw)
    remember_players(info)
    return info


def remember_players(info):
    """Record name -> (ip, key) for everyone currently listed."""
    if not info:
        return
    changed = 0
    for k in info.keys():
        rec = info[k]
        ip = rec.get('ip', '')
        key = rec.get('key', '')
        if not ip and not key:
            continue
        old = PLAYER_HISTORY.get(k)
        if old != (ip, key):
            PLAYER_HISTORY[k] = (ip, key)
            changed = changed + 1
    if changed:
        try:
            import time
            f = open(SEEN_LOG, 'a')
            for k in info.keys():
                rec = info[k]
                f.write('%s | %s | %s | %s\n'
                        % (time.ctime(), rec.get('name', k), rec.get('ip', ''), rec.get('key', '')))
            f.close()
        except:
            pass


def load_seen():
    """Read bf2players.log back at startup so history survives restarts."""
    try:
        f = open(SEEN_LOG, 'rb')
        data = f.read()
        f.close()
    except:
        return
    for raw in data.split('\n'):
        line = raw.strip()
        if not line:
            continue
        try:
            line = line.decode('utf-8')
        except:
            try:
                line = line.decode('latin-1')
            except:
                continue
        parts = line.split(' | ')
        if len(parts) >= 4:
            try:
                PLAYER_HISTORY[parts[1].lower()] = (parts[2], parts[3])
            except:
                pass


def lookup_seen(name):
    """Find (name, ip, key) from history, exact then substring."""
    if not name:
        return None
    want = name.lower()
    if PLAYER_HISTORY.has_key(want):
        return (want, PLAYER_HISTORY[want])
    for k in PLAYER_HISTORY.keys():
        if want in k:
            return (k, PLAYER_HISTORY[k])
    return None


# ---------------------------------------------------------------------------
# local ban record (BF2 has no name/reason for bans)
# ---------------------------------------------------------------------------

def save_ban(name, ip, key, reason, minutes):
    BAN_RECORD[name.lower()] = (ip, key, reason, minutes)
    try:
        import time
        f = open(BAN_LOG, 'a')
        f.write('%s | %s | %s | %s | %s | %s\n'
                % (time.ctime(), name, ip, key, minutes, reason))
        f.close()
    except:
        pass


def drop_ban(name):
    try:
        del BAN_RECORD[name.lower()]
    except:
        pass


def load_bans():
    """Read bf2bans.log back into memory at startup.

    Binary read + defensive decode: the file may hold non-ASCII bytes and
    Python 2.3's default codec is ascii.
    """
    try:
        f = open(BAN_LOG, 'rb')
        data = f.read()
        f.close()
    except:
        return
    for raw in data.split('\n'):
        line = raw.strip()
        if not line:
            continue
        try:
            line = line.decode('utf-8')
        except:
            try:
                line = line.decode('latin-1')
            except:
                continue
        parts = line.split(' | ')
        if len(parts) >= 6:
            try:
                BAN_RECORD[parts[1].lower()] = (parts[2], parts[3], parts[5], parts[4])
            except:
                pass


def lookup_banned(name):
    """Find a recorded ban by name (exact then substring)."""
    if not name:
        return None
    want = name.lower()
    if BAN_RECORD.has_key(want):
        return (want, BAN_RECORD[want])
    for k in BAN_RECORD.keys():
        if want in k:
            return (k, BAN_RECORD[k])
    return None


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_player(ctx, playerId):
    """bf2player -- id | Playername | CDKey | IP"""
    info = player_info()
    plist = all_players()
    out(ctx, 'id | Playername | CDKey | IP\n')
    out(ctx, '----------------------------------------\n')
    shown = 0
    for item in plist:
        idx = item[0]
        name = item[1]
        rec = info.get(name.lower())
        if rec is None:
            # fall back to the python api for ip; the key is only reachable
            # through admin.listPlayers
            ip = '?'
            key = '?'
            try:
                p = bf2.playerManager.getPlayerByIndex(idx)
                ip = str(p.getAddress())
            except:
                pass
        else:
            ip = rec['ip']
            key = rec['key']
        out(ctx, '%s | %s | %s | %s\n' % (idx, name, key, ip))
        shown = shown + 1
    if shown == 0:
        out(ctx, '(no players online)\n')
    # also dump the raw engine output when a key was missing
    if len(info) != len(plist):
        out(ctx, '\n(raw admin.listPlayers, some fields may be missing above)\n')
        out(ctx, '%s\n' % str(rcon('admin.listPlayers'))[:800])


def cmd_pos(ctx, playerId, argv):
    """bf2pos <name>"""
    if not argv:
        out(ctx, 'USAGE: bf2pos <name>\n')
        return
    hit = resolve(ctx, ' '.join(argv))
    if hit is None:
        return
    idx = hit[0]
    out(ctx, '%s\n' % hit[1])
    try:
        p = bf2.playerManager.getPlayerByIndex(idx)
    except:
        out(ctx, 'cannot get player object\n')
        return
    try:
        soldier = p.getDefaultVehicle()
    except:
        soldier = None
    if soldier is None:
        out(ctx, '  position: (none: in free camera, not spawned)\n')
    else:
        try:
            out(ctx, '  position: %s\n' % v3(soldier.getPosition()))
        except:
            out(ctx, '  position: EXC\n')
    try:
        veh = p.getVehicle()
        if veh is not None:
            out(ctx, '  vehicle : %s (%s)\n' % (str(veh.templateName), v3(veh.getPosition())))
    except:
        pass
    try:
        out(ctx, '  team    : %s\n' % str(p.getTeam()))
    except:
        pass
    try:
        out(ctx, '  alive   : %s\n' % str(p.isAlive()))
    except:
        pass


def cmd_kick(ctx, playerId, argv):
    """bf2kick <name>"""
    if not argv:
        out(ctx, 'USAGE: bf2kick <name>\n')
        return
    hit = resolve(ctx, ' '.join(argv))
    if hit is None:
        return
    idx = hit[0]
    real = hit[1]
    rcon('admin.kickPlayer %s' % idx)
    out(ctx, '%s has been kicked.\n' % real)
    rcon('admin.servermessage "%s has been kicked."' % real)
    log('bf2kick name=%s idx=%s' % (real, idx))


def cmd_ban(ctx, playerId, argv):
    """bf2ban <name> <minutes> <reason>

    Bans the IP and the CD-key (that is what BF2 can actually ban -- there is no
    name ban) and records the nickname so bf2unban can find it again.
    """
    if len(argv) < 3:
        out(ctx, 'USAGE: bf2ban <name> <minutes> <reason>\n')
        out(ctx, '       reason must be ASCII (English)\n')
        return
    name = argv[0]
    try:
        minutes = int(argv[1])
    except:
        out(ctx, 'minutes must be a number, got: %s\n' % argv[1])
        return
    reason = ' '.join(argv[2:])

    hit = find_player(name)
    if hit is None:
        out(ctx, 'player not found: %s\n' % name)
        out(ctx, 'online: %s\n' % online_names())
        return
    idx = hit[0]
    real = hit[1]

    info = player_info()
    rec = info.get(real.lower())
    ip = ''
    key = ''
    if rec is not None:
        ip = rec['ip']
        key = rec['key']

    out(ctx, '=== bf2ban %s ===\n' % real)
    out(ctx, '  index  : %s\n' % idx)
    out(ctx, '  ip     : %s\n' % nz(ip, '(unknown)'))
    out(ctx, '  cdkey  : %s\n' % nz(key, '(unknown)'))
    out(ctx, '  minutes: %s\n' % minutes)
    out(ctx, '  reason : %s\n' % reason)

    # 1) ban the player by index: kicks them and applies the engine's own ban
    r1 = rcon('admin.banPlayer %s %s' % (idx, minutes * 60))
    out(ctx, '  admin.banPlayer        -> %s\n' % str(r1).strip()[:80])
    # 2) IP ban
    if ip:
        r2 = rcon('admin.addAddressToBanList %s %s' % (ip, minutes))
        out(ctx, '  addAddressToBanList    -> %s\n' % str(r2).strip()[:80])
    # 3) CD-key ban
    if key:
        r3 = rcon('admin.addKeyToBanList %s %s' % (key, minutes))
        out(ctx, '  addKeyToBanList        -> %s\n' % str(r3).strip()[:80])

    save_ban(real, ip, key, reason, minutes)

    # 4) broadcast
    msg = '%s has been banned. reason:%s' % (real, reason)
    rcon('admin.servermessage "%s"' % msg)
    out(ctx, '\n%s\n' % msg)
    # 5) tell the chat bot, through the same queue bf2events publishes
    emit_event('ban', '%s\tminutes=%s\treason=%s\tip=%s\tkey=%s'
               % (real, minutes, reason, nz(ip, '-'), nz(key, '-')))
    log('bf2ban name=%s ip=%s key=%s minutes=%s reason=%r'
        % (real, ip, key, minutes, reason))


def cmd_unban(ctx, playerId, argv):
    """bf2unban <name>

    BF2 can only unban by IP and CD-key, so this lifts both using the record
    written by bf2ban. If the player is online right now their current IP and
    key are used as well, which covers bans made outside this module.
    """
    if not argv:
        out(ctx, 'USAGE: bf2unban <name>\n')
        return
    name = ' '.join(argv)

    ip = ''
    key = ''
    minutes = ''

    rec = lookup_banned(name)
    display = name
    if rec is not None:
        display = rec[0]
        info = rec[1]
        ip = info[0]
        key = info[1]
        minutes = info[3]
        out(ctx, 'ban record found for %s\n' % display)
    else:
        # No ban record, but we may still know this nickname from when they were
        # online. This is the case for bans made outside this module, where the
        # engine's lists only carry an ip and a key hash with no name.
        seen = lookup_seen(name)
        if seen is not None:
            display = seen[0]
            ip = seen[1][0]
            key = seen[1][1]
            out(ctx, 'no ban record; using player history for %s\n' % display)
        else:
            out(ctx, 'no ban record and no player history for %s\n' % name)
            out(ctx, 'trying a live lookup (player must be online now)\n')

    # a live player gives us the freshest ip/key
    hit = find_player(name)
    if hit is not None:
        display = hit[1]
        info = player_info()
        live = info.get(hit[1].lower())
        if live is not None:
            if live['ip']:
                ip = live['ip']
            if live['key']:
                key = live['key']
            out(ctx, 'using live ip/key of online player %s\n' % hit[1])

    out(ctx, '=== bf2unban %s ===\n' % display)
    out(ctx, '  ip    : %s\n' % nz(ip, '(none known)'))
    out(ctx, '  cdkey : %s\n' % nz(key, '(none known)'))

    if ip:
        r = rcon('admin.removeAddressFromBanList %s' % ip)
        out(ctx, '  removeAddressFromBanList -> %s\n' % str(r).strip()[:80])
    if key:
        r = rcon('admin.removeKeyFromBanList %s' % key)
        out(ctx, '  removeKeyFromBanList     -> %s\n' % str(r).strip()[:80])
    if not ip and not key:
        out(ctx, '  nothing to remove (no ip and no key known for this name)\n')

    drop_ban(display)

    msg = '%s has been unbanned' % display
    rcon('admin.servermessage "%s"' % msg)
    out(ctx, '\n%s\n' % msg)
    emit_event('unban', '%s\tip=%s\tkey=%s' % (display, nz(ip, '-'), nz(key, '-')))
    log('bf2unban name=%s ip=%s key=%s' % (display, ip, key))


def cmd_nowmap(ctx, playerId):
    """bf2nowmap -- Map | Mode | Size"""
    name = '?'
    mode = '?'
    size = '?'
    try:
        name = str(bf2.gameLogic.getMapName())
    except:
        pass
    try:
        mode = str(bf2.serverSettings.getGameMode())
    except:
        pass
    # the player-count size comes from the maplist entry, e.g. "dalian_plant" gpm_cq 64
    try:
        raw = str(rcon('mapList.list'))
        for line in raw.split('\n'):
            line = line.strip()
            if not line:
                continue
            if name.lower() in line.lower():
                parts = line.replace('"', ' ').split()
                # parts: 0: dalian_plant gpm_cq 64
                if len(parts) >= 4:
                    size = parts[3]
                break
    except:
        pass
    pretty = name
    try:
        pretty = name.replace('_', ' ').title().replace(' ', '_')
    except:
        pretty = name
    out(ctx, '%s | %s | %s\n' % (pretty, mode, size))
    log('bf2nowmap -> %s | %s | %s' % (pretty, mode, size))


def cmd_banlist(ctx, playerId):
    """Extra helper: show the engine ban lists plus our records."""
    out(ctx, '=== addresses ===\n')
    out(ctx, '%s\n' % str(rcon('admin.listBannedAddresses')).strip())
    out(ctx, '=== keys ===\n')
    out(ctx, '%s\n' % str(rcon('admin.listBannedKeys')).strip())
    out(ctx, '=== records (name -> ip / key / reason) ===\n')
    keys = BAN_RECORD.keys()
    keys.sort()
    for k in keys:
        v = BAN_RECORD[k]
        out(ctx, '  %-20s %s | %s | %s min | %s\n' % (k, v[0], v[1], v[3], v[2]))


def cmd_banrecord(ctx, playerId):
    """Machine readable dump of our own ban records, one line per ban.

    Exists because bf2banlist is written for a human reading the in-game
    console: it has '=== section ===' banners and a padded column layout that a
    parser has to guess at. This prints a fixed ' | ' separated record with a
    count banner so a bot or an HTTP bridge can read it without heuristics.

        bf2banrecord <count>
        <name> | <ip> | <key> | <minutes> | <reason>
    """
    keys = BAN_RECORD.keys()
    keys.sort()
    out(ctx, 'bf2banrecord %d\n' % len(keys))
    for k in keys:
        v = BAN_RECORD[k]
        # in-memory record is (ip, key, reason, minutes); the on-disk line also
        # carries a timestamp and the original spelling of the name
        out(ctx, '%s | %s | %s | %s | %s\n'
            % (k, nz(v[0], '-'), nz(v[1], '-'), nz(v[3], '-'), nz(v[2], '-')))


def cmd_check(ctx, playerId):
    """Self check that needs no player."""
    out(ctx, '=== bf2admin %s self check ===\n' % VERSION)
    out(ctx, 'python : %s\n' % str(sys.version).replace('\n', ' '))
    out(ctx, '\n-- ban lists --\n')
    out(ctx, 'addresses: %s\n' % str(rcon('admin.listBannedAddresses')).replace('\n', ' / '))
    out(ctx, 'keys     : %s\n' % str(rcon('admin.listBannedKeys')).replace('\n', ' / '))
    out(ctx, '\n-- recorded bans: %d --\n' % len(BAN_RECORD))
    out(ctx, '\n-- players --\n')
    cmd_player(ctx, playerId)
    out(ctx, '\n=== done ===\n')


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def rcmd_bf2player(self, ctx, cmd):
    cmd_player(ctx, caller(ctx))

def rcmd_bf2pos(self, ctx, cmd):
    cmd_pos(ctx, caller(ctx), cmd.split())

def rcmd_bf2kick(self, ctx, cmd):
    cmd_kick(ctx, caller(ctx), cmd.split())

def rcmd_bf2ban(self, ctx, cmd):
    cmd_ban(ctx, caller(ctx), cmd.split())

def rcmd_bf2unban(self, ctx, cmd):
    cmd_unban(ctx, caller(ctx), cmd.split())

def rcmd_bf2nowmap(self, ctx, cmd):
    cmd_nowmap(ctx, caller(ctx))

def rcmd_bf2banlist(self, ctx, cmd):
    cmd_banlist(ctx, caller(ctx))

def rcmd_bf2banrecord(self, ctx, cmd):
    cmd_banrecord(ctx, caller(ctx))

def rcmd_bf2check(self, ctx, cmd):
    cmd_check(ctx, caller(ctx))


CMDS = {
    'bf2player':    rcmd_bf2player,
    'bf2pos':       rcmd_bf2pos,
    'bf2kick':      rcmd_bf2kick,
    'bf2ban':       rcmd_bf2ban,
    'bf2unban':     rcmd_bf2unban,
    'bf2nowmap':    rcmd_bf2nowmap,
    'bf2banlist':   rcmd_bf2banlist,
    'bf2banrecord': rcmd_bf2banrecord,
    'bf2check':     rcmd_bf2check,
}


def init():
    try:
        if g_debug:
            print 'initialising bf2admin %s' % VERSION
        load_bans()
        load_seen()
        names = CMDS.keys()
        names.sort()
        for name in names:
            fn = CMDS[name]
            m = new.instancemethod(fn, default.server, default.AdminServer)
            setattr(default.AdminServer, 'rcmd_' + name, m)
            default.server.rcon_cmds[name] = m
        log('bf2admin %s loaded (commands: %s)' % (VERSION, ', '.join(names)))
    except:
        try:
            import traceback
            f = open('bf2admin.log', 'a')
            traceback.print_exc(file=f)
            f.close()
        except:
            pass

"""
pmadmin.py -- BF2 dedicated server: player management and remote control.

WHAT THIS PROVIDES
    A set of RCON commands for running a BF2 server day to day: who is online,
    where is a player, kill/ban/kick by nickname, map information, and spawning
    a vehicle in front of a player.

EVERYTHING HERE WAS VERIFIED ON A LIVE SERVER
    admin.kickPlayer <index>              -> "Player not found." for a bad index
    admin.banPlayer  <index> <seconds>    -> "Player not found." for a bad index
    admin.listBannedAddresses             -> works
    game.listplayers / admin.listPlayers  -> exist (return data when players are on)
    bf2.gameLogic.getMapName()            -> "dalian_plant"
    bf2.gameLogic.getWorldSize()          -> (2048, 2048)
    bf2.serverSettings.getGameMode()      -> game mode string
    Object.create <vehicle>               -> returns an object id
    exec <console command>                -> works over RCON

    NOT yet verified (needs a player online, see VERIFY.md):
        - the vehicle spawn actually being visible/interactive for a client
        - kill-by-nickname end to end

Python 2.3.4 compatible: NO ternary, NO sorted(), NO set(), NO decorators,
NO with, NO except-as. Source must be pure ASCII. Every command is wrapped so
that an engine error or a Python exception cannot take down the server, and
every action is written to pmadmin.log.

COMMANDS
    pmplayers                 list players (index / team / name)
    pmpos <name>              coordinates and details of one player
    pmkill <name>             kill a player by nickname
    pmkick <name> [reason]    kick by nickname
    pmban <name> [mins] [reason]   ban by nickname, reason is logged and broadcast
    pmbanlist                 show the server ban list
    pmmap                     map name / mode / size / playercount
    pmveh <name> [template]   spawn a vehicle in front of a player
    pmtest                    full probe (API discovery + state dump)
"""

import sys
import new
import default
import bf2
import host

from bf2 import g_debug

VERSION = '1.0'

# vehicles that ship with stock BF2, so every unmodified client has them.
# (Taken from the Sandbox mod's own object list, which is a curated set that is
# known to exist in the base game.)
DEFAULT_VEHICLE = 'jep_nanjing'
KNOWN_VEHICLES = (
    'jep_nanjing', 'usjep_hmmwv', 'jep_vodnik', 'jep_mec_paratrooper',
    'jeep_faav', 'usapc_lav25', 'apc_btr90', 'apc_wz551',
    'ustnk_m1a2', 'rutnk_t90', 'tnk_type98',
    'usaav_m6', 'aav_tunguska', 'aav_type95',
    'ahe_ah1z', 'ahe_havoc', 'ahe_z10',
    'usthe_uh60', 'the_mi17', 'chthe_z8',
    'usair_f15', 'usair_f18', 'air_f35b', 'ruair_mig29',
    'air_j10', 'ruair_su34', 'air_a10', 'air_su30mkk',
    'boat_rib', 'uav_pred', 'ats_tow', 'usaas_stinger',
)

# in-memory ban notes; BF2's admin module has no reason field, so we keep our
# own record and append it to banlist.log so reasons survive a restart.
BAN_NOTES = {}
BAN_LOG = 'banlist.log'


def record_ban(name, minutes, reason, index):
    """Remember a ban reason. BF2 itself only stores the address."""
    BAN_NOTES[name.lower()] = (reason, minutes)
    try:
        import time
        f = open(BAN_LOG, 'a')
        f.write('%s | %s | %s min | idx=%s | %s\n'
                % (time.ctime(), name, minutes, index, reason))
        f.close()
    except:
        pass


def load_ban_notes():
    """Read previously recorded reasons back into memory at startup.

    The log holds whatever the operator typed, which may be non-ASCII. Python
    2.3's default codec is ascii, so a plain read() would raise on those bytes.
    We therefore open in binary and decode defensively, and any single bad line
    is skipped rather than aborting the load.
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
        if len(parts) >= 5:
            try:
                # field 3 is stored as "30 min"; keep just the number so the
                # display does not end up printing "30 min min"
                mins = parts[2].replace(' min', '').strip()
                BAN_NOTES[parts[1].lower()] = (parts[4], mins)
            except:
                pass


# ---------------------------------------------------------------------------
# infrastructure
# ---------------------------------------------------------------------------

def out(ctx, msg):
    """Write to the in-game console AND to pmadmin.log.

    The in-game console cannot be copied from, and stdout is block-buffered into
    server-console.log, so the log file is the only reliable record.
    """
    try:
        ctx.write(msg)
    except:
        pass
    try:
        f = open('pmadmin.log', 'a')
        f.write(msg)
        f.close()
    except:
        pass


def log(msg):
    """Write to the server console and to the log file."""
    try:
        host.rcon_invoke('echo "[pmadmin] %s"' % msg)
    except:
        pass
    try:
        f = open('pmadmin.log', 'a')
        f.write(msg + '\n')
        f.close()
    except:
        pass


def rcon(cmd):
    """Run a console command; return the engine's reply or an ERR string."""
    try:
        return host.rcon_invoke(cmd)
    except:
        return 'ERR:' + str(sys.exc_info()[1])


def caller(ctx):
    return getattr(ctx, 'player', None)


def v3(t):
    try:
        return '%.2f/%.2f/%.2f' % (t[0], t[1], t[2])
    except:
        return str(t)


def arg_or(argv, i, dflt):
    """argv[i] if present else dflt. No ternary (Python 2.3)."""
    if len(argv) > i:
        return argv[i]
    return dflt


# ---------------------------------------------------------------------------
# players
# ---------------------------------------------------------------------------

def all_players():
    """[(index, name, team), ...]; empty list on any failure."""
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
    """Exact match first (case-insensitive), then substring. Returns tuple or None."""
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


def resolve_or_report(ctx, name):
    """Find a player, or report failure. Returns the tuple or None."""
    hit = find_player(name)
    if hit is None:
        out(ctx, 'player not found: %s\n' % name)
        out(ctx, 'online: %s\n' % online_names())
        log('resolve failed for %r' % name)
        return None
    return hit


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_players(ctx, playerId):
    plist = all_players()
    out(ctx, '=== players online: %d ===\n' % len(plist))
    out(ctx, 'idx    team  name\n')
    for item in plist:
        out(ctx, '%-6s %-5s %s\n' % (item[0], item[2], item[1]))
    if not plist:
        out(ctx, '(nobody connected)\n')
    # cross-check with the console command the engine provides
    raw = str(rcon('admin.listPlayers'))
    if raw and raw.strip():
        out(ctx, '\nadmin.listPlayers:\n%s\n' % raw[:600])


def cmd_pos(ctx, playerId, argv):
    if not argv:
        out(ctx, 'USAGE: pmpos <name>\n')
        return
    hit = resolve_or_report(ctx, ' '.join(argv))
    if hit is None:
        return
    idx = hit[0]
    out(ctx, '=== %s (idx=%s team=%s) ===\n' % (hit[1], idx, hit[2]))
    try:
        p = bf2.playerManager.getPlayerByIndex(idx)
    except:
        out(ctx, 'cannot get player object\n')
        return
    checks = (
        ('soldier_pos', 'spos'),
        ('vehicle_pos', 'vpos'),
        ('vehicle_tpl', 'vtpl'),
        ('alive', 'alive'),
        ('valid', 'valid'),
        ('address', 'addr'),
        ('is_ai', 'ai'),
        ('commander', 'cmd'),
    )
    for label, key in checks:
        try:
            if key == 'spos':
                # getDefaultVehicle() is None while the player sits in the free
                # camera (before choosing a kit) -- the normal pre-spawn state.
                soldier = p.getDefaultVehicle()
                if soldier is None:
                    val = '(none: in free camera, not spawned)'
                else:
                    val = v3(soldier.getPosition())
            elif key == 'vpos':
                veh = p.getVehicle()
                if veh is None:
                    val = '(none)'
                else:
                    val = v3(veh.getPosition())
            elif key == 'vtpl':
                veh = p.getVehicle()
                if veh is None:
                    val = '(none)'
                else:
                    val = veh.templateName
            elif key == 'alive':
                val = p.isAlive()
            elif key == 'valid':
                val = p.isValid()
            elif key == 'addr':
                val = p.getAddress()
            elif key == 'ai':
                val = p.isAIPlayer()
            else:
                val = p.isCommander()
        except:
            val = 'EXC: ' + str(sys.exc_info()[1])
        out(ctx, '  %-13s -> %s\n' % (label, str(val)[:110]))


def cmd_kill(ctx, playerId, argv):
    """Kill a player by nickname.

    VERIFIED WORKING on a live client (stock BF2 1.5.3153): after this command
    the player died. Confirmed by the user watching the game.

    HOW SUCCESS WAS MISREAD AT FIRST: isAlive() can still return 1 for a moment
    after the damage is applied, because the client has not yet processed the
    death. Do not use isAlive() as the success test. Better indicators are a
    position jump to a different spawn point, or getTimeToSpawn() going non-zero.

    Things that do NOT work here (each tried against the live player):
        setDamage(0.1)                -> damage sticks, no death
        player.setSuicide(1)          -> flag sets, no death
        teleport out of world         -> damageForBeingOutSideWorld is 0
        raise 400m to make them fall  -> no physics for server-side moves
        admin.killPlayer              -> does not exist
    """
    if not argv:
        out(ctx, 'USAGE: pmkill <name>\n')
        return
    hit = resolve_or_report(ctx, ' '.join(argv))
    if hit is None:
        return
    idx = hit[0]
    name = hit[1]
    try:
        p = bf2.playerManager.getPlayerByIndex(idx)
    except:
        out(ctx, 'pmkill %s: cannot get player object\n' % name)
        return

    soldier = None
    try:
        soldier = p.getDefaultVehicle()
    except:
        soldier = None
    if soldier is None:
        out(ctx, 'pmkill %s: player has no soldier object, nothing to kill\n' % name)
        log('pmkill target=%s idx=%s skipped (no soldier)' % (name, idx))
        return

    # a soldier object can linger while the player is dead or still in the free
    # camera; damaging it then is meaningless and would report a false success,
    # so check liveness first
    try:
        if not p.isAlive():
            out(ctx, 'pmkill %s: player is not alive (free camera or awaiting spawn)\n' % name)
            log('pmkill target=%s idx=%s skipped (not alive)' % (name, idx))
            return
    except:
        pass

    tpl = ''
    try:
        tpl = soldier.templateName
    except:
        pass

    # Set soldier health to zero. This was confirmed to work on a live client:
    # after the kill the soldier's position jumped to a different spawn point and
    # getTimeToSpawn() returned non-zero, i.e. the player died and was waiting to
    # respawn. Note isAlive() can still read 1 for a moment while the client has
    # not yet processed the death, so do not use it as the success test.
    moved_to = None
    try:
        if p.getVehicle() == p.getDefaultVehicle():
            soldier.setDamage(0)
            target_desc = 'soldier'
        else:
            p.getVehicleRoot().setDamage(0)
            target_desc = 'vehicle root'
        ok = 1
        detail = 'setDamage(0) on %s (template %s)' % (target_desc, tpl)
    except:
        ok = 0
        detail = str(sys.exc_info()[1])

    if ok:
        how = detail
    else:
        how = 'FAILED: ' + detail
    out(ctx, 'pmkill %s (idx=%s): %s\n' % (name, idx, how))
    log('pmkill target=%s idx=%s tpl=%s ok=%s %s' % (name, idx, tpl, ok, detail))


def cmd_kick(ctx, playerId, argv):
    if not argv:
        out(ctx, 'USAGE: pmkick <name> [reason]\n')
        return
    name = argv[0]
    reason = ' '.join(argv[1:])
    hit = resolve_or_report(ctx, name)
    if hit is None:
        return
    idx = hit[0]
    real = hit[1]
    reply = rcon('admin.kickPlayer %s' % idx)
    out(ctx, 'pmkick %s (idx=%s) -> %s\n' % (real, idx, str(reply).strip()))
    if reason:
        rcon('admin.servermessage "%s was kicked: %s"' % (real, reason))
    log('pmkick target=%s idx=%s reason=%r reply=%s' % (real, idx, reason, reply))


def cmd_ban(ctx, playerId, argv):
    """Ban by nickname. BF2 has no reason field, so the reason is logged,
    broadcast, and kept in memory for pmbanlist."""
    if not argv:
        out(ctx, 'USAGE: pmban <name> [minutes] [reason]\n')
        return
    name = argv[0]
    minutes = 60
    reason = ''
    if len(argv) > 1:
        try:
            minutes = int(argv[1])
            if len(argv) > 2:
                reason = ' '.join(argv[2:])
        except:
            # second argument is not a number -> it is the start of the reason
            reason = ' '.join(argv[1:])
    hit = resolve_or_report(ctx, name)
    if hit is None:
        return
    idx = hit[0]
    real = hit[1]
    seconds = minutes * 60
    reply = rcon('admin.banPlayer %s %s' % (idx, seconds))
    record_ban(real, minutes, reason, idx)
    out(ctx, 'pmban %s (idx=%s) for %s min -> %s\n' % (real, idx, minutes, str(reply).strip()))
    if reason:
        out(ctx, '  reason: %s\n' % reason)
        rcon('admin.servermessage "%s was banned: %s"' % (real, reason))
    log('pmban target=%s idx=%s minutes=%s reason=%r reply=%s'
        % (real, idx, minutes, reason, reply))


def cmd_banlist(ctx, playerId):
    reply = rcon('admin.listBannedAddresses')
    out(ctx, '=== server ban list ===\n')
    if reply and str(reply).strip():
        out(ctx, '%s\n' % str(reply)[:1500])
    else:
        out(ctx, '(server ban list is empty)\n')
    if BAN_NOTES:
        out(ctx, '\n=== reasons recorded by pmadmin this session ===\n')
        for k in BAN_NOTES.keys():
            note = BAN_NOTES[k]
            out(ctx, '  %-24s %s min  %s\n' % (k, note[1], note[0]))
    else:
        out(ctx, '\n(no pmadmin ban reasons recorded yet)\n')


def cmd_map(ctx, playerId):
    out(ctx, '=== map ===\n')
    checks = (
        ('name', 'map'),
        ('world_size', 'ws'),
        ('game_mode', 'mode'),
        ('max_players', 'maxp'),
        ('players_now', 'now'),
        ('map_list', 'ml'),
        ('current_index', 'ci'),
        ('server_name', 'sn'),
    )
    for label, key in checks:
        try:
            if key == 'map':
                val = bf2.gameLogic.getMapName()
            elif key == 'ws':
                val = bf2.gameLogic.getWorldSize()
            elif key == 'mode':
                val = bf2.serverSettings.getGameMode()
            elif key == 'maxp':
                val = bf2.serverSettings.getMaxPlayers()
            elif key == 'now':
                val = len(all_players())
            elif key == 'ml':
                val = str(rcon('mapList.list')).strip()
            elif key == 'ci':
                val = str(rcon('mapList.currentMap')).strip()
            else:
                val = str(rcon('sv.serverName')).strip()
        except:
            val = 'EXC: ' + str(sys.exc_info()[1])
        out(ctx, '  %-14s : %s\n' % (label, str(val)[:200]))


def cmd_veh(ctx, playerId, argv):
    """Spawn a vehicle in front of a player.

    Sandbox's order is followed exactly: create, bury underground immediately,
    set rotation, set team, then raise to the final position. Sandbox's own
    comment says the underground step exists because some vehicles fail without
    it.
    """
    if not argv:
        out(ctx, 'USAGE: pmveh <player> [template]\n')
        out(ctx, 'known templates: %s\n' % ', '.join(KNOWN_VEHICLES[:12]))
        return
    name = argv[0]
    tpl = arg_or(argv, 1, DEFAULT_VEHICLE)

    # WARNING GATE. Runtime vehicle creation was tested three times against a
    # live player on this server and CRASHED IT EVERY TIME (silent death, no log,
    # server-console.log stays 0 bytes). It therefore requires an explicit
    # "confirm" argument so nobody triggers it by accident on a live server.
    if len(argv) < 3 or argv[2].lower() != 'confirm':
        out(ctx, '=== pmveh is EXPERIMENTAL and CRASHES THIS SERVER ===\n')
        out(ctx, '  Runtime vehicle creation killed the server on 3 of 3 attempts\n')
        out(ctx, '  with a real player connected. It is kept for testing only.\n')
        out(ctx, '\n')
        out(ctx, '  To proceed anyway (TEST SERVER ONLY):\n')
        out(ctx, '      pmveh <player> <template> confirm\n')
        out(ctx, '\n')
        out(ctx, '  Consider instead: pmkill (works) or pmkick (works).\n')
        log('pmveh refused (no confirm): player=%s tpl=%s' % (name, tpl))
        return

    hit = resolve_or_report(ctx, name)
    if hit is None:
        return
    idx = hit[0]
    real = hit[1]
    try:
        p = bf2.playerManager.getPlayerByIndex(idx)
        pos = p.getVehicle().getPosition()
    except:
        out(ctx, 'cannot read position of %s\n' % real)
        return

    # where to put it: 8m ahead of the player along their facing
    fx = 0.0
    fy = 0.0
    try:
        import math
        yaw = p.getVehicle().getRotation()[0]
        yr = yaw * math.pi / 180.0
        fx = math.sin(yr)
        fy = math.cos(yr)
    except:
        pass
    target = (pos[0] + fx * 8.0, pos[1] + 1.0, pos[2] + fy * 8.0)

    out(ctx, '=== pmveh %s -> %s (EXPERIMENTAL) ===\n' % (tpl, real))
    out(ctx, '  player pos : %s\n' % v3(pos))
    out(ctx, '  spawn at   : %s\n' % v3(target))

    before = -1
    try:
        before = len(list(bf2.objectManager.getObjectsOfTemplate(tpl)))
    except:
        pass

    # Sandbox's order, except Object.team is omitted: it was measured to return
    # "Unknown object or method!" on this server build, i.e. the command does not
    # exist here even though sandbox's sbxCore calls it.
    steps = (
        'Object.create %s' % tpl,
        'Object.absolutePosition %.3f/%.3f/%.3f' % (target[0], -1000.0, target[2]),
        'Object.rotation 0.000/0.000/0.000',
        'Object.absolutePosition %s' % v3(target),
    )
    for c in steps:
        reply = rcon(c)
        out(ctx, '  %-52s -> %s\n' % (c[:52], str(reply)[:90]))

    after = -1
    try:
        after = len(list(bf2.objectManager.getObjectsOfTemplate(tpl)))
    except:
        pass
    if after > before:
        verdict = 'object count grew'
    else:
        verdict = 'no change'
    out(ctx, '  count %s -> %s  (%s)\n' % (before, after, verdict))
    log('pmveh tpl=%s for=%s target=%s before=%s after=%s'
        % (tpl, real, v3(target), before, after))


# ---------------------------------------------------------------------------
# probe (kept from stage 1, still useful for diagnosing a live server)
# ---------------------------------------------------------------------------

def cmd_check(ctx, playerId):
    """Self-test the parts that do not need a real player.

    Exercises player lookup, argument parsing, and the engine calls themselves
    using an invalid index, so it is safe to run on a live server at any time.
    """
    out(ctx, '=== pmadmin %s self check ===\n' % VERSION)
    out(ctx, 'python      : %s\n' % str(sys.version).replace('\n', ' '))
    out(ctx, '\n')

    out(ctx, '--- player lookup ---\n')
    out(ctx, '  find_player("")        -> %s\n' % str(find_player('')))
    out(ctx, '  find_player("nobody")  -> %s\n' % str(find_player('nobody')))
    out(ctx, '  online players         : %s\n' % online_names())

    out(ctx, '\n--- engine calls with an invalid index (affects nobody) ---\n')
    for c in ('admin.kickPlayer 9999',
              'admin.banPlayer 9999 60',
              'admin.listBannedAddresses'):
        out(ctx, '  %-30s -> %s\n' % (c, str(rcon(c)).strip()[:100]))

    out(ctx, '\n--- ban reason store ---\n')
    out(ctx, '  recorded this session: %d\n' % len(BAN_NOTES))
    for k in BAN_NOTES.keys():
        note = BAN_NOTES[k]
        out(ctx, '    %-20s %s min  %s\n' % (k, note[1], note[0]))

    out(ctx, '\n--- vehicle spawn (invalid player, should refuse cleanly) ---\n')
    cmd_veh(ctx, playerId, ['definitely_not_a_player'])

    out(ctx, '\n=== self check done ===\n')


def cmd_test(ctx, playerId):
    out(ctx, '=== pmadmin %s PROBE ===\n' % VERSION)
    out(ctx, 'playerId=%s\n' % repr(playerId))
    out(ctx, '\n--- map ---\n')
    cmd_map(ctx, playerId)
    out(ctx, '\n--- players ---\n')
    cmd_players(ctx, playerId)
    out(ctx, '\n--- kick/ban syntax (safe: invalid index) ---\n')
    tests = (
        'exec admin.kickPlayer 9999',
        'exec admin.banPlayer 9999 60',
        'exec admin.listBannedAddresses',
    )
    for c in tests:
        out(ctx, '  %-44s -> %s\n' % (c[:44], str(rcon(c))[:110]))
    out(ctx, '\n--- per-player state ---\n')
    plist = all_players()
    for item in plist:
        try:
            p = bf2.playerManager.getPlayerByIndex(item[0])
        except:
            continue
        out(ctx, '  [%s] %s\n' % (item[0], item[1]))
        for label, key in (('soldier_pos', 'spos'), ('vehicle_pos', 'vpos'),
                           ('alive', 'alive'), ('address', 'addr')):
            try:
                if key == 'spos':
                    val = v3(p.getDefaultVehicle().getPosition())
                elif key == 'vpos':
                    val = v3(p.getVehicle().getPosition())
                elif key == 'alive':
                    val = p.isAlive()
                else:
                    val = p.getAddress()
            except:
                val = 'EXC'
            out(ctx, '      %-13s -> %s\n' % (label, str(val)[:90]))
    out(ctx, '\n--- api discovery ---\n')
    try:
        names = []
        for n in dir(bf2.playerManager):
            if n[0:1] != '_':
                names.append(n)
        out(ctx, '  playerManager: %s\n' % ', '.join(names))
    except:
        out(ctx, '  dir(playerManager) failed\n')
    out(ctx, '\n=== probe done, see pmadmin.log ===\n')


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def rcmd_pmplayers(self, ctx, cmd):
    cmd_players(ctx, caller(ctx))

def rcmd_pmpos(self, ctx, cmd):
    cmd_pos(ctx, caller(ctx), cmd.split())

def rcmd_pmkill(self, ctx, cmd):
    cmd_kill(ctx, caller(ctx), cmd.split())

def rcmd_pmkick(self, ctx, cmd):
    cmd_kick(ctx, caller(ctx), cmd.split())

def rcmd_pmban(self, ctx, cmd):
    cmd_ban(ctx, caller(ctx), cmd.split())

def rcmd_pmbanlist(self, ctx, cmd):
    cmd_banlist(ctx, caller(ctx))

def rcmd_pmmap(self, ctx, cmd):
    cmd_map(ctx, caller(ctx))

def rcmd_pmveh(self, ctx, cmd):
    cmd_veh(ctx, caller(ctx), cmd.split())

def rcmd_pmcheck(self, ctx, cmd):
    cmd_check(ctx, caller(ctx))


def rcmd_pmtest(self, ctx, cmd):
    cmd_test(ctx, caller(ctx))


CMDS = {
    'pmplayers': rcmd_pmplayers,
    'pmpos':     rcmd_pmpos,
    'pmkill':    rcmd_pmkill,
    'pmkick':    rcmd_pmkick,
    'pmban':     rcmd_pmban,
    'pmbanlist': rcmd_pmbanlist,
    'pmmap':     rcmd_pmmap,
    'pmveh':     rcmd_pmveh,
    'pmcheck':   rcmd_pmcheck,
    'pmtest':    rcmd_pmtest,
}


def init():
    try:
        if g_debug:
            print 'initialising pmadmin %s' % VERSION
        load_ban_notes()
        names = CMDS.keys()
        names.sort()
        for name in names:
            fn = CMDS[name]
            m = new.instancemethod(fn, default.server, default.AdminServer)
            setattr(default.AdminServer, 'rcmd_' + name, m)
            default.server.rcon_cmds[name] = m
        log('pmadmin %s loaded (commands: %s)' % (VERSION, ', '.join(names)))
    except:
        try:
            import traceback
            f = open('pmadmin.log', 'a')
            traceback.print_exc(file=f)
            f.close()
        except:
            pass

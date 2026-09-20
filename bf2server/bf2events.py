"""
bf2events.py -- BF2 server event hooks, for pushing notifications to a chat bot.

WHAT THIS DOES
    Registers handlers for the events a server admin cares about, formats each
    into a single line of text, and appends it to an event queue that an outside
    process can read over RCON.

    PlayerConnect      player joined
    PlayerDisconnect   player left
    PlayerSpawn        player spawned
    PlayerKilled       player was killed (with attacker when known)
    ChatMessage        chat (opt-in; off by default because it is noisy)
    GameStatusChanged  round started / ended

WHY A QUEUE AND NOT A DIRECT PUSH
    The BF2 server embeds CPython 2.3.4 with a crippled `os` module and no usable
    networking, and it must not block. So it only appends lines to a queue here;
    an external process (bf2http.py) serves them over HTTP and the chat bot polls
    that. If the bot is down the queue simply accumulates, nothing blocks.

    Alternative: bind a UDP socket from inside the server and push datagrams.
    That is possible (the embedded python does have socket) but it would block or
    need a thread, and a crash in the hook would take the game server with it.
    A file-backed queue is the safe choice.

RCON COMMANDS
    bf2events                 drain and show pending events (bot-facing)
    bf2events peek            show pending events without draining
    bf2events clear           drop all pending events
    bf2events chat on|off     include chat messages in the queue
    bf2events file on|off     also append every event to bf2events.log

EVENT LINE FORMAT (tab separated, so a bot can split and format freely)
    <sequence>\t<epoch>\t<kind>\t<payload...>

Python 2.3.4 compatible: NO ternary, NO sorted(), NO set(), NO decorators,
NO with, NO except-as. Source must be pure ASCII.
"""

import sys
import new
import default
import bf2
import host

from bf2 import g_debug

VERSION = '1.0'
QUEUE_FILE = 'bf2events.queue'
LOG_FILE = 'bf2events.log'
MAX_QUEUE = 500          # keep the queue bounded so the file cannot grow forever

# events parked here until a bot drains them
QUEUE = []
_seq = [0]
_include_chat = [False]
_also_file = [True]


# ---------------------------------------------------------------------------
# infrastructure
# ---------------------------------------------------------------------------

def log(msg):
    try:
        host.rcon_invoke('echo "[bf2events] %s"' % msg)
    except:
        pass


def _ts():
    """Epoch seconds, or 0 if time is unavailable."""
    try:
        import time
        return int(time.time())
    except:
        return 0


def _append_file(line):
    if not _also_file[0]:
        return
    try:
        f = open(LOG_FILE, 'a')
        f.write(line + '\n')
        f.close()
    except:
        pass


def _rewrite_queue():
    """Persist the queue so events survive a server restart."""
    try:
        f = open(QUEUE_FILE, 'w')
        for line in QUEUE:
            f.write(line + '\n')
        f.close()
    except:
        pass


def emit(kind, payload):
    """Queue one event. Never raises."""
    try:
        _seq[0] = _seq[0] + 1
        line = '%d\t%d\t%s\t%s' % (_seq[0], _ts(), kind, payload)
        QUEUE.append(line)
        # bound the queue
        while len(QUEUE) > MAX_QUEUE:
            QUEUE.pop(0)
        _append_file(line)
        _rewrite_queue()
    except:
        pass


def load_queue():
    try:
        f = open(QUEUE_FILE, 'rb')
        data = f.read()
        f.close()
    except:
        return
    for raw in data.split('\n'):
        line = raw.strip()
        if not line:
            continue
        QUEUE.append(line)
        try:
            head = int(line.split('\t')[0])
            if head > _seq[0]:
                _seq[0] = head
        except:
            pass


# ---------------------------------------------------------------------------
# player helpers
# ---------------------------------------------------------------------------

def pname_by_index(idx):
    """Nickname for a player index, or a placeholder."""
    try:
        p = bf2.playerManager.getPlayerByIndex(idx)
        if p is None:
            return 'idx%s' % idx
        nm = p.getName()
        if nm is None:
            return 'idx%s' % idx
        return nm.strip()
    except:
        return 'idx%s' % idx


def online_count():
    try:
        return bf2.playerManager.getNumberOfPlayers()
    except:
        return -1


# ---------------------------------------------------------------------------
# event handlers
# ---------------------------------------------------------------------------

def onPlayerConnect(player):
    """PlayerConnect passes a Player object."""
    name = '?'
    try:
        name = player.getName().strip()
    except:
        pass
    emit('join', '%s\tplayers=%s' % (name, online_count()))
    log('join %s' % name)


def onPlayerDisconnect(player):
    name = '?'
    try:
        name = player.getName().strip()
    except:
        pass
    emit('leave', '%s\tplayers=%s' % (name, online_count()))
    log('leave %s' % name)


def onPlayerSpawn(player, soldier):
    name = '?'
    try:
        name = player.getName().strip()
    except:
        pass
    pos = ''
    try:
        pos = '%.1f/%.1f/%.1f' % soldier.getPosition()
    except:
        pass
    emit('spawn', '%s\t%s' % (name, pos))


def onPlayerKilled(victim, attacker, weapon, assists, obj):
    """Signature per stock tk_punish.py: (victim, attacker, weapon, assists, obj)."""
    vname = '?'
    aname = 'none'
    wname = '?'
    try:
        vname = victim.getName().strip()
    except:
        pass
    try:
        if attacker is not None:
            aname = attacker.getName().strip()
    except:
        pass
    try:
        wname = str(weapon)
    except:
        pass
    if aname == 'none' or not aname:
        emit('killed', '%s\tby=<world>\tweapon=%s' % (vname, wname))
    else:
        emit('killed', '%s\tby=%s\tweapon=%s' % (vname, aname, wname))


def onChatMessage(playerId, text, channel, flags):
    if not _include_chat[0]:
        return
    name = pname_by_index(playerId)
    try:
        emit('chat', '%s\tchannel=%s\ttext=%s' % (name, channel, text))
    except:
        pass


def onGameStatusChanged(status):
    label = str(status)
    try:
        if status == bf2.GameStatus.Playing:
            label = 'Playing'
        elif status == bf2.GameStatus.PreGame:
            label = 'PreGame'
        elif status == bf2.GameStatus.EndGame:
            label = 'EndGame'
    except:
        pass
    m = '?'
    try:
        m = str(bf2.gameLogic.getMapName())
    except:
        pass
    emit('round', '%s\tmap=%s' % (label, m))


def register_handlers():
    """Register everything, tolerating any that this build lacks."""
    want = (
        ('PlayerConnect', onPlayerConnect),
        ('PlayerDisconnect', onPlayerDisconnect),
        ('PlayerSpawn', onPlayerSpawn),
        ('PlayerKilled', onPlayerKilled),
        ('ChatMessage', onChatMessage),
    )
    ok = []
    for name, fn in want:
        try:
            host.registerHandler(name, fn, 1)
            ok.append(name)
        except:
            log('could not register %s: %s' % (name, str(sys.exc_info()[1])))
    try:
        host.registerGameStatusHandler(onGameStatusChanged)
        ok.append('GameStatusChanged')
    except:
        log('could not register GameStatusChanged')
    return ok


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def out(ctx, msg):
    try:
        ctx.write(msg)
    except:
        pass


def cmd_events(ctx, playerId, argv):
    """bf2events [peek|clear|chat on|off|file on|off|status]"""
    sub = ''
    if argv:
        sub = argv[0].lower()

    if sub == 'clear':
        n = len(QUEUE)
        del QUEUE[:]
        _rewrite_queue()
        out(ctx, 'cleared %d event(s)\n' % n)
        return

    if sub == 'status':
        out(ctx, 'bf2events %s\n' % VERSION)
        out(ctx, '  queued      : %d\n' % len(QUEUE))
        out(ctx, '  seq         : %d\n' % _seq[0])
        out(ctx, '  include chat: %s\n' % str(_include_chat[0]))
        out(ctx, '  log to file : %s\n' % str(_also_file[0]))
        out(ctx, '  players now : %s\n' % str(online_count()))
        return

    if sub == 'chat':
        if len(argv) > 1 and argv[1].lower() == 'on':
            _include_chat[0] = True
        else:
            _include_chat[0] = False
        out(ctx, 'include chat = %s\n' % str(_include_chat[0]))
        return

    if sub == 'file':
        if len(argv) > 1 and argv[1].lower() == 'on':
            _also_file[0] = True
        else:
            _also_file[0] = False
        out(ctx, 'log to file = %s\n' % str(_also_file[0]))
        return

    if sub == 'peek':
        out(ctx, '%d event(s) pending (not drained)\n' % len(QUEUE))
        for line in QUEUE:
            out(ctx, '%s\n' % line)
        return

    # default: drain
    if not QUEUE:
        out(ctx, '0 events\n')
        return
    out(ctx, '%d event(s)\n' % len(QUEUE))
    for line in QUEUE:
        out(ctx, '%s\n' % line)
    del QUEUE[:]
    _rewrite_queue()


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def rcmd_bf2events(self, ctx, cmd):
    cmd_events(ctx, getattr(ctx, 'player', None), cmd.split())


CMDS = {
    'bf2events': rcmd_bf2events,
}


def init():
    try:
        if g_debug:
            print 'initialising bf2events %s' % VERSION
        load_queue()
        names = CMDS.keys()
        names.sort()
        for name in names:
            fn = CMDS[name]
            m = new.instancemethod(fn, default.server, default.AdminServer)
            setattr(default.AdminServer, 'rcmd_' + name, m)
            default.server.rcon_cmds[name] = m
        registered = register_handlers()
        log('bf2events %s loaded (commands: %s) (handlers: %s)'
            % (VERSION, ', '.join(names), ', '.join(registered)))
    except:
        try:
            import traceback
            f = open('bf2events.log', 'a')
            traceback.print_exc(file=f)
            f.close()
        except:
            pass

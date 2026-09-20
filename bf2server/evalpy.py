"""
evalpy.py -- run arbitrary Python over RCON.

Adapted from EvalPy by dackz (bf2tech, archived), used here so that the server
side can be probed without a human having to join the game and type commands
into the in-game console.

Usage over RCON:
    eval <python statement>

Example:
    eval print len(bf2.playerManager.getPlayers())
    eval ctx.write(str(bf2.gameLogic.getMapName()))

Notes
    * Everything is wrapped: an exception is reported back instead of killing
      the server.
    * ctx.write() MUST be given a string. Passing anything else can drop the
      RCON connection, so CommandContext.write is patched to coerce with str().

Python 2.3 compatible. Pure ASCII.
"""

import sys
import new
import default
import bf2
import host

from bf2 import g_debug


def _coerce_write(self, text):
    return self.write_original(str(text))


def rcmd_eval(self, ctx, cmd):
    try:
        exec(compile(cmd, '<evalpy>', 'exec'))
    except:
        try:
            kind = str(sys.exc_info()[0])
            kind = kind.split('.')[-1]
            ctx.write(kind + ': ' + str(sys.exc_info()[1]) + '\n')
        except:
            pass


def init():
    try:
        if g_debug:
            print 'initialising evalpy'
        default.CommandContext.write_original = default.CommandContext.write
        m = new.instancemethod(_coerce_write, None, default.CommandContext)
        default.CommandContext.write = m
        m2 = new.instancemethod(rcmd_eval, default.server, default.AdminServer)
        default.AdminServer.rcmd_eval = m2
        default.server.rcon_cmds['eval'] = default.AdminServer.rcmd_eval
        host.rcon_invoke('echo "[evalpy] loaded"')
    except:
        try:
            import traceback
            f = open('evalpy.log', 'a')
            traceback.print_exc(file=f)
            f.close()
        except:
            pass

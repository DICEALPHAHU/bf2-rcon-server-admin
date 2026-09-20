#!/usr/bin/env python3
"""
mockplayer_test.py -- exercise the player-dependent code paths without a real player.

WHY
    pmpos / pmkill / pmkick / pmban / pmveh all need a player to look up. Testing
    them with a nonexistent nickname only proves the "not found" branch. To reach
    the real code path we stub bf2.playerManager so it returns a fake player, then
    call the command. Only engine calls that are already known-safe are reached:
    admin.kickPlayer / admin.banPlayer with an invalid index return
    "Player not found." and affect nobody.

WHAT IT PROVES
    - find_player() name matching works
    - the command bodies build correct strings and reach the engine calls
    - no Python exception in the full path (Python 2.3 is easy to trip)

WHAT IT DOES NOT PROVE
    - that killing a live player works (needs a real player; see VERIFY.md)

Usage:  python mockplayer_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bf2rcon import connect, send  # noqa: E402


# RCON commands are single-line, so multi-line python has to be passed as a
# single exec("...") string. Everything is packed onto one line with \n escapes.
STUB_CODE = (
    "import bf2\\n"
    "class _FP:\\n"
    "    def __init__(s,i,n,t):\\n"
    "        s.index=i; s._n=n; s._t=t\\n"
    "    def getName(s): return s._n\\n"
    "    def getTeam(s): return s._t\\n"
    "    def getAddress(s): return '127.0.0.1'\\n"
    "    def isAlive(s): return True\\n"
    "    def isValid(s): return True\\n"
    "    def isAIPlayer(s): return False\\n"
    "    def isCommander(s): return False\\n"
    "class _FM:\\n"
    "    def getPlayers(s): return [_FP(9999,'MockPlayer',2)]\\n"
    "    def getPlayerByIndex(s,i): return _FP(9999,'MockPlayer',2)\\n"
    "import __main__\\n"
    "__main__._pm_orig = bf2.playerManager\\n"
    "bf2.playerManager = _FM()\\n"
)

RESTORE_CODE = (
    "import bf2, __main__\\n"
    "bf2.playerManager = __main__._pm_orig\\n"
)

TESTS = [
    'pmpos MockPlayer',
    'pmkill MockPlayer',
    'pmkick MockPlayer test reason',
    'pmban MockPlayer 30 cheating',
    'pmveh MockPlayer jep_nanjing',
]


def evalpy(sock, code, timeout=10.0):
    """Send python source to the server through evalpy, as one exec() call."""
    escaped = code.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    # careful: code already contains literal backslash-n sequences for newlines
    return send(sock, 'eval exec("%s")' % escaped, timeout)


def main():
    sock = connect('127.0.0.1', 4711, 'deepseek', 8.0)
    print('connected')
    print()

    print('--- install stub ---')
    out = send(sock, 'eval ' + 'exec("' + STUB_CODE + '")', 10.0)
    print('  -> %s' % out.strip()[:250])
    print()

    print('--- commands against the stubbed player ---')
    for c in TESTS:
        out = send(sock, c, 10.0)
        print('  === %s' % c)
        for line in out.strip().split('\n'):
            print('      %s' % line)
    print()

    print('--- restore real playerManager ---')
    out = send(sock, 'eval ' + 'exec("' + RESTORE_CODE + '")', 10.0)
    print('  -> %s' % out.strip()[:250])
    print()

    print('--- server alive? ---')
    out = send(sock, 'eval ctx.write("alive")', 8.0)
    print('  %s' % out.strip())


if __name__ == '__main__':
    main()

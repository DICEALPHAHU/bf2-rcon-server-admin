"""
zzdoctor.py -- load diagnostic, imported FIRST by standard_admin/__init__.py

HARD-WON CONSTRAINTS (bf2's embedded interpreter is not a normal python):

  * Version is 2.3.4 (#0, May 26 2005). No ternary, no sorted(), no set(),
    no decorators, no with, no generator expressions.
  * The bundled `os` module is CRIPPLED. Confirmed by experiment:
        os.getcwd    -> AttributeError: 'module' object has no attribute 'getcwd'
        os.path      -> AttributeError: 'module' object has no attribute 'path'
        os.listdir   -> AttributeError: 'module' object has no attribute 'listdir'
    So this file must NOT import os at all. It gets the directory from
    __file__ with plain string operations.
  * stdout is redirected and block-buffered by the launcher, so console text is
    unreliable -- everything important goes to doctor.log.

WHAT IT DOES
  Compile-checks every sibling module and records the exact exception for any
  that fails. That is the whole point: a SyntaxError in one module used to kill
  the entire standard_admin package with no explanation. Now the reason lands in
  doctor.log, with the offending line number.
"""

import sys


_LOGPATH = 'doctor.log'


def _w(text):
    """Append one line to doctor.log. Cannot raise."""
    try:
        f = open(_LOGPATH, 'a')
        f.write(str(text) + '\n')
        f.close()
        return 1
    except:
        return 0


def _wt(text):
    """Log, and also try stdout (the launcher captures it into server-console.log)."""
    _w(text)
    try:
        print '[zzdoctor] ' + str(text)
    except:
        pass


def _fail(prefix):
    """Record the current exception. Must not depend on anything but builtins."""
    try:
        t = str(sys.exc_info()[0])
    except:
        t = '?'
    try:
        v = str(sys.exc_info()[1])
    except:
        v = '?'
    _w('  !! ' + str(prefix) + ' EXCEPTION: ' + t + ': ' + v)
    try:
        f = open(_LOGPATH, 'a')
        import traceback
        traceback.print_exc(file=f)
        f.close()
    except:
        pass


_wt('============================================================')
_wt('doctor v3: standard_admin import STARTED  (os module not used)')

try:
    _w('  python      : ' + str(sys.version).replace('\n', ' '))
except:
    _fail('sys.version')

try:
    _w('  platform    : ' + str(sys.platform))
except:
    _fail('sys.platform')

# ---------------------------------------------------------------------------
# Locate our own directory WITHOUT os.
# __file__ is a relative path like 'admin\\standard_admin\\zzdoctor.py'.
# ---------------------------------------------------------------------------
_here = ''
try:
    _f = __file__
    _w('  __file__    : ' + str(_f))
    _parts = _f.replace('/', '\\').split('\\')
    if len(_parts) > 1:
        _here = '\\'.join(_parts[0:len(_parts)-1])
    _w('  dir guess   : ' + repr(_here))
except:
    _fail('resolve __file__')

# ---------------------------------------------------------------------------
# What does the crippled `os` module actually offer? Useful intelligence, and
# it documents the constraint for anything we write later.
# ---------------------------------------------------------------------------
try:
    import os
    _names = []
    for _k in dir(os):
        _names.append(_k)
    _w('  os members  : ' + ', '.join(_names))
except:
    _fail('import os')

# ---------------------------------------------------------------------------
# THE IMPORTANT PART: compile-check every sibling module.
# This is what turns "import failed, no idea why" into an exact answer.
# ---------------------------------------------------------------------------
_wt('  --- compile pre-flight ---')

_MODULES = ('autobalance.py', 'tk_punish.py',
            'playerconnect.py', 'zzdoctor.py')

for _i in range(len(_MODULES)):
    _name = _MODULES[_i]
    if _here:
        _p = _here + '\\' + _name
    else:
        _p = _name
    _w('    checking ' + _p)
    # read in TEXT mode ('rU'), never 'rb'.
    # This embedded 2.3.4 build rejects raw CRLF in compile(), and the stock
    # modules (autobalance / tk_punish / playerconnect) are CRLF. Reading bytes
    # made this check report "*** SYNTAX ERROR *** line 2" for three perfectly
    # healthy files. Text mode normalises newlines the same way import does.
    _src = None
    try:
        _fh = open(_p, 'rU')
        _src = _fh.read()
        _fh.close()
    except:
        _w('      ' + _name + ' : CANNOT OPEN')
        _fail('      open ' + _name)
        continue
    # compile
    try:
        compile(_src, _p, 'exec')
        _w('      ' + _name + ' : SYNTAX OK (' + str(len(_src)) + ' bytes)')
    except:
        _w('      ' + _name + ' : *** SYNTAX ERROR ***')
        _fail('      compile ' + _name)

# ---------------------------------------------------------------------------
# Engine modules the package needs.
# ---------------------------------------------------------------------------
_wt('  --- engine imports ---')
_ENG = ('bf2', 'host', 'default', 'new')
for _i in range(len(_ENG)):
    _m = _ENG[_i]
    try:
        __import__(_m)
        _w('    import ' + _m + ' : OK')
    except:
        _w('    import ' + _m + ' : FAILED')
        _fail('    import ' + _m)

_wt('doctor v3: standard_admin import FINISHED')
_wt('============================================================')


def init():
    _wt('doctor: init() called  <-- standard_admin package fully loaded')

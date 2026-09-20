# standard_admin package init

import zzdoctor

import autobalance
import tk_punish
import playerconnect

autobalance.init()
tk_punish.init()
playerconnect.init()

try:
    import bf2admin
    bf2admin.init()
    zzdoctor._wt('__init__: bf2admin.init() OK')
except:
    zzdoctor._fail('__init__: bf2admin')

try:
    import bf2events
    bf2events.init()
    zzdoctor._wt('__init__: bf2events.init() OK')
except:
    zzdoctor._fail('__init__: bf2events')

try:
    import pmadmin
    pmadmin.init()
    zzdoctor._wt('__init__: pmadmin.init() OK')
except:
    zzdoctor._fail('__init__: pmadmin')

try:
    import evalpy
    evalpy.init()
    zzdoctor._wt('__init__: evalpy.init() OK')
except:
    zzdoctor._fail('__init__: evalpy')

zzdoctor.init()

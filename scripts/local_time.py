"""Print 'HH MM' = this computer's local clock time for a given US Eastern time (default 09:15 ET)."""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

hh, mm = map(int, (sys.argv[1] if len(sys.argv) > 1 else "09:15").split(":"))
et = datetime.now(ZoneInfo("America/New_York")).replace(hour=hh, minute=mm, second=0, microsecond=0)
local = et.astimezone()
print(f"{local.hour:02d} {local.minute:02d}")

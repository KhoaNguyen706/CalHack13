"""Free-time windows in the coordinator's format: "Sat 14:00-16:00", one day, 24 h clock."""

import re
from datetime import datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
LAST_MINUTE = 23 * 60 + 59


class Window(NamedTuple):
    day: str
    start: int  # minutes since midnight
    end: int

    def __str__(self) -> str:
        return f"{self.day} {self.start // 60:02d}:{self.start % 60:02d}-{self.end // 60:02d}:{self.end % 60:02d}"

    @property
    def minutes(self) -> int:
        return self.end - self.start


def parse(text: str) -> Window:
    m = _RE.match(text.strip())
    if not m:
        raise ValueError('window must look like "Sat 14:00-16:00"')
    day, h1, m1, h2, m2 = m.groups()
    start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
    if int(m1) > 59 or int(m2) > 59 or not 0 <= start < end <= 24 * 60:
        raise ValueError("window must be a valid start < end time on one day")
    return Window(day, start, end)


def valid(text: str | None) -> bool:
    try:
        parse(text or "")
        return True
    except ValueError:
        return False


def now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def from_now(tz: str, hours: float = 2, at: datetime | None = None) -> str:
    """The window from now until `hours` later, cut at midnight."""
    t = at or now(tz)
    start = t.hour * 60 + t.minute
    return str(Window(DAYS[t.weekday()], start, min(start + int(hours * 60), LAST_MINUTE)))


def fits(plan: str, free: str) -> bool:
    """True when the plan's window sits inside the free window."""
    p, f = parse(plan), parse(free)
    return p.day == f.day and f.start <= p.start and p.end <= f.end


def shift(window: str, minutes: int, day: str) -> str | None:
    """Move a window by `minutes` onto `day`; None when less than an hour is left before midnight."""
    w = parse(window)
    start, end = max(0, w.start + minutes), min(LAST_MINUTE, w.end + minutes)
    return str(Window(day, start, end)) if end - start >= 60 else None

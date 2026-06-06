"""
expiry_manager.py — Indian Index Options Expiry Manager (2025-26)

Current NSE/BSE expiry schedule:
  NIFTY      → Weekly  every TUESDAY
  SENSEX     → Weekly  every THURSDAY
  BANKNIFTY  → Monthly last TUESDAY of month
  FINNIFTY   → Monthly last TUESDAY of month
  MIDCPNIFTY → Monthly last TUESDAY of month

Rules:
  - On expiry day → use NEXT expiry (avoid theta crush)
  - 1 day before expiry → still use current (has value)
  - Always log which expiry is selected and why
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo
import logging

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _today():
    from datetime import datetime
    return datetime.now(IST).date()

def _now_hour():
    from datetime import datetime
    return datetime.now(IST).hour


def _next_weekday(from_date: date, weekday: int) -> date:
    """Next occurrence of weekday (0=Mon,1=Tue,2=Wed,3=Thu,4=Fri) from from_date."""
    days = (weekday - from_date.weekday()) % 7
    if days == 0:
        days = 7
    return from_date + timedelta(days=days)


def _last_tuesday_of_month(yr: int, mo: int) -> date:
    """Last Tuesday of given month."""
    # Start from last day of month, walk back to Tuesday
    if mo == 12:
        last_day = date(yr+1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(yr, mo+1, 1) - timedelta(days=1)
    # Tuesday = weekday 1
    days_back = (last_day.weekday() - 1) % 7
    return last_day - timedelta(days=days_back)


class ExpiryManager:

    # Weekly indices
    WEEKLY = {
        "NIFTY":  1,   # Tuesday
        "SENSEX": 3,   # Thursday
    }

    # Monthly indices (last Tuesday of month)
    MONTHLY = {"BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

    def __init__(self, index: str):
        self.index = index.upper()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_expiry(self) -> str:
        """
        Returns best expiry date string YYYY-MM-DD.
        On expiry day: returns NEXT expiry.
        """
        today     = _today()
        current   = self._current_expiry(today)
        is_expday = (current == today)

        if is_expday:
            expiry = self._next_expiry_after(current)
            log.info(f"[EXPIRY] {self.index}: Today IS expiry ({current}) "
                     f"→ using NEXT: {expiry}")
        else:
            expiry = current
            dte    = (expiry - today).days
            log.debug(f"[EXPIRY] {self.index}: expiry={expiry} ({dte}d away)")

        return expiry.strftime("%Y-%m-%d")

    def days_to_expiry(self) -> int:
        """Days until current/next expiry (0 = today is expiry)."""
        today  = _today()
        expiry = self._current_expiry(today)
        return (expiry - today).days

    def is_expiry_day(self) -> bool:
        return self._current_expiry(_today()) == _today()

    def next_expiry_str(self) -> str:
        """Always returns the NEXT expiry (skips today if expiry day)."""
        today   = _today()
        current = self._current_expiry(today)
        nxt     = self._next_expiry_after(current)
        return nxt.strftime("%Y-%m-%d")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _current_expiry(self, today: date) -> date:
        """Find the nearest upcoming (or today's) expiry."""
        if self.index in self.WEEKLY:
            wd   = self.WEEKLY[self.index]
            days = (wd - today.weekday()) % 7
            return today + timedelta(days=days)   # 0 = today is expiry

        elif self.index in self.MONTHLY:
            # Last Tuesday of current month
            exp = _last_tuesday_of_month(today.year, today.month)
            if exp < today:
                # This month's expiry already passed → use next month
                nxt_mo = today.month + 1 if today.month < 12 else 1
                nxt_yr = today.year + (1 if today.month == 12 else 0)
                exp    = _last_tuesday_of_month(nxt_yr, nxt_mo)
            return exp

        else:
            # Default: treat as weekly Tuesday
            days = (1 - today.weekday()) % 7
            return today + timedelta(days=days)

    def _next_expiry_after(self, current: date) -> date:
        """Return the expiry AFTER the given one."""
        after = current + timedelta(days=1)
        return self._current_expiry(after)


# ── Quick test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    for idx in ["NIFTY","SENSEX","BANKNIFTY","FINNIFTY","MIDCPNIFTY"]:
        em  = ExpiryManager(idx)
        exp = em.get_expiry()
        dte = em.days_to_expiry()
        print(f"{idx:12s}: expiry={exp}  DTE={dte}d  expiry_day={em.is_expiry_day()}")
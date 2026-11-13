"""
Plans, vid-coins and what each plan unlocks.

- A vid-coin pays for a full-quality export: 1 coin per started minute of the video. Previews, chat edits and
  hand edits are free.
- Free: 2 coins, topped back up to 2 at the start of each calendar month.
  Basic: 10 coins for one month from purchase. Pro: 25 coins for three months from purchase.
  When a paid plan ends, its unused coins end with it and the account is back on Free.
- Buying adds the plan's coins to what's left and starts (or extends) its period. You can't buy Basic while
  Pro is active.
- Limits are checked on the server wherever a timeline is saved or exported and wherever a gated feature starts,
  so hiding a button is never the only gate. Turn billing off with EDITOR_BILLING=0 (everything unlocked).

Every coin movement goes in a ledger (with the balance after it), so usage can be shown and audited.
Payments go through a provider; for now a demo provider that records the purchase without taking money.
"""

from __future__ import annotations

import calendar
import math
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from . import store

CAPTION_BASIC_STYLES = ("karaoke", "minimal")


def _ngn(plan: str, default: int) -> int:
    try:
        return int(os.getenv(f"EDITOR_PRICE_{plan.upper()}_NGN", str(default)))
    except ValueError:
        return default


def plans() -> dict[str, dict]:
    return {
        "free": {
            "name": "Free", "coins": 2, "months": None, "price": {"USD": 0, "NGN": 0},
            "max_angles": 1, "max_broll": 1, "caption_styles": CAPTION_BASIC_STYLES, "caption_fonts": False,
            "ai_background": False, "ai_images": False,
        },
        "basic": {
            "name": "Basic", "coins": 10, "months": 1, "price": {"USD": 3, "NGN": _ngn("basic", 4500)},
            "max_angles": 2, "max_broll": None, "caption_styles": None, "caption_fonts": True,
            "ai_background": False, "ai_images": False,
        },
        "pro": {
            "name": "Pro", "coins": 25, "months": 3, "price": {"USD": 10, "NGN": _ngn("pro", 15000)},
            "max_angles": 3, "max_broll": None, "caption_styles": None, "caption_fonts": True,
            "ai_background": True, "ai_images": True,
        },
    }


RANK = {"free": 0, "basic": 1, "pro": 2}


def enabled() -> bool:
    return os.getenv("EDITOR_BILLING", "1").strip().lower() not in ("0", "false", "no", "off")


class PlanLimit(Exception):
    """A request the person's plan doesn't allow. The message says which plan unlocks it."""

    def __init__(self, message: str, needs: str = "basic"):
        super().__init__(message)
        self.needs = needs


class NotEnoughCoins(PlanLimit):
    pass


# ---------------------------------------------------------------- time

def _now() -> datetime:
    return datetime.now(timezone.utc)


def add_months(t: datetime, months: int) -> datetime:
    m = t.month - 1 + months
    year, month = t.year + m // 12, m % 12 + 1
    day = min(t.day, calendar.monthrange(year, month)[1])
    return t.replace(year=year, month=month, day=day)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: Optional[str]) -> Optional[datetime]:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


# ---------------------------------------------------------------- state

def _ledger(c, user_id: str, delta: int, balance: int, reason: str, ref: str = "") -> None:
    c.execute("INSERT INTO editor_coin_ledger (user_id, delta, balance, reason, ref) VALUES (?, ?, ?, ?, ?)",
              (user_id, delta, balance, reason, ref))


def state(user_id: str) -> dict:
    """The person's plan and coins now, after any expiry or monthly top-up is applied."""
    now = _now()
    month = now.strftime("%Y-%m")
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM editor_billing WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            free = plans()["free"]["coins"]
            c.execute("INSERT INTO editor_billing (user_id, plan, coins, free_month) VALUES (?, 'free', ?, ?)",
                      (user_id, free, month))
            _ledger(c, user_id, free, free, "Free plan coins")
            row = c.execute("SELECT * FROM editor_billing WHERE user_id = ?", (user_id,)).fetchone()
        row = dict(row)
        end = _parse(row["period_end"])
        if row["plan"] != "free" and end and end <= now:
            free = plans()["free"]["coins"]
            c.execute("UPDATE editor_billing SET plan = 'free', coins = ?, period_end = NULL, free_month = ? "
                      "WHERE user_id = ?", (free, month, user_id))
            _ledger(c, user_id, free - row["coins"], free, f"{plans()[row['plan']]['name']} plan ended")
            row.update(plan="free", coins=free, period_end=None, free_month=month)
        elif row["plan"] == "free" and row["free_month"] != month:
            free = plans()["free"]["coins"]
            top = max(row["coins"], free)
            c.execute("UPDATE editor_billing SET coins = ?, free_month = ? WHERE user_id = ?", (top, month, user_id))
            if top != row["coins"]:
                _ledger(c, user_id, top - row["coins"], top, "Monthly free coins")
            row.update(coins=top, free_month=month)
    plan = plans()[row["plan"]]
    return {"plan": row["plan"], "plan_name": plan["name"], "coins": row["coins"], "period_end": row["period_end"],
            "limits": {k: plan[k] for k in ("max_angles", "max_broll", "caption_styles", "caption_fonts",
                                             "ai_background", "ai_images")},
            "billing": enabled()}


def limits(user_id: str) -> dict:
    if not enabled():
        return plans()["pro"] | {"max_broll": None}
    return plans()[state(user_id)["plan"]]


def history(user_id: str, n: int = 25) -> list[dict]:
    with store.connect() as c:
        rows = c.execute("SELECT delta, balance, reason, ref, created_at FROM editor_coin_ledger WHERE user_id = ? "
                         "ORDER BY id DESC LIMIT ?", (user_id, n)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- gates

def _upgrade_to(needs: str) -> str:
    return plans()[needs]["name"]


def check_feature(user_id: str, feature: str) -> None:
    """ai_background / ai_images / caption_fonts: raise PlanLimit if the plan doesn't include it."""
    if not enabled():
        return
    lim = limits(user_id)
    if not lim.get(feature):
        needs = next(p for p in ("basic", "pro") if plans()[p].get(feature))
        what = {"ai_background": "AI background removal", "ai_images": "AI-generated images",
                "caption_fonts": "Caption fonts and styled key words"}[feature]
        raise PlanLimit(f"{what} come{'s' if not what.endswith('s') else ''} with the {_upgrade_to(needs)} plan. "
                        f"Upgrade to use {'it' if not what.endswith('s') else 'them'}.", needs)


def check_angles(user_id: str, n: int) -> None:
    if not enabled():
        return
    cap = limits(user_id)["max_angles"]
    if n > cap:
        needs = "basic" if n <= 2 else "pro"
        raise PlanLimit(f"Your plan allows {cap} camera angle{'s' if cap > 1 else ''}; {n} need the "
                        f"{_upgrade_to(needs)} plan.", needs)


def timeline_problems(user_id: str, tl, assets: dict[str, dict]) -> list[str]:
    """Plan limits a timeline goes over (empty when it's fine or billing is off)."""
    if not enabled():
        return []
    lim = limits(user_id)
    out = []
    if lim["max_broll"] is not None and len(tl.broll) > lim["max_broll"]:
        out.append(f"Your plan allows {lim['max_broll']} B-roll cutaway per project. Upgrade to Basic for more.")
    cap = tl.captions
    if cap is not None:
        if lim["caption_styles"] and cap.style not in lim["caption_styles"]:
            out.append(f"The {cap.style} caption style comes with the Basic plan "
                       f"(your plan has {', '.join(lim['caption_styles'])}).")
        if not lim["caption_fonts"] and (cap.font or cap.emphasis):
            out.append("Caption fonts and styled key words come with the Basic plan.")
    if not lim["caption_fonts"] and any(t.font for t in tl.text):
        out.append("Text fonts come with the Basic plan.")
    if not lim["ai_background"] and any(c.background and c.background.method == "ai" for c in tl.clips):
        out.append("AI background removal comes with the Pro plan.")
    angles_used = {c.angle or 0 for c in tl.clips if (assets.get(c.asset_id) or {}).get("kind") == "multicam"}
    if angles_used and max(angles_used) + 1 > lim["max_angles"]:
        out.append(f"Your plan shows {lim['max_angles']} camera angle(s); camera {max(angles_used) + 1} needs "
                   f"{'Basic' if max(angles_used) + 1 <= 2 else 'Pro'}.")
    return out


# ---------------------------------------------------------------- coins

def export_cost(duration: float) -> int:
    return max(1, math.ceil(max(0.0, duration) / 60 - 1e-9))


def charge(user_id: str, coins: int, reason: str, ref: str) -> int:
    """Take coins (atomically); raises NotEnoughCoins. Returns the new balance."""
    if not enabled() or coins <= 0:
        return state(user_id)["coins"] if enabled() else 0
    have = state(user_id)["coins"]
    with store.connect() as c:
        cur = c.execute("UPDATE editor_billing SET coins = coins - ? WHERE user_id = ? AND coins >= ?",
                        (coins, user_id, coins))
        if cur.rowcount == 0:
            raise NotEnoughCoins(f"This export needs {coins} vid-coin{'s' if coins > 1 else ''} and you have "
                                 f"{have}. Get more coins by upgrading.", "basic")
        balance = c.execute("SELECT coins FROM editor_billing WHERE user_id = ?", (user_id,)).fetchone()[0]
        _ledger(c, user_id, -coins, balance, reason, ref)
    return balance


def refund_job(job: dict) -> None:
    """Give back the coins of a failed export (once)."""
    coins = int((job.get("payload") or {}).get("coins") or 0)
    if coins <= 0:
        return
    uid, ref = job["user_id"], job["id"]
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM editor_coin_ledger WHERE ref = ? AND delta > 0 AND reason LIKE 'Refund%'",
                     (ref,)).fetchone():
            return
        c.execute("UPDATE editor_billing SET coins = coins + ? WHERE user_id = ?", (coins, uid))
        row = c.execute("SELECT coins FROM editor_billing WHERE user_id = ?", (uid,)).fetchone()
        if row is not None:
            _ledger(c, uid, coins, row[0], "Refund: export failed", ref)


# ---------------------------------------------------------------- purchases (demo provider)

class PaymentError(ValueError):
    pass


def checkout(user_id: str, plan: str, currency: str) -> dict:
    p = plans().get(plan)
    if plan not in ("basic", "pro") or p is None:
        raise PaymentError("Choose Basic or Pro.")
    if currency not in ("USD", "NGN"):
        raise PaymentError("Pay in USD or NGN.")
    now_state = state(user_id)
    if RANK[now_state["plan"]] > RANK[plan]:
        raise PaymentError(f"You're on {now_state['plan_name']} until {now_state['period_end'][:10]}; "
                           f"{p['name']} would be a step down.")
    ref = "demo_" + secrets.token_hex(8)
    with store.connect() as c:
        c.execute("INSERT INTO editor_payments (reference, user_id, plan, currency, amount, status, provider) "
                  "VALUES (?, ?, ?, ?, ?, 'pending', 'demo')", (ref, user_id, plan, currency, p["price"][currency]))
    return {"reference": ref, "plan": plan, "plan_name": p["name"], "currency": currency,
            "amount": p["price"][currency], "provider": "demo"}


def confirm(user_id: str, reference: str, success: bool) -> dict:
    """Finish a payment. The demo provider decides the outcome; a real one would verify with the provider."""
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        pay = c.execute("SELECT * FROM editor_payments WHERE reference = ? AND user_id = ?",
                        (reference, user_id)).fetchone()
        if pay is None:
            raise PaymentError("Payment not found.")
        if pay["status"] != "pending":
            raise PaymentError(f"This payment is already {pay['status']}.")
        if not success:
            c.execute("UPDATE editor_payments SET status = 'failed' WHERE reference = ?", (reference,))
            return {"status": "failed"}
        c.execute("UPDATE editor_payments SET status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE reference = ?",
                  (reference,))
    _activate(user_id, pay["plan"], reference)
    return {"status": "paid", **state(user_id)}


def _activate(user_id: str, plan: str, ref: str) -> None:
    st = state(user_id)                       # applies expiry first
    p = plans()[plan]
    now = _now()
    current_end = _parse(st["period_end"]) if st["plan"] == plan else None
    start = current_end if current_end and current_end > now else now
    end = add_months(start, p["months"])
    coins = st["coins"] + p["coins"]
    with store.connect() as c:
        c.execute("UPDATE editor_billing SET plan = ?, coins = ?, period_end = ? WHERE user_id = ?",
                  (plan, coins, _iso(end), user_id))
        _ledger(c, user_id, p["coins"], coins, f"{p['name']} plan purchased", ref)


def payments(user_id: str, n: int = 20) -> list[dict]:
    with store.connect() as c:
        rows = c.execute("SELECT reference, plan, currency, amount, status, provider, created_at, paid_at "
                         "FROM editor_payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                         (user_id, n)).fetchall()
    return [dict(r) for r in rows]

"""Application package initialization.

Keep the legacy front-desk import path stable while routing nightly room-charge
posting through the business-date-aware reconciler.
"""

from . import front_desk as _front_desk
from .room_charge_integrity import post_accrued_room_charges as _reconciled_room_charges

_front_desk.post_accrued_room_charges = _reconciled_room_charges

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import Folio, FolioItem, Guest, Payment, Reservation, ReservationRoom, Room, User
from .night_audit import router as night_audit_router

router = APIRouter(prefix="/reports", tags=["reports"])
router.include_router(night_audit_router)
MONEY = Decimal("0.01")
ACTIVE_STATUSES = ("reserved", "checked_in", "checked_out")


def money(value: Decimal | int | float) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def overlap_nights(check_in: date, check_out: date, period_start: date, period_end: date) -> int:
    start = max(check_in, period_start)
    end = min(check_out, period_end)
    return max(0, (end - start).days)


@router.get("/summary")
def report_summary(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "reception")),
):
    period_start = from_date or date.today()
    period_end_exclusive = (to_date + timedelta(days=1)) if to_date else (period_start + timedelta(days=1))
    if period_end_exclusive <= period_start:
        raise HTTPException(status_code=400, detail="to_date must be on or after from_date")

    period_days = (period_end_exclusive - period_start).days
    total_rooms = db.scalar(select(func.count(Room.id))) or 0
    operational_rooms = db.scalar(select(func.count(Room.id)).where(Room.status != "out_of_order")) or 0

    reservations = db.scalars(
        select(Reservation).where(
            Reservation.check_in < period_end_exclusive,
            Reservation.check_out > period_start,
            Reservation.status.in_(ACTIVE_STATUSES),
        )
    ).all()

    room_count_by_reservation = {
        reservation_id: int(count)
        for reservation_id, count in db.execute(
            select(ReservationRoom.reservation_id, func.count(ReservationRoom.room_id))
            .group_by(ReservationRoom.reservation_id)
        )
    }

    booked_room_nights = 0
    occupied_room_nights = 0
    scheduled_arrivals = 0
    scheduled_departures = 0
    actual_check_ins = 0
    actual_check_outs = 0
    completed_stays = 0
    stays_overlapping_period = 0
    legacy_lifecycle_records = 0

    for reservation in reservations:
        room_count = room_count_by_reservation.get(reservation.id, 0)
        planned_nights = overlap_nights(reservation.check_in, reservation.check_out, period_start, period_end_exclusive)
        booked_room_nights += planned_nights * room_count
        stays_overlapping_period += 1

        if period_start <= reservation.check_in < period_end_exclusive:
            scheduled_arrivals += 1
        if period_start <= reservation.check_out < period_end_exclusive:
            scheduled_departures += 1

        if reservation.checked_in_at is not None:
            if period_start <= reservation.checked_in_at.date() < period_end_exclusive:
                actual_check_ins += 1
        else:
            legacy_lifecycle_records += 1

        if reservation.checked_out_at is not None and period_start <= reservation.checked_out_at.date() < period_end_exclusive:
            actual_check_outs += 1

        if reservation.status == "checked_out":
            completed_stays += 1

        if reservation.checked_in_at is not None:
            effective_start = max(reservation.check_in, reservation.checked_in_at.date())
            effective_end = reservation.check_out
            if reservation.checked_out_at is not None:
                effective_end = min(effective_end, reservation.checked_out_at.date())
            occupied_nights = overlap_nights(effective_start, effective_end, period_start, period_end_exclusive)
            occupied_room_nights += occupied_nights * room_count
        elif reservation.status == "checked_in":
            occupied_room_nights += planned_nights * room_count

    available_room_nights = operational_rooms * period_days
    occupancy_rate = round((occupied_room_nights / available_room_nights) * 100, 2) if available_room_nights else 0.0

    revenue_row = db.execute(
        select(
            func.coalesce(func.sum(FolioItem.quantity * FolioItem.unit_price), 0),
            func.coalesce(func.sum(FolioItem.discount), 0),
        ).where(FolioItem.created_at >= period_start, FolioItem.created_at < period_end_exclusive)
    ).first()
    gross_revenue = money(revenue_row[0] or 0)
    discounts = money(revenue_row[1] or 0)
    net_revenue = money(max(Decimal("0.00"), gross_revenue - discounts))

    payment_total = money(
        db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.created_at >= period_start, Payment.created_at < period_end_exclusive)) or 0
    )
    payment_breakdown = [{"method": method, "amount": float(money(amount or 0))} for method, amount in db.execute(select(Payment.method, func.coalesce(func.sum(Payment.amount), 0)).where(Payment.created_at >= period_start, Payment.created_at < period_end_exclusive).group_by(Payment.method).order_by(Payment.method))]
    item_totals = {folio_id: (Decimal(gross or 0), Decimal(discount or 0)) for folio_id, gross, discount in db.execute(select(FolioItem.folio_id, func.coalesce(func.sum(FolioItem.quantity * FolioItem.unit_price), 0), func.coalesce(func.sum(FolioItem.discount), 0)).group_by(FolioItem.folio_id))}
    payment_totals = {folio_id: Decimal(amount or 0) for folio_id, amount in db.execute(select(Payment.folio_id, func.coalesce(func.sum(Payment.amount), 0)).group_by(Payment.folio_id))}
    outstanding = Decimal("0.00")
    for folio_id in db.scalars(select(Folio.id)):
        gross, discount = item_totals.get(folio_id, (Decimal("0.00"), Decimal("0.00"))); paid = payment_totals.get(folio_id, Decimal("0.00")); outstanding += max(Decimal("0.00"), gross - discount - paid)

    guest_rows = db.execute(select(Guest.full_name, func.count(Reservation.id)).join(Reservation, Reservation.guest_id == Guest.id).where(Reservation.check_in < period_end_exclusive, Reservation.check_out > period_start, Reservation.status.in_(ACTIVE_STATUSES)).group_by(Guest.id, Guest.full_name).order_by(func.count(Reservation.id).desc(), Guest.full_name).limit(5)).all()
    top_guests = [{"guest_name": name, "stays": int(count)} for name, count in guest_rows]

    return {
        "from_date": period_start, "to_date": period_end_exclusive - timedelta(days=1), "period_days": period_days,
        "rooms": {"total": total_rooms, "operational": operational_rooms, "available_room_nights": available_room_nights, "booked_room_nights": booked_room_nights, "occupied_room_nights": occupied_room_nights, "occupancy_rate": occupancy_rate},
        "operations": {"scheduled_arrivals": scheduled_arrivals, "scheduled_departures": scheduled_departures, "actual_check_ins": actual_check_ins, "actual_check_outs": actual_check_outs, "checked_in_guests": sum(1 for reservation in reservations if reservation.status == "checked_in"), "completed_stays": completed_stays, "stays_overlapping_period": stays_overlapping_period, "legacy_lifecycle_records": legacy_lifecycle_records},
        "revenue": {"gross": float(gross_revenue), "discounts": float(discounts), "net": float(net_revenue), "payments_received": float(payment_total), "outstanding_balance": float(money(outstanding))},
        "payment_breakdown": payment_breakdown, "top_guests": top_guests,
    }

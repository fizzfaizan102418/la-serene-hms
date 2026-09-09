from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .business_date import get_current_business_date
from .db import get_db
from .financial_authority import folio_ledger_summary
from .financial_ops import ledger_reconciliation
from .models import BusinessDateState, Folio, FolioItem, Guest, Payment, Reservation, ReservationRoom, Room, User

router = APIRouter(prefix="/reports", tags=["reports"])
MONEY = Decimal("0.01")
ACTIVE_STATUSES = ("reserved", "checked_in", "checked_out")


def money(value: Decimal | int | float) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def overlap_nights(check_in: date, check_out: date, period_start: date, period_end: date) -> int:
    start = max(check_in, period_start); end = min(check_out, period_end)
    return max(0, (end - start).days)


@router.get("/summary")
def report_summary(from_date: date | None = Query(default=None), to_date: date | None = Query(default=None), db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    period_start = from_date or date.today(); period_end_exclusive = (to_date + timedelta(days=1)) if to_date else (period_start + timedelta(days=1))
    if period_end_exclusive <= period_start: raise HTTPException(status_code=400, detail="to_date must be on or after from_date")
    period_days = (period_end_exclusive - period_start).days
    total_rooms = db.scalar(select(func.count(Room.id))) or 0; operational_rooms = db.scalar(select(func.count(Room.id)).where(Room.status != "out_of_order")) or 0
    reservations = db.scalars(select(Reservation).where(Reservation.check_in < period_end_exclusive, Reservation.check_out > period_start, Reservation.status.in_(ACTIVE_STATUSES))).all()
    room_count_by_reservation = {rid: int(count) for rid, count in db.execute(select(ReservationRoom.reservation_id, func.count(ReservationRoom.room_id)).group_by(ReservationRoom.reservation_id))}
    booked_room_nights = occupied_room_nights = scheduled_arrivals = scheduled_departures = actual_check_ins = actual_check_outs = completed_stays = stays_overlapping_period = legacy_lifecycle_records = 0
    for reservation in reservations:
        room_count = room_count_by_reservation.get(reservation.id, 0); planned_nights = overlap_nights(reservation.check_in, reservation.check_out, period_start, period_end_exclusive); booked_room_nights += planned_nights * room_count; stays_overlapping_period += 1
        if period_start <= reservation.check_in < period_end_exclusive: scheduled_arrivals += 1
        if period_start <= reservation.check_out < period_end_exclusive: scheduled_departures += 1
        if reservation.checked_in_at is not None:
            if period_start <= reservation.checked_in_at.date() < period_end_exclusive: actual_check_ins += 1
        else: legacy_lifecycle_records += 1
        if reservation.checked_out_at is not None and period_start <= reservation.checked_out_at.date() < period_end_exclusive: actual_check_outs += 1
        if reservation.status == "checked_out": completed_stays += 1
        if reservation.checked_in_at is not None:
            effective_start = max(reservation.check_in, reservation.checked_in_at.date()); effective_end = reservation.check_out
            if reservation.checked_out_at is not None: effective_end = min(effective_end, reservation.checked_out_at.date())
            occupied_room_nights += overlap_nights(effective_start, effective_end, period_start, period_end_exclusive) * room_count
        elif reservation.status == "checked_in": occupied_room_nights += planned_nights * room_count
    available_room_nights = operational_rooms * period_days; occupancy_rate = round((occupied_room_nights / available_room_nights) * 100, 2) if available_room_nights else 0.0
    revenue_row = db.execute(select(func.coalesce(func.sum(FolioItem.quantity * FolioItem.unit_price), 0), func.coalesce(func.sum(FolioItem.discount), 0)).where(FolioItem.created_at >= period_start, FolioItem.created_at < period_end_exclusive)).first()
    gross_revenue = money(revenue_row[0] or 0); discounts = money(revenue_row[1] or 0); net_revenue = money(max(Decimal("0.00"), gross_revenue - discounts))
    payment_total = money(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.created_at >= period_start, Payment.created_at < period_end_exclusive)) or 0)
    payment_breakdown = [{"method": method, "amount": float(money(amount or 0))} for method, amount in db.execute(select(Payment.method, func.coalesce(func.sum(Payment.amount), 0)).where(Payment.created_at >= period_start, Payment.created_at < period_end_exclusive).group_by(Payment.method).order_by(Payment.method))]
    item_totals = {folio_id: (Decimal(gross or 0), Decimal(discount or 0)) for folio_id, gross, discount in db.execute(select(FolioItem.folio_id, func.coalesce(func.sum(FolioItem.quantity * FolioItem.unit_price), 0), func.coalesce(func.sum(FolioItem.discount), 0)).group_by(FolioItem.folio_id))}; payment_totals = {folio_id: Decimal(amount or 0) for folio_id, amount in db.execute(select(Payment.folio_id, func.coalesce(func.sum(Payment.amount), 0)).group_by(Payment.folio_id))}; outstanding = Decimal("0.00")
    for folio_id in db.scalars(select(Folio.id)):
        gross, discount = item_totals.get(folio_id, (Decimal("0.00"), Decimal("0.00"))); paid = payment_totals.get(folio_id, Decimal("0.00")); outstanding += max(Decimal("0.00"), gross - discount - paid)
    guest_rows = db.execute(select(Guest.full_name, func.count(Reservation.id)).join(Reservation, Reservation.guest_id == Guest.id).where(Reservation.check_in < period_end_exclusive, Reservation.check_out > period_start, Reservation.status.in_(ACTIVE_STATUSES)).group_by(Guest.id, Guest.full_name).order_by(func.count(Reservation.id).desc(), Guest.full_name).limit(5)).all(); top_guests = [{"guest_name": name, "stays": int(count)} for name, count in guest_rows]
    return {"from_date": period_start, "to_date": period_end_exclusive - timedelta(days=1), "period_days": period_days, "rooms": {"total": total_rooms, "operational": operational_rooms, "available_room_nights": available_room_nights, "booked_room_nights": booked_room_nights, "occupied_room_nights": occupied_room_nights, "occupancy_rate": occupancy_rate}, "operations": {"scheduled_arrivals": scheduled_arrivals, "scheduled_departures": scheduled_departures, "actual_check_ins": actual_check_ins, "actual_check_outs": actual_check_outs, "checked_in_guests": sum(1 for reservation in reservations if reservation.status == "checked_in"), "completed_stays": completed_stays, "stays_overlapping_period": stays_overlapping_period, "legacy_lifecycle_records": legacy_lifecycle_records}, "revenue": {"gross": float(gross_revenue), "discounts": float(discounts), "net": float(net_revenue), "payments_received": float(payment_total), "outstanding_balance": float(money(outstanding))}, "payment_breakdown": payment_breakdown, "top_guests": top_guests}


def _revenue_by_account_prefix(db: Session, business_date: date, prefix: str) -> Decimal:
    from .models import FinancialTransaction, LedgerEntry
    rows = db.execute(
        select(LedgerEntry.account, LedgerEntry.direction, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            FinancialTransaction.business_date == business_date,
            FinancialTransaction.status == "posted",
            LedgerEntry.account.like(prefix + "%"),
        )
        .group_by(LedgerEntry.account, LedgerEntry.direction)
    ).all()
    balance = Decimal("0.00")
    for account, direction, amount in rows:
        value = Decimal(amount or 0)
        balance += value if direction == "credit" else -value
    return money(max(Decimal("0.00"), balance))


def management_report(db: Session):
    """Return management KPIs from authoritative business-date and ledger sources."""
    business_date = get_current_business_date(db, fallback_to_today=True)
    day_end = business_date + timedelta(days=1)

    total_rooms = int(db.scalar(select(func.count(Room.id))) or 0)
    active_out_of_order = int(db.scalar(select(func.count(Room.id)).where(Room.status == "out_of_order")) or 0)
    available_room_nights = max(0, total_rooms - active_out_of_order)

    reservation_rows = db.execute(
        select(Reservation.id, Reservation.status, Reservation.check_in, Reservation.check_out, Reservation.checked_in_at, Reservation.checked_out_at, ReservationRoom.room_id)
        .join(ReservationRoom, ReservationRoom.reservation_id == Reservation.id)
        .where(
            Reservation.check_in < day_end,
            Reservation.check_out > business_date,
            Reservation.status.in_(ACTIVE_STATUSES),
        )
    ).all()
    occupied_room_nights = 0
    arrivals = 0
    departures = 0
    checked_in_guests = 0
    seen_arrivals: set[int] = set()
    seen_departures: set[int] = set()
    seen_in_house: set[int] = set()
    for reservation_id, status, check_in, check_out, checked_in_at, checked_out_at, _room_id in reservation_rows:
        if check_in == business_date and reservation_id not in seen_arrivals:
            arrivals += 1; seen_arrivals.add(reservation_id)
        if check_out == business_date and reservation_id not in seen_departures:
            departures += 1; seen_departures.add(reservation_id)
        if status == "checked_in":
            seen_in_house.add(reservation_id)
        if checked_in_at is not None:
            start = max(check_in, checked_in_at.date())
            end = check_out
            if checked_out_at is not None:
                end = min(end, checked_out_at.date())
            occupied_room_nights += overlap_nights(start, end, business_date, day_end)
        elif status == "checked_in":
            occupied_room_nights += overlap_nights(check_in, check_out, business_date, day_end)
    checked_in_guests = len(seen_in_house)

    room_revenue = _revenue_by_account_prefix(db, business_date, "Revenue - room")
    total_revenue = _revenue_by_account_prefix(db, business_date, "Revenue -")
    adr = money(room_revenue / Decimal(occupied_room_nights)) if occupied_room_nights else Decimal("0.00")
    revpar = money(room_revenue / Decimal(available_room_nights)) if available_room_nights else Decimal("0.00")

    finance = ledger_reconciliation(business_date, db, None)
    ar_total = Decimal("0.00")
    for folio_id in db.scalars(select(Folio.id)):
        ar_total += folio_ledger_summary(db, folio_id).balance
    ar_total = money(ar_total)

    state = db.get(BusinessDateState, 1)
    return {
        "business_date": business_date,
        "rooms": {
            "total": total_rooms,
            "out_of_order": active_out_of_order,
            "available_room_nights": available_room_nights,
        },
        "occupancy": {
            "occupied_room_nights": occupied_room_nights,
            "occupancy_rate": round((occupied_room_nights / available_room_nights) * 100, 2) if available_room_nights else 0.0,
            "checked_in_guests": checked_in_guests,
            "arrivals": arrivals,
            "departures": departures,
        },
        "revenue": {
            "room": room_revenue,
            "total": total_revenue,
            "adr": adr,
            "revpar": revpar,
        },
        "finance": {
            "reconciliation_status": finance["reconciliation"]["status"],
            "revenue_difference": money(finance["reconciliation"]["charge_difference"]),
            "cash_difference": money(finance["reconciliation"]["cash_difference"]),
            "ledger_balanced": finance["ledger"]["balanced"],
        },
        "receivables": {"outstanding": ar_total},
        "period": {"posting_open": not bool(state and state.last_closed_at and state.last_closed_at.date() >= business_date)},
    }


@router.get("/management")
def management_report_route(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    return management_report(db)

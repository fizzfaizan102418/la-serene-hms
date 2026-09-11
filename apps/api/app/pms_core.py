from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import require_roles
from .db import Base, get_db
from .models import AuditLog, Folio, Guest, Reservation, ReservationRoom, Room, User

router = APIRouter(prefix="/api", tags=["pms-core"])
MONEY = Decimal("0.01")


class BookingGroup(Base):
    __tablename__ = "booking_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    group_type: Mapped[str] = mapped_column(String(40), default="group")
    primary_guest_id: Mapped[int | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    check_in: Mapped[date | None] = mapped_column(Date, nullable=True)
    check_out: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="tentative", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GroupReservation(Base):
    __tablename__ = "group_reservations"
    group_id: Mapped[int] = mapped_column(ForeignKey("booking_groups.id", ondelete="CASCADE"), primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30), default="member")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Stay(Base):
    __tablename__ = "stays"
    id: Mapped[int] = mapped_column(primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id", ondelete="CASCADE"), index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="reserved", index=True)
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)
    actual_check_in: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_check_out: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    agreed_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    payment_due_policy: Mapped[str] = mapped_column(String(30), default="at_checkout")
    deposit_required: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    deposit_received: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FolioWindow(Base):
    __tablename__ = "folio_windows"
    id: Mapped[int] = mapped_column(primary_key=True)
    folio_id: Mapped[int] = mapped_column(ForeignKey("folios.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    payer_type: Mapped[str] = mapped_column(String(30), default="guest")
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("booking_groups.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GroupCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    group_type: str = Field(default="group", max_length=40)
    primary_guest_id: int | None = None
    company_name: str | None = Field(default=None, max_length=160)
    contact_phone: str | None = Field(default=None, max_length=40)
    contact_email: str | None = Field(default=None, max_length=160)
    check_in: date | None = None
    check_out: date | None = None
    notes: str | None = None


class GroupResponse(GroupCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class GroupReservationLink(BaseModel):
    reservation_id: int
    role: str = Field(default="member", max_length=30)
    notes: str | None = None


class StayCreate(BaseModel):
    room_id: int
    guest_id: int | None = None
    agreed_rate: Decimal = Field(ge=0)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    payment_due_policy: str = Field(default="at_checkout", pattern="^(at_booking|at_checkin|at_checkout|partial)$")
    deposit_required: Decimal = Field(default=Decimal("0"), ge=0)
    deposit_received: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def calculate_discount(rate: Decimal, percent: Decimal, fixed: Decimal) -> tuple[Decimal, Decimal]:
    """Apply percentage and fixed discounts cumulatively, capped at gross."""
    gross = money(rate)
    pct_amount = money(gross * percent / Decimal("100")) if percent else Decimal("0.00")
    fixed_amount = money(fixed) if fixed else Decimal("0.00")
    chosen = min(money(pct_amount + fixed_amount), gross)
    return chosen, money(gross - chosen)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int, details: dict) -> None:
    import json
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


@router.post("/groups", response_model=GroupResponse, status_code=201)
def create_group(payload: GroupCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    if db.scalar(select(BookingGroup.id).where(BookingGroup.code == payload.code)):
        raise HTTPException(status_code=409, detail="Group code already exists")
    if payload.check_in and payload.check_out and payload.check_out <= payload.check_in:
        raise HTTPException(status_code=400, detail="Group check-out must be after check-in")
    if payload.primary_guest_id and not db.get(Guest, payload.primary_guest_id):
        raise HTTPException(status_code=400, detail="Primary guest does not exist")
    group = BookingGroup(**payload.model_dump()); db.add(group); db.flush()
    audit(db, user.id, "create", "booking_group", group.id, {"code": group.code, "name": group.name})
    db.commit(); db.refresh(group)
    return group


@router.get("/groups", response_model=list[GroupResponse])
def list_groups(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    return db.scalars(select(BookingGroup).order_by(BookingGroup.id.desc())).all()


@router.get("/groups/{group_id}", response_model=GroupResponse)
def get_group(group_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    group = db.get(BookingGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.post("/groups/{group_id}/reservations", status_code=204)
def link_reservation_to_group(group_id: int, payload: GroupReservationLink, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    group = db.get(BookingGroup, group_id)
    reservation = db.get(Reservation, payload.reservation_id)
    if not group or not reservation:
        raise HTTPException(status_code=404, detail="Group or reservation not found")
    exists = db.get(GroupReservation, {"group_id": group_id, "reservation_id": payload.reservation_id})
    if exists:
        raise HTTPException(status_code=409, detail="Reservation is already linked to this group")
    db.add(GroupReservation(group_id=group_id, reservation_id=payload.reservation_id, role=payload.role, notes=payload.notes))
    audit(db, user.id, "link", "booking_group", group_id, {"reservation_id": payload.reservation_id, "role": payload.role})
    db.commit()


@router.get("/groups/{group_id}/reservations")
def group_reservations(group_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    if not db.get(BookingGroup, group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    rows = db.execute(
        select(GroupReservation, Reservation, Guest.full_name)
        .join(Reservation, Reservation.id == GroupReservation.reservation_id)
        .join(Guest, Guest.id == Reservation.guest_id)
        .where(GroupReservation.group_id == group_id)
        .order_by(Reservation.check_in, Reservation.id)
    ).all()
    return [
        {
            "reservation_id": link.reservation_id,
            "role": link.role,
            "notes": link.notes,
            "guest_name": guest_name,
            "check_in": reservation.check_in,
            "check_out": reservation.check_out,
            "status": reservation.status,
        }
        for link, reservation, guest_name in rows
    ]


@router.post("/reservations/{reservation_id}/stays", response_model=dict, status_code=201)
def create_reservation_stays(reservation_id: int, payload: list[StayCreate], db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if not payload:
        raise HTTPException(status_code=400, detail="At least one room stay is required")
    if db.scalar(select(Stay.id).where(Stay.reservation_id == reservation_id)):
        raise HTTPException(status_code=409, detail="Room stays already exist for this reservation")

    room_ids = [item.room_id for item in payload]
    if len(room_ids) != len(set(room_ids)):
        raise HTTPException(status_code=400, detail="Duplicate room stays are not allowed")

    stays = []
    for item in payload:
        room = db.get(Room, item.room_id)
        if not room:
            raise HTTPException(status_code=400, detail=f"Room {item.room_id} does not exist")
        if item.guest_id and not db.get(Guest, item.guest_id):
            raise HTTPException(status_code=400, detail=f"Guest {item.guest_id} does not exist")
        discount, net = calculate_discount(item.agreed_rate, item.discount_percent, item.discount_amount)
        if item.deposit_received > item.deposit_required:
            raise HTTPException(status_code=400, detail="Deposit received cannot exceed deposit required")
        stay = Stay(
            reservation_id=reservation.id,
            room_id=item.room_id,
            guest_id=item.guest_id or reservation.guest_id,
            status="checked_in" if reservation.status == "checked_in" else "reserved",
            check_in=reservation.check_in,
            check_out=reservation.check_out,
            actual_check_in=datetime.utcnow() if reservation.status == "checked_in" else None,
            agreed_rate=net,
            discount_percent=item.discount_percent,
            discount_amount=discount,
            payment_due_policy=item.payment_due_policy,
            deposit_required=money(item.deposit_required),
            deposit_received=money(item.deposit_received),
            notes=item.notes,
        )
        db.add(stay); stays.append(stay)

    audit(db, user.id, "create", "stay", reservation.id, {"reservation_id": reservation.id, "room_ids": room_ids})
    db.commit()
    return {"reservation_id": reservation.id, "stays": [{"id": s.id, "room_id": s.room_id, "guest_id": s.guest_id, "agreed_rate": str(s.agreed_rate), "discount_percent": str(s.discount_percent), "discount_amount": str(s.discount_amount), "payment_due_policy": s.payment_due_policy, "deposit_required": str(s.deposit_required), "deposit_received": str(s.deposit_received), "status": s.status} for s in stays]}


@router.get("/reservations/{reservation_id}/stays")
def list_reservation_stays(reservation_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    if not db.get(Reservation, reservation_id):
        raise HTTPException(status_code=404, detail="Reservation not found")
    rows = db.execute(select(Stay, Room.number, Guest.full_name).join(Room, Room.id == Stay.room_id).outerjoin(Guest, Guest.id == Stay.guest_id).where(Stay.reservation_id == reservation_id).order_by(Stay.id)).all()
    return [
        {"id": stay.id, "room_id": stay.room_id, "room_number": room_number, "guest_id": stay.guest_id, "guest_name": guest_name, "status": stay.status, "check_in": stay.check_in, "check_out": stay.check_out, "actual_check_in": stay.actual_check_in, "actual_check_out": stay.actual_check_out, "agreed_rate": stay.agreed_rate, "discount_percent": stay.discount_percent, "discount_amount": stay.discount_amount, "payment_due_policy": stay.payment_due_policy, "deposit_required": stay.deposit_required, "deposit_received": stay.deposit_received, "notes": stay.notes}
        for stay, room_number, guest_name in rows
    ]


@router.patch("/stays/{stay_id}/discount")
def update_stay_discount(stay_id: int, discount_percent: Decimal = 0, discount_amount: Decimal = 0, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = db.get(Stay, stay_id)
    if not stay:
        raise HTTPException(status_code=404, detail="Stay not found")
    if discount_percent < 0 or discount_percent > 100 or discount_amount < 0:
        raise HTTPException(status_code=400, detail="Discount values are invalid")
    discount, net = calculate_discount(stay.agreed_rate + stay.discount_amount, discount_percent, discount_amount)
    stay.discount_percent = discount_percent
    stay.discount_amount = discount
    stay.agreed_rate = net
    audit(db, user.id, "discount_update", "stay", stay.id, {"discount_percent": str(discount_percent), "discount_amount": str(discount), "net_rate": str(net)})
    db.commit(); db.refresh(stay)
    return {"stay_id": stay.id, "discount_percent": stay.discount_percent, "discount_amount": stay.discount_amount, "net_rate": stay.agreed_rate}


@router.post("/folios/{folio_id}/windows", status_code=201)
def create_folio_window(folio_id: int, name: str, payer_type: str = "guest", guest_id: int | None = None, group_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    if payer_type not in {"guest", "group", "company", "other"}:
        raise HTTPException(status_code=400, detail="Invalid payer type")
    if payer_type == "guest" and guest_id and not db.get(Guest, guest_id):
        raise HTTPException(status_code=400, detail="Guest does not exist")
    if payer_type in {"group", "company"} and group_id and not db.get(BookingGroup, group_id):
        raise HTTPException(status_code=400, detail="Group does not exist")
    window = FolioWindow(folio_id=folio_id, name=name.strip(), payer_type=payer_type, guest_id=guest_id, group_id=group_id)
    db.add(window); db.flush()
    audit(db, user.id, "create", "folio_window", window.id, {"folio_id": folio_id, "payer_type": payer_type, "name": window.name})
    db.commit(); db.refresh(window)
    return {"id": window.id, "folio_id": window.folio_id, "name": window.name, "payer_type": window.payer_type, "guest_id": window.guest_id, "group_id": window.group_id, "status": window.status}


@router.get("/folios/{folio_id}/windows")
def list_folio_windows(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    if not db.get(Folio, folio_id):
        raise HTTPException(status_code=404, detail="Folio not found")
    return db.scalars(select(FolioWindow).where(FolioWindow.folio_id == folio_id).order_by(FolioWindow.id)).all()

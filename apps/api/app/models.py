from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Role(TimestampMixin, Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))


class RoomType(TimestampMixin, Base):
    __tablename__ = "room_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    base_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Room(TimestampMixin, Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    room_type_id: Mapped[int] = mapped_column(ForeignKey("room_types.id"))
    status: Mapped[str] = mapped_column(String(30), default="available", index=True)


class Guest(TimestampMixin, Base):
    __tablename__ = "guests"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(160), index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_document: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Reservation(TimestampMixin, Base):
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    guest_id: Mapped[int] = mapped_column(ForeignKey("guests.id"))
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="reserved", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


@event.listens_for(Reservation.status, "set", retval=False)
def track_reservation_lifecycle(target: Reservation, value: str, oldvalue: str | None, initiator):
    if value == "checked_in" and oldvalue != "checked_in" and target.checked_in_at is None:
        target.checked_in_at = datetime.utcnow()
    elif value == "checked_out" and oldvalue != "checked_out" and target.checked_out_at is None:
        target.checked_out_at = datetime.utcnow()


class ReservationRoom(Base):
    __tablename__ = "reservation_rooms"
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), primary_key=True)


class Folio(TimestampMixin, Base):
    __tablename__ = "folios"
    id: Mapped[int] = mapped_column(primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="open")


class FolioItem(TimestampMixin, Base):
    __tablename__ = "folio_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    folio_id: Mapped[int] = mapped_column(ForeignKey("folios.id"))
    stay_id: Mapped[int | None] = mapped_column(ForeignKey("stays.id"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    folio_id: Mapped[int] = mapped_column(ForeignKey("folios.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    method: Mapped[str] = mapped_column(String(30))
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Expense(TimestampMixin, Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[str] = mapped_column(String(30))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StayOccupant(TimestampMixin, Base):
    __tablename__ = "stay_occupants"
    id: Mapped[int] = mapped_column(primary_key=True)
    stay_id: Mapped[int] = mapped_column(ForeignKey("stays.id", ondelete="CASCADE"), index=True)
    guest_id: Mapped[int] = mapped_column(ForeignKey("guests.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="occupant")
    is_primary: Mapped[bool] = mapped_column(default=False)
    check_in: Mapped[date | None] = mapped_column(Date, nullable=True)
    check_out: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class StayRateSegment(TimestampMixin, Base):
    __tablename__ = "stay_rate_segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    stay_id: Mapped[int] = mapped_column(ForeignKey("stays.id", ondelete="CASCADE"), index=True)
    from_date: Mapped[date] = mapped_column(Date)
    to_date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    rate_plan: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class DepositTransaction(TimestampMixin, Base):
    __tablename__ = "deposit_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    stay_id: Mapped[int] = mapped_column(ForeignKey("stays.id"), index=True)
    folio_id: Mapped[int | None] = mapped_column(ForeignKey("folios.id"), nullable=True, index=True)
    transaction_type: Mapped[str] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class RoomMove(TimestampMixin, Base):
    __tablename__ = "room_moves"
    id: Mapped[int] = mapped_column(primary_key=True)
    stay_id: Mapped[int] = mapped_column(ForeignKey("stays.id"), index=True)
    from_room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))
    to_room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))
    effective_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class ReservationSplit(TimestampMixin, Base):
    __tablename__ = "reservation_splits"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), index=True)
    new_reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), index=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    split_check_in: Mapped[date] = mapped_column(Date)
    split_check_out: Mapped[date] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class BusinessDateState(Base):
    __tablename__ = "business_date_state"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    current_business_date: Mapped[date] = mapped_column(Date)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinancialTransaction(TimestampMixin, Base):
    __tablename__ = "financial_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True, index=True, nullable=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    transaction_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="posted", index=True)
    reference_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    folio_id: Mapped[int | None] = mapped_column(ForeignKey("folios.id"), nullable=True, index=True)
    reservation_id: Mapped[int | None] = mapped_column(ForeignKey("reservations.id"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reversal_of_id: Mapped[int | None] = mapped_column(ForeignKey("financial_transactions.id"), nullable=True, index=True)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("financial_transactions.id", ondelete="RESTRICT"), index=True)
    account: Mapped[str] = mapped_column(String(60), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    folio_id: Mapped[int | None] = mapped_column(ForeignKey("folios.id"), nullable=True, index=True)
    stay_id: Mapped[int | None] = mapped_column(ForeignKey("stays.id"), nullable=True, index=True)
    payment_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MenuItem(TimestampMixin, Base):
    __tablename__ = "menu_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(50), default="food")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    active: Mapped[bool] = mapped_column(default=True, index=True)
    stock_item_id: Mapped[int | None] = mapped_column(ForeignKey("stock_items.id"), nullable=True, index=True)
    stock_quantity_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)


class RestaurantOrder(TimestampMixin, Base):
    __tablename__ = "restaurant_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    folio_id: Mapped[int] = mapped_column(ForeignKey("folios.id"), index=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), index=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    service_charge_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.10"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class RestaurantOrderItem(TimestampMixin, Base):
    __tablename__ = "restaurant_order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("restaurant_orders.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), index=True)
    description: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock_quantity_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    folio_item_id: Mapped[int | None] = mapped_column(ForeignKey("folio_items.id"), nullable=True, unique=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StockItem(TimestampMixin, Base):
    __tablename__ = "stock_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    unit: Mapped[str] = mapped_column(String(20), default="unit")
    on_hand: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0)
    active: Mapped[bool] = mapped_column(default=True, index=True)


class StockMovement(TimestampMixin, Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    movement_type: Mapped[str] = mapped_column(String(30), index=True)
    reference_type: Mapped[str] = mapped_column(String(40))
    reference_id: Mapped[str] = mapped_column(String(50))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))

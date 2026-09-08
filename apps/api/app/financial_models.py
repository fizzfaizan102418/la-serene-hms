from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), index=True)
    folio_id: Mapped[int] = mapped_column(ForeignKey("folios.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    method: Mapped[str] = mapped_column(String(30))
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class FolioItemWindow(Base):
    __tablename__ = "folio_item_windows"
    id: Mapped[int] = mapped_column(primary_key=True)
    folio_item_id: Mapped[int] = mapped_column(ForeignKey("folio_items.id", ondelete="CASCADE"), unique=True, index=True)
    folio_window_id: Mapped[int] = mapped_column(ForeignKey("folio_windows.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class InvoiceSequence(Base):
    __tablename__ = "invoice_sequences"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    last_number: Mapped[int] = mapped_column(default=0)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    folio_id: Mapped[int] = mapped_column(ForeignKey("folios.id", ondelete="RESTRICT"), unique=True, index=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id", ondelete="RESTRICT"), index=True)
    business_date: Mapped[datetime] = mapped_column()
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    status: Mapped[str] = mapped_column(String(20), default="issued")
    issued_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    issued_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("folio_id", name="uq_invoice_folio"),)

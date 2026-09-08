from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import DATA_DIR, get_db
from .financial_ops import ledger_reconciliation
from .finance_controls import payment_reconciliation, revenue_report, trial_balance
from .models import AuditLog, BusinessDateState, Expense, Folio, FolioItem, Payment, Reservation, Room, User

router = APIRouter(prefix="/night-audit", tags=["night-audit"])
MONEY = Decimal("0.01")
FOOD_SERVICE_CHARGE_RATE = Decimal("0.10")
FOOD_CATEGORIES = {"food", "restaurant", "room_service", "beverage", "drink", "snack"}
PACK_ROOT = DATA_DIR / "daily_closing"


class ClosingConfirm(BaseModel):
    notes: str | None = None


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def serializable(value):
    if isinstance(value, Decimal):
        return str(money(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serializable(v) for v in value]
    return value


def get_business_date(db: Session) -> date:
    state = db.get(BusinessDateState, 1)
    if state is None:
        now = datetime.utcnow()
        state = BusinessDateState(id=1, current_business_date=date.today(), opened_at=now)
        db.add(state)
        db.flush()
    return state.current_business_date


def audit(db: Session, user_id: int, action: str, business_date: date, details: dict) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type="night_audit",
            entity_id=business_date.isoformat(),
            details=json.dumps(serializable(details)),
        )
    )


def finance_snapshot(db: Session, business_date: date) -> dict:
    reconciliation = ledger_reconciliation(business_date, db, None)
    trial = trial_balance(business_date, db, None)
    payments = payment_reconciliation(business_date, db, None)
    revenue = revenue_report(business_date, db, None)
    return {
        "status": reconciliation["reconciliation"]["status"],
        "ledger_balanced": reconciliation["ledger"]["balanced"],
        "total_debits": reconciliation["ledger"]["debits"],
        "total_credits": reconciliation["ledger"]["credits"],
        "revenue_difference": reconciliation["reconciliation"]["charge_difference"],
        "cash_difference": reconciliation["reconciliation"]["cash_difference"],
        "ledger_transactions": reconciliation["ledger"]["transactions"],
        "trial_balance": {
            "balanced": trial["balanced"],
            "total_debit": trial["total_debit"],
            "total_credit": trial["total_credit"],
            "accounts": trial["accounts"],
        },
        "payment_reconciliation": {
            "received_total": payments["received_total"],
            "refunded_total": payments["refunded_total"],
            "net_total": payments["net_total"],
            "methods": payments["methods"],
        },
        "revenue_reconciliation": {
            "ledger_total": reconciliation["ledger"]["revenue_credits"],
            "operational_total": reconciliation["operational"]["folio_charges"],
            "difference": reconciliation["reconciliation"]["charge_difference"],
            "accounts": revenue["revenue"],
            "total": revenue["total"],
        },
    }


def build_summary(db: Session, business_date: date, finance: dict | None = None):
    day_start = datetime.combine(business_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    daily_items = db.scalars(
        select(FolioItem).where(FolioItem.created_at >= day_start, FolioItem.created_at < day_end)
    ).all()
    room_revenue = Decimal("0.00")
    other_revenue = Decimal("0.00")
    food_revenue = Decimal("0.00")
    for item in daily_items:
        net = max(Decimal("0.00"), Decimal(item.quantity) * Decimal(item.unit_price) - Decimal(item.discount))
        category = item.category.strip().lower()
        if category == "room":
            room_revenue += net
        elif category in FOOD_CATEGORIES:
            food_revenue += net
        else:
            other_revenue += net

    service_charge = money(food_revenue * FOOD_SERVICE_CHARGE_RATE)
    daily_payments = db.scalars(
        select(Payment).where(Payment.created_at >= day_start, Payment.created_at < day_end)
    ).all()
    payment_totals: dict[str, Decimal] = {}
    for payment in daily_payments:
        payment_totals[payment.method] = payment_totals.get(payment.method, Decimal("0.00")) + Decimal(payment.amount)

    rooms = db.scalars(select(Room)).all()
    status_counts = {s: 0 for s in ("available", "reserved", "occupied", "dirty", "out_of_order")}
    for room in rooms:
        status_counts[room.status] = status_counts.get(room.status, 0) + 1

    arrivals = db.scalar(
        select(func.count(Reservation.id)).where(
            Reservation.check_in == business_date,
            Reservation.status.in_(("reserved", "checked_in")),
        )
    ) or 0
    departures = db.scalar(
        select(func.count(Reservation.id)).where(
            Reservation.check_out == business_date,
            Reservation.status == "checked_in",
        )
    ) or 0
    in_house = db.scalar(select(func.count(Reservation.id)).where(Reservation.status == "checked_in")) or 0
    no_shows = db.scalar(
        select(func.count(Reservation.id)).where(
            Reservation.check_in == business_date,
            Reservation.status == "no_show",
        )
    ) or 0
    expenses = db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.created_at >= day_start,
            Expense.created_at < day_end,
        )
    ) or Decimal("0.00")

    gross_revenue = money(room_revenue + food_revenue + service_charge + other_revenue)
    paid_total = money(sum(payment_totals.values(), Decimal("0.00")))

    outstanding = Decimal("0.00")
    for folio in db.scalars(select(Folio)).all():
        items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id)).all()
        total = sum((max(Decimal("0.00"), Decimal(i.quantity) * Decimal(i.unit_price) - Decimal(i.discount)) for i in items), Decimal("0.00"))
        food_net = sum((max(Decimal("0.00"), Decimal(i.quantity) * Decimal(i.unit_price) - Decimal(i.discount)) for i in items if i.category.strip().lower() in FOOD_CATEGORIES), Decimal("0.00"))
        total += food_net * FOOD_SERVICE_CHARGE_RATE
        paid = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.folio_id == folio.id)) or Decimal("0.00")
        outstanding += max(Decimal("0.00"), total - Decimal(paid))

    finance = finance or finance_snapshot(db, business_date)
    state = db.get(BusinessDateState, 1)
    posting_open = not bool(state and state.last_closed_at and state.last_closed_at.date() >= business_date)

    return {
        "business_date": business_date,
        "generated_at": datetime.utcnow(),
        "posting_open": posting_open,
        "occupancy": {
            "total_rooms": len(rooms),
            "occupied_rooms": status_counts.get("occupied", 0),
            "reserved_rooms": status_counts.get("reserved", 0),
            "available_rooms": status_counts.get("available", 0),
            "dirty_rooms": status_counts.get("dirty", 0),
            "out_of_order_rooms": status_counts.get("out_of_order", 0),
            "in_house_reservations": in_house,
        },
        "movement": {"arrivals": arrivals, "departures": departures, "no_shows": no_shows},
        "revenue": {
            "room": money(room_revenue),
            "food": money(food_revenue),
            "food_service_charge": service_charge,
            "other": money(other_revenue),
            "gross": gross_revenue,
        },
        "payments": {method: money(amount) for method, amount in payment_totals.items()} | {"total": paid_total},
        "outstanding": money(outstanding),
        "expenses": money(expenses),
        "net_operating": money(gross_revenue - Decimal(expenses)),
        "finance": finance,
    }


def pack_dir(business_date: date) -> Path:
    path = PACK_ROOT / business_date.isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_json(pack: Path, summary: dict, notes: str | None, closed_by: str, closed_at: datetime) -> Path:
    path = pack / "daily-closing.json"
    payload = {
        "report": serializable(summary),
        "closing": {"notes": notes, "closed_by": closed_by, "closed_at": closed_at.isoformat()},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def build_xlsx(pack: Path, summary: dict, notes: str | None, closed_by: str, closed_at: datetime) -> Path:
    path = pack / "daily-closing.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Closing"
    ws["A1"] = "LA SERENE HOTEL"
    ws["A1"].font = Font(size=16, bold=True)
    ws["A2"] = "Daily Closing / Night Audit"
    ws["A2"].font = Font(size=12, bold=True)
    ws["A3"] = "Business Date"; ws["B3"] = summary["business_date"].isoformat()
    ws["A4"] = "Closed By"; ws["B4"] = closed_by
    ws["A5"] = "Closed At"; ws["B5"] = closed_at.isoformat()
    sections = [
        ("Occupancy", [("Total rooms", summary["occupancy"]["total_rooms"]), ("Occupied rooms", summary["occupancy"]["occupied_rooms"]), ("Reserved rooms", summary["occupancy"]["reserved_rooms"]), ("Available rooms", summary["occupancy"]["available_rooms"]), ("Dirty rooms", summary["occupancy"]["dirty_rooms"]), ("Out of order", summary["occupancy"]["out_of_order_rooms"]), ("In-house reservations", summary["occupancy"]["in_house_reservations"])]),
        ("Guest Movement", [("Arrivals", summary["movement"]["arrivals"]), ("Departures", summary["movement"]["departures"]), ("No-shows", summary["movement"]["no_shows"])]),
        ("Revenue", [("Room revenue", float(summary["revenue"]["room"])), ("Food revenue", float(summary["revenue"]["food"])), ("Food service charge (10%)", float(summary["revenue"]["food_service_charge"])), ("Other revenue", float(summary["revenue"]["other"])), ("Gross revenue", float(summary["revenue"]["gross"]))]),
        ("Cashier", [(k.replace("_", " ").title(), float(v)) for k, v in summary["payments"].items()]),
        ("Finance Control", [("Status", summary["finance"]["status"]), ("Ledger transactions", summary["finance"]["ledger_transactions"]), ("Ledger balanced", summary["finance"]["ledger_balanced"]), ("Revenue difference", float(summary["finance"]["revenue_difference"])), ("Cash difference", float(summary["finance"]["cash_difference"]))]),
        ("Trial Balance", [(f"{row['account']} | debit", float(row["debit"])) for row in summary["finance"]["trial_balance"]["accounts"]] + [("Total debit", float(summary["finance"]["trial_balance"]["total_debit"])), ("Total credit", float(summary["finance"]["trial_balance"]["total_credit"])), ("Balanced", summary["finance"]["trial_balance"]["balanced"])]),
        ("Payment Reconciliation", [(f"{row['method']} | received", float(row["received"])) for row in summary["finance"]["payment_reconciliation"]["methods"]] + [("Received total", float(summary["finance"]["payment_reconciliation"]["received_total"])), ("Refunded total", float(summary["finance"]["payment_reconciliation"]["refunded_total"])), ("Net total", float(summary["finance"]["payment_reconciliation"]["net_total"]))]),
        ("Revenue Reconciliation", [("Ledger revenue", float(summary["finance"]["revenue_reconciliation"]["ledger_total"])), ("Operational folio charges", float(summary["finance"]["revenue_reconciliation"]["operational_total"])), ("Difference", float(summary["finance"]["revenue_reconciliation"]["difference"])), ("Report total", float(summary["finance"]["revenue_reconciliation"]["total"]))]),
        ("Operating", [("Outstanding (end-of-day)", float(summary["outstanding"])), ("Expenses (today)", float(summary["expenses"])), ("Net operating (today)", float(summary["net_operating"]))]),
    ]
    row = 7
    for title, items in sections:
        ws.cell(row, 1, title); ws.cell(row, 1).font = Font(bold=True); ws.cell(row, 1).fill = PatternFill("solid", fgColor="EDE9E0"); row += 1
        for label, value in items:
            ws.cell(row, 1, label); ws.cell(row, 2, value); row += 1
        row += 1
    ws.cell(row, 1, "Closing Notes"); ws.cell(row, 1).font = Font(bold=True); ws.cell(row, 2, notes or ""); ws.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 42; ws.column_dimensions["B"].width = 30
    wb.save(path)
    return path


def build_pdf(pack: Path, summary: dict, notes: str | None, closed_by: str, closed_at: datetime) -> Path:
    path = pack / "daily-closing.pdf"
    styles = getSampleStyleSheet(); styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8.5, leading=11))
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    story = [Paragraph("LA SERENE HOTEL", styles["Title"]), Paragraph("Daily Closing / Night Audit", styles["Heading2"]), Paragraph(f"Business Date: {summary['business_date'].isoformat()} &nbsp;&nbsp; Closed By: {closed_by} &nbsp;&nbsp; Closed At: {closed_at.isoformat()}", styles["Small"]), Spacer(1, 5 * mm)]

    def section(title, rows):
        story.extend([Paragraph(title, styles["Heading3"]), Table(rows, colWidths=[95 * mm, 65 * mm], style=TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#EDE9E0")), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D0CCC3")), ("ALIGN", (1,1), (1,-1), "RIGHT"), ("FONTSIZE", (0,0), (-1,-1), 8.5), ("BOTTOMPADDING", (0,0), (-1,-1), 4), ("TOPPADDING", (0,0), (-1,-1), 4)])), Spacer(1, 3 * mm)])

    section("Occupancy", [["Metric", "Value"], ["Total rooms", summary["occupancy"]["total_rooms"]], ["Occupied rooms", summary["occupancy"]["occupied_rooms"]], ["Reserved rooms", summary["occupancy"]["reserved_rooms"]], ["Available rooms", summary["occupancy"]["available_rooms"]], ["Dirty rooms", summary["occupancy"]["dirty_rooms"]], ["Out of order", summary["occupancy"]["out_of_order_rooms"]], ["In-house reservations", summary["occupancy"]["in_house_reservations"]]])
    section("Guest Movement", [["Metric", "Value"], ["Arrivals", summary["movement"]["arrivals"]], ["Departures", summary["movement"]["departures"]], ["No-shows", summary["movement"]["no_shows"]]])
    section("Revenue", [["Metric", "Amount"], ["Room revenue", f"{summary['revenue']['room']:.2f}"], ["Food revenue", f"{summary['revenue']['food']:.2f}"], ["Food service charge (10%)", f"{summary['revenue']['food_service_charge']:.2f}"], ["Other revenue", f"{summary['revenue']['other']:.2f}"], ["Gross revenue", f"{summary['revenue']['gross']:.2f}"]])
    section("Cashier Collection", [["Payment Method", "Amount"]] + [[k.replace("_", " ").title(), f"{v:.2f}"] for k, v in summary["payments"].items()])
    tb = summary["finance"]["trial_balance"]
    section("Trial Balance", [["Account", "Debit"]] + [[row["account"], f"{row['debit']:.2f}"] for row in tb["accounts"]] + [["Total debit", f"{tb['total_debit']:.2f}"], ["Total credit", f"{tb['total_credit']:.2f}"], ["Balanced", tb["balanced"]]])
    pr = summary["finance"]["payment_reconciliation"]
    section("Payment Reconciliation", [["Method", "Received / Refunded / Net"]] + [[row["method"], f"{row['received']:.2f} / {row['refunded']:.2f} / {row['net']:.2f}"] for row in pr["methods"]] + [["Totals", f"{pr['received_total']:.2f} / {pr['refunded_total']:.2f} / {pr['net_total']:.2f}"]])
    rr = summary["finance"]["revenue_reconciliation"]
    section("Revenue Reconciliation", [["Control", "Amount"], ["Ledger revenue", f"{rr['ledger_total']:.2f}"], ["Operational folio charges", f"{rr['operational_total']:.2f}"], ["Difference", f"{rr['difference']:.2f}"], ["Revenue report total", f"{rr['total']:.2f}"]])
    section("Finance Control", [["Control", "Result"], ["Overall status", summary["finance"]["status"]], ["Ledger balanced", summary["finance"]["ledger_balanced"]], ["Cash difference", f"{summary['finance']['cash_difference']:.2f}"], ["Revenue difference", f"{summary['finance']['revenue_difference']:.2f}"], ["Ledger transactions", summary["finance"]["ledger_transactions"]]])
    section("Operating", [["Metric", "Amount"], ["Outstanding (end-of-day)", f"{summary['outstanding']:.2f}"], ["Expenses (today)", f"{summary['expenses']:.2f}"], ["Net operating (today)", f"{summary['net_operating']:.2f}"]])
    story.extend([Paragraph("Closing Notes", styles["Heading3"]), Paragraph((notes or "No closing notes recorded.").replace("\n", "<br/>"), styles["BodyText"]), Spacer(1, 8 * mm), Table([["Prepared / Closed By", "Head Office Received / Verified"], [closed_by, ""], ["Signature: __________________________", "Signature: __________________________"]], colWidths=[80 * mm, 80 * mm], style=TableStyle([("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#D0CCC3")), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7)]))])
    doc.build(story)
    return path


def create_pack(summary: dict, notes: str | None, closed_by: str, closed_at: datetime) -> dict[str, str]:
    pack = pack_dir(summary["business_date"])
    files = {"json": build_json(pack, summary, notes, closed_by, closed_at), "xlsx": build_xlsx(pack, summary, notes, closed_by, closed_at), "pdf": build_pdf(pack, summary, notes, closed_by, closed_at)}
    return {kind: f.name for kind, f in files.items()}


def get_pack_file(business_date: date, filename: str) -> Path:
    allowed = {"daily-closing.pdf", "daily-closing.xlsx", "daily-closing.json"}
    if filename not in allowed:
        raise HTTPException(status_code=400, detail="Invalid closing pack file")
    path = pack_dir(business_date) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Closing pack has not been generated for this business date")
    return path


@router.get("/preview")
def preview(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    business_date = get_business_date(db)
    return build_summary(db, business_date)


@router.post("/close")
def close_day(payload: ClosingConfirm | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    business_date = get_business_date(db)
    state = db.get(BusinessDateState, 1)
    if state and state.last_closed_at and state.last_closed_at.date() >= business_date:
        raise HTTPException(status_code=409, detail=f"Business date {business_date.isoformat()} is already closed")

    finance = finance_snapshot(db, business_date)
    if finance["status"] != "balanced":
        raise HTTPException(status_code=409, detail={"message": "Financial reconciliation requires review before Night Audit can close", "business_date": business_date, "finance": serializable(finance)})

    summary = build_summary(db, business_date, finance)
    closed_at = datetime.utcnow()
    pack = create_pack(summary, payload.notes if payload else None, user.username, closed_at)

    state = db.get(BusinessDateState, 1)
    if state is None:
        state = BusinessDateState(id=1, current_business_date=business_date, opened_at=closed_at)
        db.add(state)
        db.flush()
    state.last_closed_at = closed_at
    state.current_business_date = business_date + timedelta(days=1)
    state.opened_at = closed_at
    audit(db, user.id, "daily_close", business_date, {"business_date": business_date, "summary": summary, "notes": payload.notes if payload else None, "pack": pack, "closed_by": user.username, "closed_at": closed_at, "next_business_date": state.current_business_date})
    db.commit()
    db.refresh(state)
    return {"status": "closed", "business_date": business_date, "next_business_date": state.current_business_date, "summary": summary, "pack": pack, "closed_by": user.username, "closed_at": closed_at, "download_urls": {k: f"/api/night-audit/pack/{business_date.isoformat()}/{v}" for k, v in pack.items()}}


@router.get("/pack/{business_date}/{filename}")
def download_pack(business_date: date, filename: str, _: User = Depends(require_roles("admin", "reception"))):
    path = get_pack_file(business_date, filename)
    media = {"daily-closing.pdf": "application/pdf", "daily-closing.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "daily-closing.json": "application/json"}[filename]
    return FileResponse(path, media_type=media, filename=filename)

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import DATA_DIR, get_db
from .models import AuditLog, Expense, Folio, FolioItem, Payment, Reservation, Room, User

router = APIRouter(prefix="/night-audit", tags=["night-audit"])
MONEY = Decimal("0.01")
FOOD_SERVICE_CHARGE_RATE = Decimal("0.10")
FOOD_CATEGORIES = {"food", "restaurant", "room_service", "beverage", "drink", "snack"}
PACK_ROOT = DATA_DIR / "daily_closing"


def money(value: Decimal | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def serializable(value):
    if isinstance(value, Decimal): return str(money(value))
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, dict): return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, list): return [serializable(v) for v in value]
    return value


def audit(db: Session, user_id: int, action: str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type="night_audit", entity_id=str(date.today()), details=json.dumps(serializable(details))))


class ClosingConfirm(BaseModel):
    notes: str | None = None


def build_summary(db: Session, business_date: date):
    day_start = datetime.combine(business_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # End-of-day revenue is based only on folio lines posted during this business date.
    daily_items = db.scalars(
        select(FolioItem).where(FolioItem.created_at >= day_start, FolioItem.created_at < day_end)
    ).all()
    room_revenue = Decimal("0.00"); other_revenue = Decimal("0.00"); food_revenue = Decimal("0.00")
    for item in daily_items:
        net = max(Decimal("0.00"), Decimal(item.quantity) * Decimal(item.unit_price) - Decimal(item.discount))
        category = item.category.strip().lower()
        if category == "room": room_revenue += net
        elif category in FOOD_CATEGORIES: food_revenue += net
        else: other_revenue += net

    service_charge = money(food_revenue * FOOD_SERVICE_CHARGE_RATE)

    # Cashier collection is limited to payments posted during this business date.
    daily_payments = db.scalars(
        select(Payment).where(Payment.created_at >= day_start, Payment.created_at < day_end)
    ).all()
    payment_totals: dict[str, Decimal] = {}
    for payment in daily_payments:
        payment_totals[payment.method] = payment_totals.get(payment.method, Decimal("0.00")) + Decimal(payment.amount)

    rooms = db.scalars(select(Room)).all()
    status_counts = {s: 0 for s in ("available", "reserved", "occupied", "dirty", "out_of_order")}
    for room in rooms: status_counts[room.status] = status_counts.get(room.status, 0) + 1

    arrivals = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_in == business_date, Reservation.status.in_(("reserved", "checked_in")))) or 0
    departures = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_out == business_date, Reservation.status == "checked_in")) or 0
    in_house = db.scalar(select(func.count(Reservation.id)).where(Reservation.status == "checked_in")) or 0
    no_shows = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_in == business_date, Reservation.status == "no_show")) or 0

    # Expenses are also limited to expenses entered on this business date.
    expenses = db.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.created_at >= day_start, Expense.created_at < day_end)
    ) or Decimal("0.00")

    gross_revenue = money(room_revenue + food_revenue + service_charge + other_revenue)
    paid_total = money(sum(payment_totals.values(), Decimal("0.00")))

    # Outstanding is an end-of-day snapshot across all folios, not a daily transaction total.
    # This answers: "What is still owed to the hotel when we close today?"
    folios = db.scalars(select(Folio)).all()
    outstanding = Decimal("0.00")
    for folio in folios:
        items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id)).all()
        total = sum((max(Decimal("0.00"), Decimal(i.quantity) * Decimal(i.unit_price) - Decimal(i.discount)) for i in items), Decimal("0.00"))
        food_net = sum((max(Decimal("0.00"), Decimal(i.quantity) * Decimal(i.unit_price) - Decimal(i.discount)) for i in items if i.category.strip().lower() in FOOD_CATEGORIES), Decimal("0.00"))
        total += food_net * FOOD_SERVICE_CHARGE_RATE
        paid = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.folio_id == folio.id)) or Decimal("0.00")
        outstanding += max(Decimal("0.00"), total - Decimal(paid))

    return {
        "business_date": business_date, "generated_at": datetime.utcnow(),
        "occupancy": {"total_rooms": len(rooms), "occupied_rooms": status_counts.get("occupied", 0), "reserved_rooms": status_counts.get("reserved", 0), "available_rooms": status_counts.get("available", 0), "dirty_rooms": status_counts.get("dirty", 0), "out_of_order_rooms": status_counts.get("out_of_order", 0), "in_house_reservations": in_house},
        "movement": {"arrivals": arrivals, "departures": departures, "no_shows": no_shows},
        "revenue": {"room": money(room_revenue), "food": money(food_revenue), "food_service_charge": service_charge, "other": money(other_revenue), "gross": gross_revenue},
        "payments": {method: money(amount) for method, amount in payment_totals.items()} | {"total": paid_total},
        "outstanding": money(outstanding), "expenses": money(expenses), "net_operating": money(gross_revenue - Decimal(expenses)),
    }


def pack_dir(business_date: date) -> Path:
    path = PACK_ROOT / business_date.isoformat(); path.mkdir(parents=True, exist_ok=True); return path


def build_json(pack: Path, summary: dict, notes: str | None, closed_by: str, closed_at: datetime) -> Path:
    path = pack / "daily-closing.json"
    payload = {"report": serializable(summary), "closing": {"notes": notes, "closed_by": closed_by, "closed_at": closed_at.isoformat()}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8"); return path


def build_xlsx(pack: Path, summary: dict, notes: str | None, closed_by: str, closed_at: datetime) -> Path:
    path = pack / "daily-closing.xlsx"; wb = Workbook(); ws = wb.active; ws.title = "Daily Closing"
    ws["A1"] = "LA SERENE HOTEL"; ws["A1"].font = Font(size=16, bold=True)
    ws["A2"] = "Daily Closing / Night Audit"; ws["A2"].font = Font(size=12, bold=True)
    ws["A3"] = "Business Date"; ws["B3"] = summary["business_date"].isoformat(); ws["A4"] = "Closed By"; ws["B4"] = closed_by; ws["A5"] = "Closed At"; ws["B5"] = closed_at.isoformat()
    sections = [
        ("Occupancy", [("Total rooms", summary["occupancy"]["total_rooms"]), ("Occupied rooms", summary["occupancy"]["occupied_rooms"]), ("Reserved rooms", summary["occupancy"]["reserved_rooms"]), ("Available rooms", summary["occupancy"]["available_rooms"]), ("Dirty rooms", summary["occupancy"]["dirty_rooms"]), ("Out of order", summary["occupancy"]["out_of_order_rooms"]), ("In-house reservations", summary["occupancy"]["in_house_reservations"])]),
        ("Guest Movement", [("Arrivals", summary["movement"]["arrivals"]), ("Departures", summary["movement"]["departures"]), ("No-shows", summary["movement"]["no_shows"])]),
        ("Revenue", [("Room revenue", float(summary["revenue"]["room"])), ("Food revenue", float(summary["revenue"]["food"])), ("Food service charge (10%)", float(summary["revenue"]["food_service_charge"])), ("Other revenue", float(summary["revenue"]["other"])), ("Gross revenue", float(summary["revenue"]["gross"]))]),
        ("Cashier", [(k.replace("_", " ").title(), float(v)) for k, v in summary["payments"].items()]),
        ("Operating", [("Outstanding (end-of-day)", float(summary["outstanding"])), ("Expenses (today)", float(summary["expenses"])), ("Net operating (today)", float(summary["net_operating"]))]),
    ]; row = 7
    for title, items in sections:
        ws.cell(row, 1, title); ws.cell(row, 1).font = Font(bold=True); ws.cell(row, 1).fill = PatternFill("solid", fgColor="EDE9E0"); row += 1
        for label, value in items: ws.cell(row, 1, label); ws.cell(row, 2, value); row += 1
        row += 1
    ws.cell(row, 1, "Closing Notes"); ws.cell(row, 1).font = Font(bold=True); ws.cell(row, 2, notes or ""); ws.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top"); ws.column_dimensions["A"].width = 30; ws.column_dimensions["B"].width = 28
    wb.save(path); return path


def build_pdf(pack: Path, summary: dict, notes: str | None, closed_by: str, closed_at: datetime) -> Path:
    path = pack / "daily-closing.pdf"; styles = getSampleStyleSheet(); styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8.5, leading=11))
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    story = [Paragraph("LA SERENE HOTEL", styles["Title"]), Paragraph("Daily Closing / Night Audit", styles["Heading2"]), Paragraph(f"Business Date: {summary['business_date'].isoformat()} &nbsp;&nbsp; Closed By: {closed_by} &nbsp;&nbsp; Closed At: {closed_at.isoformat()}", styles["Small"]), Spacer(1, 5 * mm)]
    def section(title, rows):
        story.extend([Paragraph(title, styles["Heading3"]), Table(rows, colWidths=[95 * mm, 65 * mm], style=TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#EDE9E0")), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D0CCC3")), ("ALIGN", (1,1), (1,-1), "RIGHT"), ("FONTSIZE", (0,0), (-1,-1), 9), ("BOTTOMPADDING", (0,0), (-1,-1), 4), ("TOPPADDING", (0,0), (-1,-1), 4)])), Spacer(1, 3 * mm)])
    section("Occupancy", [["Metric", "Value"], ["Total rooms", summary["occupancy"]["total_rooms"]], ["Occupied rooms", summary["occupancy"]["occupied_rooms"]], ["Reserved rooms", summary["occupancy"]["reserved_rooms"]], ["Available rooms", summary["occupancy"]["available_rooms"]], ["Dirty rooms", summary["occupancy"]["dirty_rooms"]], ["Out of order", summary["occupancy"]["out_of_order_rooms"]], ["In-house reservations", summary["occupancy"]["in_house_reservations"]]])
    section("Guest Movement", [["Metric", "Value"], ["Arrivals", summary["movement"]["arrivals"]], ["Departures", summary["movement"]["departures"]], ["No-shows", summary["movement"]["no_shows"]]])
    section("Revenue", [["Metric", "Amount"], ["Room revenue", f"{summary['revenue']['room']:.2f}"], ["Food revenue", f"{summary['revenue']['food']:.2f}"], ["Food service charge (10%)", f"{summary['revenue']['food_service_charge']:.2f}"], ["Other revenue", f"{summary['revenue']['other']:.2f}"], ["Gross revenue", f"{summary['revenue']['gross']:.2f}"]])
    section("Cashier Collection", [["Payment Method", "Amount"]] + [[k.replace("_", " ").title(), f"{v:.2f}"] for k, v in summary["payments"].items()])
    section("Operating", [["Metric", "Amount"], ["Outstanding (end-of-day)", f"{summary['outstanding']:.2f}"], ["Expenses (today)", f"{summary['expenses']:.2f}"], ["Net operating (today)", f"{summary['net_operating']:.2f}"]])
    story.extend([Paragraph("Closing Notes", styles["Heading3"]), Paragraph((notes or "No closing notes recorded.").replace("\n", "<br/>"), styles["BodyText"]), Spacer(1, 9 * mm), Table([["Prepared / Closed By", "Head Office Received / Verified"], [closed_by, ""], ["Signature: __________________________", "Signature: __________________________"]], colWidths=[80 * mm, 80 * mm], style=TableStyle([("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#D0CCC3")), ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7)]))])
    doc.build(story); return path


def create_pack(summary: dict, notes: str | None, closed_by: str, closed_at: datetime) -> dict[str, str]:
    pack = pack_dir(summary["business_date"]); files = {"json": build_json(pack, summary, notes, closed_by, closed_at), "xlsx": build_xlsx(pack, summary, notes, closed_by, closed_at), "pdf": build_pdf(pack, summary, notes, closed_by, closed_at)}; return {kind: f.name for kind, f in files.items()}


def get_pack_file(business_date: date, filename: str) -> Path:
    if filename not in {"daily-closing.pdf", "daily-closing.xlsx", "daily-closing.json"}: raise HTTPException(status_code=400, detail="Invalid closing pack file")
    path = pack_dir(business_date) / filename
    if not path.exists(): raise HTTPException(status_code=404, detail="Closing pack has not been generated for this business date")
    return path


@router.get("/preview")
def preview(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    return build_summary(db, date.today())


@router.post("/close")
def close_day(payload: ClosingConfirm | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    today = date.today(); existing = db.scalar(select(AuditLog.id).where(AuditLog.entity_type == "night_audit", AuditLog.entity_id == str(today), AuditLog.action == "daily_close").limit(1))
    if existing: raise HTTPException(status_code=409, detail="Daily closing is already completed for today")
    summary = build_summary(db, today); closed_at = datetime.utcnow(); pack = create_pack(summary, payload.notes if payload else None, user.username, closed_at)
    audit(db, user.id, "daily_close", {"business_date": str(today), "summary": summary, "notes": payload.notes if payload else None, "pack": pack, "closed_by": user.username, "closed_at": closed_at})
    db.commit(); return {"status": "closed", "business_date": today, "summary": summary, "pack": pack, "closed_by": user.username, "closed_at": closed_at, "download_urls": {k: f"/api/night-audit/pack/{today.isoformat()}/{v}" for k, v in pack.items()}}


@router.get("/pack/{business_date}/{filename}")
def download_pack(business_date: date, filename: str, _: User = Depends(require_roles("admin", "reception"))):
    path = get_pack_file(business_date, filename); media = {"daily-closing.pdf": "application/pdf", "daily-closing.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "daily-closing.json": "application/json"}[filename]
    return FileResponse(path, media_type=media, filename=filename)

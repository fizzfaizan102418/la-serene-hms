from datetime import date
import json

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .models import AuditLog, Guest, Reservation, ReservationRoom, Room, RoomType, Folio, Role
from .schemas import DashboardResponse, GuestCreate, GuestResponse, HealthResponse, ReservationCreate, ReservationResponse, RoomCreate, RoomResponse, RoomStatusUpdate, RoomTypeCreate, RoomTypeResponse

Base.metadata.create_all(bind=engine)
app = FastAPI(title="La Serene HMS API", version="0.2.0")


def write_audit(db: Session, action: str, entity_type: str, entity_id: int | None = None, details: dict | None = None):
    db.add(AuditLog(action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None, details=json.dumps(details) if details else None))


@app.on_event("startup")
def seed_system_roles():
    with Session(engine) as db:
        for name in ("admin", "reception", "housekeeping"):
            if not db.scalar(select(Role).where(Role.name == name)):
                db.add(Role(name=name))
        db.commit()


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="la-serene-hms-api", mode="offline-first")


@app.get("/api/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db)):
    today = date.today()
    statuses = ("available", "reserved", "occupied", "dirty", "out_of_order")
    counts = {status: 0 for status in statuses}
    for status, count in db.execute(select(Room.status, func.count(Room.id)).group_by(Room.status)):
        if status in counts:
            counts[status] = count
    arrivals = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_in == today, Reservation.status.in_(("reserved", "checked_in")))) or 0
    departures = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_out == today, Reservation.status == "checked_in")) or 0
    in_house = db.scalar(select(func.count(Reservation.id)).where(Reservation.status == "checked_in")) or 0
    return DashboardResponse(business_date=today, total_rooms=sum(counts.values()), available_rooms=counts["available"], reserved_rooms=counts["reserved"], occupied_rooms=counts["occupied"], dirty_rooms=counts["dirty"], out_of_order_rooms=counts["out_of_order"], arrivals_today=arrivals, departures_today=departures, in_house_guests=in_house)


@app.get("/api/room-types", response_model=list[RoomTypeResponse])
def list_room_types(db: Session = Depends(get_db)):
    return db.scalars(select(RoomType).order_by(RoomType.name)).all()


@app.post("/api/room-types", response_model=RoomTypeResponse, status_code=201)
def create_room_type(payload: RoomTypeCreate, db: Session = Depends(get_db)):
    if db.scalar(select(RoomType).where(RoomType.name == payload.name)):
        raise HTTPException(status_code=409, detail="Room type already exists")
    room_type = RoomType(**payload.model_dump())
    db.add(room_type); db.flush(); write_audit(db, "create", "room_type", room_type.id, {"name": room_type.name}); db.commit(); db.refresh(room_type)
    return room_type


@app.get("/api/rooms", response_model=list[RoomResponse])
def list_rooms(db: Session = Depends(get_db)):
    return db.scalars(select(Room).order_by(Room.number)).all()


@app.post("/api/rooms", response_model=RoomResponse, status_code=201)
def create_room(payload: RoomCreate, db: Session = Depends(get_db)):
    if not db.get(RoomType, payload.room_type_id): raise HTTPException(status_code=400, detail="Room type does not exist")
    if db.scalar(select(Room).where(Room.number == payload.number)): raise HTTPException(status_code=409, detail="Room number already exists")
    room = Room(**payload.model_dump()); db.add(room); db.flush(); write_audit(db, "create", "room", room.id, {"number": room.number}); db.commit(); db.refresh(room)
    return room


@app.patch("/api/rooms/{room_id}/status", response_model=RoomResponse)
def update_room_status(room_id: int, payload: RoomStatusUpdate, db: Session = Depends(get_db)):
    room = db.get(Room, room_id)
    if not room: raise HTTPException(status_code=404, detail="Room not found")
    old_status = room.status; room.status = payload.status
    write_audit(db, "status_change", "room", room.id, {"from": old_status, "to": room.status}); db.commit(); db.refresh(room)
    return room


@app.post("/api/guests", response_model=GuestResponse, status_code=201)
def create_guest(payload: GuestCreate, db: Session = Depends(get_db)):
    guest = Guest(**payload.model_dump()); db.add(guest); db.flush(); write_audit(db, "create", "guest", guest.id, {"full_name": guest.full_name}); db.commit(); db.refresh(guest)
    return guest


@app.get("/api/guests", response_model=list[GuestResponse])
def list_guests(db: Session = Depends(get_db)):
    return db.scalars(select(Guest).order_by(Guest.full_name)).all()


@app.post("/api/reservations", response_model=ReservationResponse, status_code=201)
def create_reservation(payload: ReservationCreate, db: Session = Depends(get_db)):
    if payload.check_out <= payload.check_in: raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if not db.get(Guest, payload.guest_id): raise HTTPException(status_code=400, detail="Guest does not exist")
    if len(set(payload.room_ids)) != len(payload.room_ids): raise HTTPException(status_code=400, detail="Duplicate room IDs are not allowed")
    rooms = [db.get(Room, room_id) for room_id in payload.room_ids]
    if any(room is None for room in rooms): raise HTTPException(status_code=400, detail="One or more rooms do not exist")
    if any(room.status != "available" for room in rooms): raise HTTPException(status_code=409, detail="One or more rooms are not available")
    reservation = Reservation(guest_id=payload.guest_id, check_in=payload.check_in, check_out=payload.check_out, notes=payload.notes); db.add(reservation); db.flush()
    for room in rooms:
        db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id)); room.status = "reserved"
    folio = Folio(reservation_id=reservation.id); db.add(folio); db.flush(); write_audit(db, "create", "reservation", reservation.id, {"room_ids": payload.room_ids}); db.commit(); db.refresh(reservation); db.refresh(folio)
    return ReservationResponse(id=reservation.id, guest_id=reservation.guest_id, check_in=reservation.check_in, check_out=reservation.check_out, status=reservation.status, room_ids=payload.room_ids, folio_id=folio.id)

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .models import Guest, Reservation, ReservationRoom, Room, RoomType, Folio
from .schemas import (
    GuestCreate, GuestResponse, HealthResponse, ReservationCreate, ReservationResponse,
    RoomCreate, RoomResponse, RoomTypeCreate, RoomTypeResponse,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="La Serene HMS API", version="0.1.0")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="la-serene-hms-api", mode="offline-first")


@app.get("/api/room-types", response_model=list[RoomTypeResponse])
def list_room_types(db: Session = Depends(get_db)):
    return db.scalars(select(RoomType).order_by(RoomType.name)).all()


@app.post("/api/room-types", response_model=RoomTypeResponse, status_code=201)
def create_room_type(payload: RoomTypeCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(RoomType).where(RoomType.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="Room type already exists")
    room_type = RoomType(**payload.model_dump())
    db.add(room_type)
    db.commit()
    db.refresh(room_type)
    return room_type


@app.get("/api/rooms", response_model=list[RoomResponse])
def list_rooms(db: Session = Depends(get_db)):
    return db.scalars(select(Room).order_by(Room.number)).all()


@app.post("/api/rooms", response_model=RoomResponse, status_code=201)
def create_room(payload: RoomCreate, db: Session = Depends(get_db)):
    if not db.get(RoomType, payload.room_type_id):
        raise HTTPException(status_code=400, detail="Room type does not exist")
    if db.scalar(select(Room).where(Room.number == payload.number)):
        raise HTTPException(status_code=409, detail="Room number already exists")
    room = Room(**payload.model_dump())
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


@app.post("/api/guests", response_model=GuestResponse, status_code=201)
def create_guest(payload: GuestCreate, db: Session = Depends(get_db)):
    guest = Guest(**payload.model_dump())
    db.add(guest)
    db.commit()
    db.refresh(guest)
    return guest


@app.get("/api/guests", response_model=list[GuestResponse])
def list_guests(db: Session = Depends(get_db)):
    return db.scalars(select(Guest).order_by(Guest.full_name)).all()


@app.post("/api/reservations", response_model=ReservationResponse, status_code=201)
def create_reservation(payload: ReservationCreate, db: Session = Depends(get_db)):
    if payload.check_out <= payload.check_in:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if not db.get(Guest, payload.guest_id):
        raise HTTPException(status_code=400, detail="Guest does not exist")

    rooms = [db.get(Room, room_id) for room_id in payload.room_ids]
    if any(room is None for room in rooms):
        raise HTTPException(status_code=400, detail="One or more rooms do not exist")
    if any(room.status != "available" for room in rooms):
        raise HTTPException(status_code=409, detail="One or more rooms are not available")

    reservation = Reservation(
        guest_id=payload.guest_id,
        check_in=payload.check_in,
        check_out=payload.check_out,
        notes=payload.notes,
    )
    db.add(reservation)
    db.flush()

    for room in rooms:
        db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id))
        room.status = "reserved"

    folio = Folio(reservation_id=reservation.id)
    db.add(folio)
    db.commit()
    db.refresh(reservation)
    db.refresh(folio)

    return ReservationResponse(
        id=reservation.id,
        guest_id=reservation.guest_id,
        check_in=reservation.check_in,
        check_out=reservation.check_out,
        status=reservation.status,
        room_ids=payload.room_ids,
        folio_id=folio.id,
    )

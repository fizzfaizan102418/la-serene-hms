from datetime import date
import json

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import create_access_token, get_current_user, hash_password, require_roles, verify_password
from .db import Base, engine, get_db
from .models import AuditLog, Folio, Guest, Reservation, ReservationRoom, Role, Room, RoomType, User
from .schemas import (
    BootstrapAdminRequest,
    DashboardResponse,
    GuestCreate,
    GuestResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    ReservationCreate,
    ReservationResponse,
    RoomCreate,
    RoomResponse,
    RoomStatusUpdate,
    RoomTypeCreate,
    RoomTypeResponse,
    SetupStatusResponse,
)

Base.metadata.create_all(bind=engine)
app = FastAPI(title="La Serene HMS API", version="0.3.0")


def write_audit(db: Session, action: str, entity_type: str, entity_id: int | None = None, details: dict | None = None, user_id: int | None = None):
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None, details=json.dumps(details) if details else None))


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


@app.get("/api/auth/setup-status", response_model=SetupStatusResponse)
def setup_status(db: Session = Depends(get_db)):
    return SetupStatusResponse(initialized=db.scalar(select(User.id)) is not None)


@app.post("/api/auth/bootstrap-admin", response_model=MeResponse, status_code=201)
def bootstrap_admin(payload: BootstrapAdminRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User.id)) is not None:
        raise HTTPException(status_code=409, detail="An initial user already exists")
    role = db.scalar(select(Role).where(Role.name == "admin"))
    if not role:
        raise HTTPException(status_code=500, detail="Admin role is not configured")
    user = User(username=payload.username, password_hash=hash_password(payload.password), role_id=role.id)
    db.add(user)
    db.flush()
    write_audit(db, "bootstrap", "user", user.id, {"username": user.username, "role": role.name}, user.id)
    db.commit()
    return MeResponse(id=user.id, username=user.username, role=role.name)


@app.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    role = db.get(Role, user.role_id)
    if not role:
        raise HTTPException(status_code=500, detail="User role is not configured")
    token = create_access_token(user)
    write_audit(db, "login", "user", user.id, {"username": user.username}, user.id)
    db.commit()
    return LoginResponse(access_token=token, user_id=user.id, username=user.username, role=role.name)


@app.get("/api/auth/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    role = db.get(Role, user.role_id)
    return MeResponse(id=user.id, username=user.username, role=role.name if role else "unknown")


@app.get("/api/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    today = date.today()
    statuses = ("available", "reserved", "occupied", "dirty", "out_of_order")
    counts = {status: 0 for status in statuses}
    for room_status, count in db.execute(select(Room.status, func.count(Room.id)).group_by(Room.status)):
        if room_status in counts:
            counts[room_status] = count
    arrivals = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_in == today, Reservation.status.in_(("reserved", "checked_in")))) or 0
    departures = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_out == today, Reservation.status == "checked_in")) or 0
    in_house = db.scalar(select(func.count(Reservation.id)).where(Reservation.status == "checked_in")) or 0
    return DashboardResponse(business_date=today, total_rooms=sum(counts.values()), available_rooms=counts["available"], reserved_rooms=counts["reserved"], occupied_rooms=counts["occupied"], dirty_rooms=counts["dirty"], out_of_order_rooms=counts["out_of_order"], arrivals_today=arrivals, departures_today=departures, in_house_guests=in_house)


@app.get("/api/room-types", response_model=list[RoomTypeResponse])
def list_room_types(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(RoomType).order_by(RoomType.name)).all()


@app.post("/api/room-types", response_model=RoomTypeResponse, status_code=201)
def create_room_type(payload: RoomTypeCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if db.scalar(select(RoomType).where(RoomType.name == payload.name)):
        raise HTTPException(status_code=409, detail="Room type already exists")
    room_type = RoomType(**payload.model_dump())
    db.add(room_type); db.flush(); write_audit(db, "create", "room_type", room_type.id, {"name": room_type.name}, user.id); db.commit(); db.refresh(room_type)
    return room_type


@app.get("/api/rooms", response_model=list[RoomResponse])
def list_rooms(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(Room).order_by(Room.number)).all()


@app.post("/api/rooms", response_model=RoomResponse, status_code=201)
def create_room(payload: RoomCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if not db.get(RoomType, payload.room_type_id): raise HTTPException(status_code=400, detail="Room type does not exist")
    if db.scalar(select(Room).where(Room.number == payload.number)): raise HTTPException(status_code=409, detail="Room number already exists")
    room = Room(**payload.model_dump()); db.add(room); db.flush(); write_audit(db, "create", "room", room.id, {"number": room.number}, user.id); db.commit(); db.refresh(room)
    return room


@app.patch("/api/rooms/{room_id}/status", response_model=RoomResponse)
def update_room_status(room_id: int, payload: RoomStatusUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    room = db.get(Room, room_id)
    if not room: raise HTTPException(status_code=404, detail="Room not found")
    old_status = room.status; room.status = payload.status
    write_audit(db, "status_change", "room", room.id, {"from": old_status, "to": room.status}, user.id); db.commit(); db.refresh(room)
    return room


@app.post("/api/guests", response_model=GuestResponse, status_code=201)
def create_guest(payload: GuestCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    guest = Guest(**payload.model_dump()); db.add(guest); db.flush(); write_audit(db, "create", "guest", guest.id, {"full_name": guest.full_name}, user.id); db.commit(); db.refresh(guest)
    return guest


@app.get("/api/guests", response_model=list[GuestResponse])
def list_guests(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(Guest).order_by(Guest.full_name)).all()


@app.post("/api/reservations", response_model=ReservationResponse, status_code=201)
def create_reservation(payload: ReservationCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    if payload.check_out <= payload.check_in: raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if not db.get(Guest, payload.guest_id): raise HTTPException(status_code=400, detail="Guest does not exist")
    if len(set(payload.room_ids)) != len(payload.room_ids): raise HTTPException(status_code=400, detail="Duplicate room IDs are not allowed")
    rooms = [db.get(Room, room_id) for room_id in payload.room_ids]
    if any(room is None for room in rooms): raise HTTPException(status_code=400, detail="One or more rooms do not exist")
    if any(room.status != "available" for room in rooms): raise HTTPException(status_code=409, detail="One or more rooms are not available")
    reservation = Reservation(guest_id=payload.guest_id, check_in=payload.check_in, check_out=payload.check_out, notes=payload.notes); db.add(reservation); db.flush()
    for room in rooms:
        db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id)); room.status = "reserved"
    folio = Folio(reservation_id=reservation.id); db.add(folio); db.flush(); write_audit(db, "create", "reservation", reservation.id, {"room_ids": payload.room_ids}, user.id); db.commit(); db.refresh(reservation); db.refresh(folio)
    return ReservationResponse(id=reservation.id, guest_id=reservation.guest_id, check_in=reservation.check_in, check_out=reservation.check_out, status=reservation.status, room_ids=payload.room_ids, folio_id=folio.id)

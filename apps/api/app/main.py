from datetime import date
import json

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import create_access_token, get_current_user, hash_password, require_roles, verify_password
from .billing import router as billing_router
from .backup import router as backup_router
from .db import engine, get_db
from .expenses import router as expenses_router
from .inventory import router as inventory_router
from .models import AuditLog, Folio, Guest, Reservation, ReservationRoom, Role, Room, RoomType, User
from .purchasing import router as purchasing_router
from .phase_a_workflows import router as phase_a_workflows_router
from .pms_core_bootstrap import ensure_pms_core_schema
from .reservation_workflows import router as reservation_workflows_router
from .restaurant_pos import router as restaurant_pos_router
from .reports import router as reports_router
from .business_date import get_current_business_date
from .sqlite_bootstrap import initialize_sqlite_database
from .schemas import (
    AvailabilityResponse, BootstrapAdminRequest, CheckInResponse, CheckOutResponse,
    DashboardResponse, FrontDeskResponse, GuestCreate, GuestResponse, HealthResponse,
    LoginRequest, LoginResponse, MeResponse, ReservationCreate, ReservationListResponse,
    ReservationResponse, RoomCreate, RoomResponse, RoomStatusUpdate, RoomTransferRequest,
    RoomTypeCreate, RoomTypeResponse, RoomTypeUpdate, RoomUpdate, SetupStatusResponse,
)

app = FastAPI(title="La Serene HMS API", version="0.9.1")
app.include_router(billing_router)
app.include_router(backup_router)
app.include_router(expenses_router)
app.include_router(reports_router, prefix="/api")
app.include_router(reservation_workflows_router)
app.include_router(phase_a_workflows_router)
app.include_router(inventory_router)
app.include_router(purchasing_router)
app.include_router(restaurant_pos_router)


def write_audit(db: Session, action: str, entity_type: str, entity_id: int | None = None, details: dict | None = None, user_id: int | None = None):
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None, details=json.dumps(details) if details else None))


def reservation_overlaps(room_id: int, check_in: date, check_out: date, db: Session, exclude_reservation_id: int | None = None) -> bool:
    stmt = (
        select(ReservationRoom.reservation_id)
        .join(Reservation, Reservation.id == ReservationRoom.reservation_id)
        .where(
            ReservationRoom.room_id == room_id,
            Reservation.status == "reserved",
            Reservation.check_in < check_out,
            Reservation.check_out > check_in,
        ).limit(1)
    )
    if exclude_reservation_id is not None:
        stmt = stmt.where(Reservation.id != exclude_reservation_id)
    return db.scalar(stmt) is not None


def reservation_response(db: Session, reservation: Reservation) -> ReservationResponse:
    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation.id)).all()
    folio = db.scalar(select(Folio.id).where(Folio.reservation_id == reservation.id))
    return ReservationResponse(id=reservation.id, guest_id=reservation.guest_id, check_in=reservation.check_in, check_out=reservation.check_out, status=reservation.status, room_ids=room_ids, folio_id=folio or 0)


def reservation_list_item(db: Session, reservation: Reservation, guest_name: str) -> ReservationListResponse:
    return ReservationListResponse(**reservation_response(db, reservation).model_dump(), guest_name=guest_name)


@app.on_event("startup")
def initialize_database():
    initialize_sqlite_database()
    with Session(engine) as db:
        for name in ("admin", "reception", "housekeeping"):
            if not db.scalar(select(Role).where(Role.name == name)):
                db.add(Role(name=name))
        db.commit()
    ensure_pms_core_schema()


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
    db.add(user); db.flush()
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
    today = get_current_business_date(db, fallback_to_today=True); statuses = ("available", "reserved", "occupied", "dirty", "out_of_order")
    counts = {name: 0 for name in statuses}
    for room_status, count in db.execute(select(Room.status, func.count(Room.id)).group_by(Room.status)):
        if room_status in counts: counts[room_status] = count
    arrivals = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_in == today, Reservation.status == "reserved")) or 0
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
    room_type = RoomType(**payload.model_dump()); db.add(room_type); db.flush()
    write_audit(db, "create", "room_type", room_type.id, {"name": room_type.name, "base_rate": str(room_type.base_rate)}, user.id)
    db.commit(); db.refresh(room_type); return room_type


@app.patch("/api/room-types/{room_type_id}", response_model=RoomTypeResponse)
def update_room_type(room_type_id: int, payload: RoomTypeUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    room_type = db.get(RoomType, room_type_id)
    if not room_type: raise HTTPException(status_code=404, detail="Room type not found")
    if db.scalar(select(RoomType).where(RoomType.name == payload.name, RoomType.id != room_type_id)):
        raise HTTPException(status_code=409, detail="Room type already exists")
    old = {"name": room_type.name, "base_rate": str(room_type.base_rate), "description": room_type.description}
    room_type.name = payload.name; room_type.base_rate = payload.base_rate; room_type.description = payload.description
    write_audit(db, "update", "room_type", room_type.id, {"from": old, "to": payload.model_dump(mode="json")}, user.id)
    db.commit(); db.refresh(room_type); return room_type


@app.get("/api/rooms", response_model=list[RoomResponse])
def list_rooms(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(Room).order_by(Room.number)).all()


@app.post("/api/rooms", response_model=RoomResponse, status_code=201)
def create_room(payload: RoomCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if not db.get(RoomType, payload.room_type_id): raise HTTPException(status_code=400, detail="Room type does not exist")
    if db.scalar(select(Room).where(Room.number == payload.number)): raise HTTPException(status_code=409, detail="Room number already exists")
    room = Room(**payload.model_dump()); db.add(room); db.flush()
    write_audit(db, "create", "room", room.id, {"number": room.number, "room_type_id": room.room_type_id}, user.id)
    db.commit(); db.refresh(room); return room


@app.patch("/api/rooms/{room_id}", response_model=RoomResponse)
def update_room(room_id: int, payload: RoomUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    room = db.get(Room, room_id)
    if not room: raise HTTPException(status_code=404, detail="Room not found")
    if not db.get(RoomType, payload.room_type_id): raise HTTPException(status_code=400, detail="Room type does not exist")
    if db.scalar(select(Room).where(Room.number == payload.number, Room.id != room_id)): raise HTTPException(status_code=409, detail="Room number already exists")
    old = {"number": room.number, "room_type_id": room.room_type_id}
    room.number = payload.number; room.room_type_id = payload.room_type_id
    write_audit(db, "update", "room", room.id, {"from": old, "to": payload.model_dump()}, user.id)
    db.commit(); db.refresh(room); return room


@app.patch("/api/rooms/{room_id}/status", response_model=RoomResponse)
def update_room_status(room_id: int, payload: RoomStatusUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    room = db.get(Room, room_id)
    if not room: raise HTTPException(status_code=404, detail="Room not found")
    if room.status == "reserved" and payload.status == "available":
        active = db.scalar(select(ReservationRoom.reservation_id).where(ReservationRoom.room_id == room.id).join(Reservation, Reservation.id == ReservationRoom.reservation_id).where(Reservation.status == "reserved").limit(1))
        if active: raise HTTPException(status_code=409, detail="Room is linked to an active reservation")
    old_status = room.status; room.status = payload.status
    write_audit(db, "status_change", "room", room.id, {"from": old_status, "to": room.status}, user.id)
    db.commit(); db.refresh(room); return room


GUEST_SEARCH_DEFAULT_LIMIT = 20
GUEST_SEARCH_MAX_LIMIT = 50


def _guest_identifier(value: str | None) -> str:
    return (value or "").strip().lower()


def _guest_duplicate_matches(db: Session, payload: GuestCreate, exclude_guest_id: int | None = None) -> list[Guest]:
    phone = _guest_identifier(payload.phone)
    id_document = _guest_identifier(payload.id_document)
    if not phone and not id_document:
        return []

    conditions = []
    if phone:
        conditions.append(func.lower(Guest.phone) == phone)
    if id_document:
        conditions.append(func.lower(Guest.id_document) == id_document)

    stmt = select(Guest).where(conditions[0] if len(conditions) == 1 else conditions[0] | conditions[1]).order_by(Guest.full_name).limit(10)
    if exclude_guest_id is not None:
        stmt = stmt.where(Guest.id != exclude_guest_id)
    return db.scalars(stmt).all()


def _raise_guest_duplicate_warning(matches: list[Guest]) -> None:
    if not matches:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Possible duplicate guest record. Verify the guest before saving.",
            "matches": [
                {
                    "id": guest.id,
                    "full_name": guest.full_name,
                    "phone": guest.phone,
                    "id_document": guest.id_document,
                }
                for guest in matches
            ],
        },
    )


@app.get("/api/guests", response_model=list[GuestResponse])
def list_guests(
    q: str | None = Query(default=None, min_length=1, max_length=160),
    limit: int = Query(default=GUEST_SEARCH_DEFAULT_LIMIT, ge=1, le=GUEST_SEARCH_MAX_LIMIT),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Guest)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where((Guest.full_name.ilike(pattern)) | (Guest.phone.ilike(pattern)) | (Guest.email.ilike(pattern)))
    return db.scalars(stmt.order_by(Guest.full_name).limit(limit)).all()


@app.get("/api/guests/directory")
def guest_directory(
    q: str | None = Query(default=None, min_length=1, max_length=160),
    limit: int = Query(default=25, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    base = select(Guest)
    count_stmt = select(func.count(Guest.id))
    if q:
        pattern = f"%{q.strip()}%"
        condition = (
            Guest.full_name.ilike(pattern)
            | Guest.phone.ilike(pattern)
            | Guest.email.ilike(pattern)
            | Guest.address.ilike(pattern)
            | Guest.id_document.ilike(pattern)
        )
        base = base.where(condition)
        count_stmt = count_stmt.where(condition)

    total = db.scalar(count_stmt) or 0
    guests = db.scalars(
        base.order_by(Guest.full_name, Guest.id).offset(offset).limit(limit)
    ).all()
    return {
        "items": guests,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/guests/duplicate-check", response_model=list[GuestResponse])
def guest_duplicate_check(
    phone: str | None = Query(default=None, max_length=40),
    id_document: str | None = Query(default=None, max_length=100),
    exclude_guest_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    payload = GuestCreate(full_name="Duplicate check", phone=phone, id_document=id_document)
    return _guest_duplicate_matches(db, payload, exclude_guest_id=exclude_guest_id)


@app.post("/api/guests", response_model=GuestResponse, status_code=201)
def create_guest(payload: GuestCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    _raise_guest_duplicate_warning(_guest_duplicate_matches(db, payload))
    guest = Guest(**payload.model_dump()); db.add(guest); db.flush()
    write_audit(db, "create", "guest", guest.id, {"full_name": guest.full_name}, user.id)
    db.commit(); db.refresh(guest); return guest


@app.patch("/api/guests/{guest_id}", response_model=GuestResponse)
def update_guest(guest_id: int, payload: GuestCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    guest = db.get(Guest, guest_id)
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    _raise_guest_duplicate_warning(_guest_duplicate_matches(db, payload, exclude_guest_id=guest_id))
    old = {
        "full_name": guest.full_name,
        "phone": guest.phone,
        "email": guest.email,
        "address": guest.address,
        "id_document": guest.id_document,
    }
    values = payload.model_dump()
    guest.full_name = values["full_name"]
    guest.phone = values["phone"]
    guest.email = values["email"]
    guest.address = values["address"]
    guest.id_document = values["id_document"]
    write_audit(db, "update", "guest", guest.id, {"from": old, "to": values}, user.id)
    db.commit(); db.refresh(guest); return guest


@app.get("/api/availability", response_model=AvailabilityResponse)
def availability(check_in: date, check_out: date, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if check_out <= check_in: raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    rooms = db.scalars(select(Room).where(Room.status.not_in(("dirty", "out_of_order"))).order_by(Room.number)).all()
    available = [room for room in rooms if not reservation_overlaps(room.id, check_in, check_out, db)]
    return AvailabilityResponse(check_in=check_in, check_out=check_out, rooms=available)


@app.get("/api/reservations", response_model=list[ReservationListResponse])
def list_reservations(status_filter: str | None = Query(default=None, alias="status"), from_date: date | None = None, to_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    stmt = select(Reservation, Guest.full_name).join(Guest, Guest.id == Reservation.guest_id)
    if status_filter: stmt = stmt.where(Reservation.status == status_filter)
    if from_date: stmt = stmt.where(Reservation.check_out > from_date)
    if to_date: stmt = stmt.where(Reservation.check_in < to_date)
    rows = db.execute(stmt.order_by(Reservation.check_in, Reservation.id)).all()
    return [reservation_list_item(db, reservation, guest_name) for reservation, guest_name in rows]


@app.get("/api/front-desk", response_model=FrontDeskResponse)
def front_desk(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    today = get_current_business_date(db, fallback_to_today=True)
    arrivals = db.execute(select(Reservation, Guest.full_name).join(Guest, Guest.id == Reservation.guest_id).where(Reservation.check_in == today, Reservation.status == "reserved").order_by(Reservation.id)).all()
    departures = db.execute(select(Reservation, Guest.full_name).join(Guest, Guest.id == Reservation.guest_id).where(Reservation.check_out == today, Reservation.status == "checked_in").order_by(Reservation.id)).all()
    in_house = db.execute(select(Reservation, Guest.full_name).join(Guest, Guest.id == Reservation.guest_id).where(Reservation.status == "checked_in").order_by(Reservation.check_out, Reservation.id)).all()
    return FrontDeskResponse(arrivals=[reservation_list_item(db, r, g) for r, g in arrivals], departures=[reservation_list_item(db, r, g) for r, g in departures], in_house=[reservation_list_item(db, r, g) for r, g in in_house])


@app.post("/api/reservations", response_model=ReservationResponse, status_code=201)
def create_reservation(payload: ReservationCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    if payload.check_out <= payload.check_in: raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if not db.get(Guest, payload.guest_id): raise HTTPException(status_code=400, detail="Guest does not exist")
    if len(set(payload.room_ids)) != len(payload.room_ids): raise HTTPException(status_code=400, detail="Duplicate room IDs are not allowed")
    rooms = [db.get(Room, rid) for rid in payload.room_ids]
    if any(room is None for room in rooms): raise HTTPException(status_code=400, detail="One or more rooms do not exist")
    if any(room.status in ("dirty", "out_of_order") for room in rooms): raise HTTPException(status_code=409, detail="One or more rooms are not operationally bookable")
    conflicts = [room.number for room in rooms if reservation_overlaps(room.id, payload.check_in, payload.check_out, db)]
    if conflicts: raise HTTPException(status_code=409, detail=f"Room(s) unavailable for selected dates: {', '.join(conflicts)}")
    reservation = Reservation(guest_id=payload.guest_id, check_in=payload.check_in, check_out=payload.check_out, notes=payload.notes)
    db.add(reservation); db.flush()
    for room in rooms:
        db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id))
        if payload.check_in == date.today(): room.status = "reserved"
    folio = Folio(reservation_id=reservation.id); db.add(folio); db.flush()
    write_audit(db, "create", "reservation", reservation.id, {"room_ids": payload.room_ids, "guest_id": payload.guest_id, "check_in": str(payload.check_in), "check_out": str(payload.check_out)}, user.id)
    db.commit(); db.refresh(reservation); return reservation_response(db, reservation)


@app.post("/api/reservations/{reservation_id}/check-in", response_model=CheckInResponse)
def check_in_reservation(reservation_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "reserved": raise HTTPException(status_code=409, detail="Reservation is not awaiting check-in")
    if reservation.check_in > date.today(): raise HTTPException(status_code=409, detail="Reservation check-in date is in the future")
    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation.id)).all()
    rooms = [db.get(Room, rid) for rid in room_ids]
    if not rooms or any(room is None for room in rooms): raise HTTPException(status_code=409, detail="Reservation has an invalid room assignment")
    blocked = [room.number for room in rooms if room.status in ("dirty", "out_of_order", "occupied")]
    if blocked: raise HTTPException(status_code=409, detail=f"Assigned room(s) cannot be checked in: {', '.join(blocked)}")
    reservation.status = "checked_in"
    from .pms_core import Stay
    existing_stays = db.scalars(select(Stay).where(Stay.reservation_id == reservation.id)).all()
    if not existing_stays:
        for room in rooms:
            db.add(Stay(reservation_id=reservation.id, room_id=room.id, guest_id=reservation.guest_id, status="checked_in", check_in=reservation.check_in, check_out=reservation.check_out, actual_check_in=__import__("datetime").datetime.utcnow(), agreed_rate=Decimal("0.00")))
    else:
        for stay in existing_stays:
            stay.status = "checked_in"
            stay.actual_check_in = stay.actual_check_in or __import__("datetime").datetime.utcnow()
    for room in rooms: room.status = "occupied"
    write_audit(db, "check_in", "reservation", reservation.id, {"room_ids": room_ids}, user.id)
    for room in rooms: write_audit(db, "check_in", "room", room.id, {"reservation_id": reservation.id}, user.id)
    db.commit(); db.refresh(reservation); return CheckInResponse(**reservation_response(db, reservation).model_dump())


@app.post("/api/reservations/{reservation_id}/check-out", response_model=CheckOutResponse)
def check_out_reservation(reservation_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in": raise HTTPException(status_code=409, detail="Reservation is not checked in")
    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation.id)).all()
    rooms = [db.get(Room, rid) for rid in room_ids]
    reservation.status = "checked_out"
    from .pms_core import Stay
    for stay in db.scalars(select(Stay).where(Stay.reservation_id == reservation.id)).all():
        stay.status = "completed"
        stay.actual_check_out = stay.actual_check_out or __import__("datetime").datetime.utcnow()
    for room in rooms:
        if room and room.status == "occupied": room.status = "dirty"
    write_audit(db, "check_out", "reservation", reservation.id, {"room_ids": room_ids}, user.id)
    for room in rooms:
        if room: write_audit(db, "check_out", "room", room.id, {"reservation_id": reservation.id, "new_status": room.status}, user.id)
    db.commit(); db.refresh(reservation); return CheckOutResponse(**reservation_response(db, reservation).model_dump())


@app.post("/api/reservations/{reservation_id}/transfer", response_model=ReservationResponse)
def transfer_room(reservation_id: int, payload: RoomTransferRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    if payload.from_room_id == payload.to_room_id: raise HTTPException(status_code=400, detail="Source and destination rooms must be different")
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in": raise HTTPException(status_code=409, detail="Room transfer requires a checked-in reservation")
    source = db.get(Room, payload.from_room_id); target = db.get(Room, payload.to_room_id)
    if not source or not target: raise HTTPException(status_code=404, detail="Source or destination room not found")
    link = db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == reservation.id, ReservationRoom.room_id == source.id))
    if not link: raise HTTPException(status_code=409, detail="Source room is not assigned to this reservation")
    if target.status != "available": raise HTTPException(status_code=409, detail="Destination room is not available")
    if reservation_overlaps(target.id, date.today(), reservation.check_out, db, exclude_reservation_id=reservation.id):
        raise HTTPException(status_code=409, detail="Destination room has a conflicting reservation")
    db.delete(link); db.add(ReservationRoom(reservation_id=reservation.id, room_id=target.id))
    from .pms_core import Stay
    stay = db.scalar(select(Stay).where(Stay.reservation_id == reservation.id, Stay.room_id == source.id, Stay.status == "checked_in"))
    if stay: stay.room_id = target.id
    source.status = "dirty"; target.status = "occupied"
    write_audit(db, "room_transfer", "reservation", reservation.id, {"from_room_id": source.id, "to_room_id": target.id}, user.id)
    write_audit(db, "room_transfer", "room", source.id, {"reservation_id": reservation.id, "to_room_id": target.id, "new_status": "dirty"}, user.id)
    write_audit(db, "room_transfer", "room", target.id, {"reservation_id": reservation.id, "from_room_id": source.id, "new_status": "occupied"}, user.id)
    db.commit(); db.refresh(reservation); return reservation_response(db, reservation)

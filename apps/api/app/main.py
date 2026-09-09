from datetime import date
import json

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import create_access_token, get_current_user, hash_password, require_roles, verify_password
from .billing import router as billing_router
from .db import engine, get_db
from .models import AuditLog, Folio, Guest, Reservation, ReservationRoom, Role, Room, RoomType, User
from .phase_a_workflows import router as phase_a_workflows_router
from .pms_core_bootstrap import ensure_pms_core_schema
from .reservation_workflows import router as reservation_workflows_router
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
app.include_router(reservation_workflows_router)
app.include_router(phase_a_workflows_router)


def write_audit(db: Session, action: str, entity_type: str, entity_id: int | None = None, details: dict | None = None, user_id: int | None = None):
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None, details=json.dumps(details) if details else None))


def reservation_overlaps(room_id: int, check_in: date, check_out: date, db: Session, exclude_reservation_id: int | None = None) -> bool:
    stmt = (
        select(ReservationRoom.reservation_id)
        .join(Reservation, Reservation.id == ReservationRoom.reservation_id)
        .where(
            ReservationRoom.room_id == room_id,
            Reservation.status.in_(("reserved", "checked_in")),
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

    # Existing local installations receive the PMS Core backfill/triggers on startup.
    # The SQLite bootstrap above guarantees the required tables exist first.
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
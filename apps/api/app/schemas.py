from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    mode: str


class RoomTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_rate: Decimal = Field(default=0, ge=0)
    description: str | None = None


class RoomTypeResponse(RoomTypeCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class RoomCreate(BaseModel):
    number: str = Field(min_length=1, max_length=20)
    room_type_id: int


class RoomResponse(RoomCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class GuestCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    id_document: str | None = None


class GuestResponse(GuestCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ReservationCreate(BaseModel):
    guest_id: int
    check_in: date
    check_out: date
    room_ids: list[int] = Field(min_length=1)
    notes: str | None = None


class ReservationResponse(BaseModel):
    id: int
    guest_id: int
    check_in: date
    check_out: date
    status: str
    room_ids: list[int]
    folio_id: int

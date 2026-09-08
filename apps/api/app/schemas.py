from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    mode: str


class SetupStatusResponse(BaseModel):
    initialized: bool


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=255)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    role: str


class MeResponse(BaseModel):
    id: int
    username: str
    role: str


class BootstrapAdminRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=255)


class RoomTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_rate: Decimal = Field(default=0, ge=0)
    description: str | None = None


class RoomTypeUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_rate: Decimal = Field(ge=0)
    description: str | None = None


class RoomTypeResponse(RoomTypeCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class RoomCreate(BaseModel):
    number: str = Field(min_length=1, max_length=20)
    room_type_id: int


class RoomUpdate(BaseModel):
    number: str = Field(min_length=1, max_length=20)
    room_type_id: int


class RoomResponse(RoomCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class RoomStatusUpdate(BaseModel):
    status: str = Field(pattern="^(available|reserved|occupied|dirty|out_of_order)$")


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


class ReservationListResponse(ReservationResponse):
    guest_name: str


class AvailabilityResponse(BaseModel):
    check_in: date
    check_out: date
    rooms: list[RoomResponse]


class CheckInResponse(ReservationResponse):
    pass


class CheckOutResponse(ReservationResponse):
    pass


class RoomTransferRequest(BaseModel):
    from_room_id: int
    to_room_id: int


class FrontDeskResponse(BaseModel):
    arrivals: list[ReservationListResponse]
    departures: list[ReservationListResponse]
    in_house: list[ReservationListResponse]


class FolioItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    quantity: Decimal = Field(default=1, gt=0)
    unit_price: Decimal = Field(ge=0)
    discount: Decimal = Field(default=0, ge=0)


class FolioItemResponse(FolioItemCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    line_total: Decimal


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    method: str = Field(pattern="^(cash|card|bank_transfer|other)$")
    reference: str | None = Field(default=None, max_length=100)


class PaymentResponse(PaymentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class FolioResponse(BaseModel):
    id: int
    reservation_id: int
    status: str
    items: list[FolioItemResponse]
    payments: list[PaymentResponse]
    subtotal: Decimal
    discounts: Decimal
    food_service_charge: Decimal = Decimal("0.00")
    total: Decimal
    paid: Decimal
    balance: Decimal


class FolioItemListResponse(BaseModel):
    id: int
    folio_id: int
    description: str
    category: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    line_total: Decimal


class BillingSummaryResponse(BaseModel):
    folio_id: int
    reservation_id: int
    guest_name: str
    status: str
    total: Decimal
    paid: Decimal
    balance: Decimal


class DashboardResponse(BaseModel):
    business_date: date
    total_rooms: int
    available_rooms: int
    reserved_rooms: int
    occupied_rooms: int
    dirty_rooms: int
    out_of_order_rooms: int
    arrivals_today: int
    departures_today: int
    in_house_guests: int

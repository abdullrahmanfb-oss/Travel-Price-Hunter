"""
Provider contract.

Every source — Ignav, Amadeus, Kiwi, Travelpayouts, RateHawk, whatever
comes next — implements the same three things:

    NAME          str
    BOOKABLE      bool   can we actually reserve through it, or is it
                         price-discovery only?
    available()   bool   are credentials present?
    search(req)   list[dict] normalised results

`BOOKABLE` is the important flag. Some sources return a real bookable
offer; others (metasearch, affiliate feeds) return an accurate price plus
a deep link, and you finish on the partner's own site. Both are useful —
but the digest must never imply we can hold a fare we cannot hold.

Normalised result keys (flights):
    amount, currency, carrier, carrier_code, stops, segments,
    slice_index, offer_id, deep_link, refundable, changeable,
    conditions_raw, bookable, provider

Hotels:
    amount, currency, hotel_name, hotel_id, stars, board, room_name,
    lat, lon, free_cancellation, cancel_by, deep_link, bookable, provider

Cars:
    amount, currency, vendor, category, transmission, seats, doors,
    unlimited_mileage, pickup_type, deep_link, bookable, provider
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    NAME: str
    KIND: str            # 'flight' | 'hotel' | 'car'
    BOOKABLE: bool

    def available(self) -> bool: ...
    def search(self, req: dict) -> list[dict]: ...


class ProviderError(Exception):
    pass


def blank_flight(provider, bookable=False):
    return {"provider": provider, "bookable": bookable, "kind": "flight",
            "stops": 0, "segments": [], "flags": [], "deep_link": None,
            "conditions_raw": {}, "refundable": None, "changeable": None}


def blank_hotel(provider, bookable=False):
    return {"provider": provider, "bookable": bookable, "kind": "hotel",
            "flags": [], "deep_link": None, "free_cancellation": None,
            "cancel_by": None, "board": None, "stars": None}


def blank_car(provider, bookable=False):
    return {"provider": provider, "bookable": bookable, "kind": "car",
            "flags": [], "deep_link": None, "unlimited_mileage": None,
            "transmission": None, "seats": None}

"""Contact and CallLog data models."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Contact:
    id: Optional[int] = None
    phone_uid: str = ""          # UID from vCard — stable identity across syncs
    display_name: str = ""       # Name from phone
    phone_number: str = ""       # Primary phone number (raw)
    phone_number_normalized: str = ""  # E.164-like, digits only
    custom_name: Optional[str] = None  # User-set rename; overrides display_name
    is_favorite: bool = False
    last_synced: Optional[str] = None
    raw_vcard: Optional[str] = None
    photo_data: Optional[bytes] = None  # User-uploaded photo (overrides vCard photo)

    @property
    def effective_name(self) -> str:
        """Return custom name if set, otherwise display name, otherwise number."""
        return self.custom_name or self.display_name or self.phone_number

    @staticmethod
    def normalize_number(number: str) -> str:
        """Strip all non-digit characters except leading +."""
        number = number.strip()
        has_plus = number.startswith("+")
        digits = re.sub(r"\D", "", number)
        return ("+" + digits) if has_plus else digits


@dataclass
class CallLog:
    id: Optional[int] = None
    direction: str = "incoming"     # "incoming" | "outgoing" | "missed"
    number: str = ""
    contact_id: Optional[int] = None
    started_at: str = ""
    duration_sec: int = 0
    source_uid: Optional[str] = None  # set for phone-synced entries

    @staticmethod
    def now_iso() -> str:
        return datetime.utcnow().isoformat()
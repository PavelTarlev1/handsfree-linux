"""
AT command parser and builder for HFP.
Stateless — converts raw AT lines to structured ATEvent objects
and builds command strings to send to the phone (AG).
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ATEvent:
    kind: str                    # e.g. "ring", "clip", "ciev", "ok", "error", ...
    raw: str = ""
    params: dict = field(default_factory=dict)


# Ordered list of (compiled-regex, event-kind) pairs.
# The regex groups are named and mapped to ATEvent.params.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\+BRSF:\s*(\d+)"),                         "brsf"),
    (re.compile(r"^\+CIND:\s*\("),                            "cind_format"),
    (re.compile(r"^\+CIND:\s*(\d.*)"),                        "cind_values"),
    (re.compile(r"^\+CIEV:\s*(\d+),(\d+)"),                   "ciev"),
    (re.compile(r"^\+CLIP:\s*\"([^\"]*)\",(\d+)"),            "clip"),
    (re.compile(r"^\+CLCC:\s*(.+)"),                          "clcc"),
    (re.compile(r"^\+BCS:\s*(\d+)"),                          "bcs"),
    (re.compile(r"^\+VGM:\s*(\d+)"),                          "vgm"),
    (re.compile(r"^\+VGS:\s*(\d+)"),                          "vgs"),
    (re.compile(r"^\+CHLD:\s*\(([^)]*)\)"),                   "chld"),
    (re.compile(r"^\+CCWA:\s*\"([^\"]*)\",(\d+)"),            "ccwa"),
    (re.compile(r"^\+BVRA:\s*(\d+)"),                         "bvra"),
    (re.compile(r"^\+CME ERROR:\s*(\d+)"),                    "cme_error"),
    (re.compile(r"^RING\b"),                                   "ring"),
    (re.compile(r"^OK\s*$"),                                   "ok"),
    (re.compile(r"^ERROR\s*$"),                                "error"),
    (re.compile(r"^NO CARRIER"),                               "no_carrier"),
]


def parse_line(line: str) -> ATEvent:
    """Parse a single AT response line into an ATEvent."""
    line = line.strip()
    if not line:
        return ATEvent(kind="empty", raw=line)

    for pattern, kind in _PATTERNS:
        m = pattern.match(line)
        if m:
            groups = m.groups()
            params = _extract_params(kind, groups, line)
            return ATEvent(kind=kind, raw=line, params=params)

    logger.debug("Unrecognised AT line: %r", line)
    return ATEvent(kind="unknown", raw=line)


def _extract_params(kind: str, groups: tuple, raw: str) -> dict:
    if kind == "brsf":
        return {"features": int(groups[0])}
    if kind == "ciev":
        return {"index": int(groups[0]), "value": int(groups[1])}
    if kind == "clip":
        return {"number": groups[0], "type": int(groups[1])}
    if kind == "bcs":
        return {"codec": int(groups[0])}  # 1=CVSD, 2=mSBC
    if kind == "vgm":
        return {"gain": int(groups[0])}
    if kind == "vgs":
        return {"gain": int(groups[0])}
    if kind == "cme_error":
        return {"code": int(groups[0])}
    if kind == "cind_values":
        values = [int(x.strip()) for x in groups[0].split(",") if x.strip().isdigit()]
        return {"values": values}
    if kind == "ccwa":
        return {"number": groups[0], "type": int(groups[1])}
    if kind == "chld":
        return {"modes": [m.strip() for m in groups[0].split(",")]}
    if kind == "bvra":
        return {"active": groups[0] == "1"}
    return {}


# ── Command builders ──────────────────────────────────────────────────────────

# HF supported features bitmask (what we tell the phone we support)
# Bit 0: EC/NR  Bit 1: Call Waiting  Bit 2: CLI  Bit 3: Voice recognition
# Bit 4: Remote volume  Bit 5: Enhanced call status  Bit 6: Enhanced call control
# NOTE: Bit 7 (codec negotiation / mSBC) is intentionally NOT set.
# Our raw BTPROTO_SCO socket path uses the kernel's CVSD codec conversion,
# which only works for CVSD.  mSBC uses transparent mode where user-space
# must supply SBC-encoded frames — which we don't do yet.
HF_FEATURES = (
    (1 << 0)  # Echo cancellation / noise reduction
    | (1 << 1)  # Call waiting + three-way calling
    | (1 << 2)  # CLI presentation
    | (1 << 4)  # Remote volume control
    | (1 << 5)  # Enhanced call status
    | (1 << 6)  # Enhanced call control
)

CODEC_CVSD = 1
CODEC_MSBC = 2


def cmd_brsf() -> str:
    return f"AT+BRSF={HF_FEATURES}"


def cmd_bac(preferred: str = "msbc") -> str:
    """Bluetooth Available Codecs.
    Only CVSD is advertised: our raw SCO socket relies on the kernel's
    CVSD conversion (transparent PCM in/out).  mSBC requires user-space
    SBC encoding which is not implemented.
    """
    return f"AT+BAC={CODEC_CVSD}"


def cmd_cind_test() -> str:
    return "AT+CIND=?"


def cmd_cind_read() -> str:
    return "AT+CIND?"


def cmd_cmer() -> str:
    return "AT+CMER=3,0,0,1"


def cmd_chld_test() -> str:
    return "AT+CHLD=?"


def cmd_clip_enable() -> str:
    return "AT+CLIP=1"


def cmd_ccwa_enable() -> str:
    return "AT+CCWA=1"


def cmd_answer() -> str:
    return "ATA"


def cmd_reject() -> str:
    return "AT+CHUP"


def cmd_hangup() -> str:
    return "AT+CHUP"


def cmd_dial(number: str) -> str:
    """Dial a number. Appends ; to indicate voice call."""
    clean = re.sub(r"[^0-9+*#]", "", number)
    return f"ATD{clean};"


def cmd_bcs_confirm(codec: int) -> str:
    return f"AT+BCS={codec}"


def cmd_volume_speaker(gain: int) -> str:
    return f"AT+VGS={max(0, min(15, gain))}"


def cmd_volume_mic(gain: int) -> str:
    return f"AT+VGM={max(0, min(15, gain))}"


def cmd_clcc() -> str:
    """List current calls."""
    return "AT+CLCC"


def cmd_dtmf(digit: str) -> str:
    """Send a DTMF tone (0-9, *, #, A-D)."""
    return f"AT+VTS={digit}"


def cmd_redial() -> str:
    return "AT+BLDN"


def cmd_bcc() -> str:
    """Request Bluetooth Codec Connection (open SCO audio)."""
    return "AT+BCC"
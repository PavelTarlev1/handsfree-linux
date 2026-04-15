"""
PBAP (Phone Book Access Profile) client.
Uses BlueZ obexd via D-Bus to pull contacts from the connected phone.

Requires obexd to be running:
    systemctl --user start obex
    (or: obexd --no-plugin=... &)
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from contacts.models import Contact

logger = logging.getLogger(__name__)

OBEX_BUS_NAME = "org.bluez.obex"
OBEX_CLIENT_PATH = "/org/bluez/obex"


class PBAPClient:
    """
    Pull contacts from a paired phone using PBAP over obexd.

    Usage:
        client = PBAPClient()
        if client.connect(device_address):
            contacts = client.pull_all_contacts()
            client.disconnect()
    """

    def __init__(self):
        self._session_path: Optional[str] = None
        self._session_bus = None
        self._pbap_iface = None

    def _get_session_bus(self):
        if self._session_bus is None:
            import dbus
            self._session_bus = dbus.SessionBus()
        return self._session_bus

    def _ensure_obexd(self) -> bool:
        """Start obexd if it's not already running."""
        try:
            bus = self._get_session_bus()
            bus.get_object(OBEX_BUS_NAME, OBEX_CLIENT_PATH)
            return True
        except Exception:
            pass

        logger.info("obexd not running — attempting to start via systemctl --user")
        try:
            result = subprocess.run(
                ["systemctl", "--user", "start", "obex"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                time.sleep(1)  # Give obexd time to start
                return True
        except Exception:
            pass

        # Try starting obexd directly
        try:
            subprocess.Popen(
                ["obexd"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)
            return True
        except FileNotFoundError:
            logger.error("obexd not found. Install: sudo apt install bluez-obexd")
            return False

    def connect(self, device_address: str) -> bool:
        """Create a PBAP session to the device. Returns True on success."""
        if not self._ensure_obexd():
            return False
        try:
            import dbus
            bus = self._get_session_bus()
            client = dbus.Interface(
                bus.get_object(OBEX_BUS_NAME, OBEX_CLIENT_PATH),
                "org.bluez.obex.Client1",
            )
            session_path = client.CreateSession(
                device_address,
                {"Target": dbus.String("PBAP")},
            )
            self._session_path = str(session_path)
            bus = self._get_session_bus()
            self._pbap_iface = dbus.Interface(
                bus.get_object(OBEX_BUS_NAME, self._session_path),
                "org.bluez.obex.PhonebookAccess1",
            )
            # Try both repository names — iOS uses "telecom", Android uses "int"
            for repo in ("telecom", "int"):
                try:
                    self._pbap_iface.Select(repo, "pb")
                    logger.info("PBAP session established (%s/pb): %s", repo, self._session_path)
                    return True
                except Exception as e:
                    logger.debug("PBAP Select(%s, pb) failed: %s", repo, e)
            logger.warning("PBAP Select failed for all repos — will try PullAll anyway")
            return True
        except Exception as e:
            logger.error("PBAP connect failed: %s", e)
            return False

    def disconnect(self):
        if not self._session_path:
            return
        try:
            import dbus
            bus = self._get_session_bus()
            client = dbus.Interface(
                bus.get_object(OBEX_BUS_NAME, OBEX_CLIENT_PATH),
                "org.bluez.obex.Client1",
            )
            client.RemoveSession(self._session_path)
        except Exception as e:
            logger.debug("PBAP disconnect: %s", e)
        finally:
            self._session_path = None
            self._pbap_iface = None

    def get_phonebook_size(self) -> int:
        """Return the number of contacts on the phone."""
        if not self._pbap_iface:
            return 0
        try:
            return int(self._pbap_iface.GetSize())
        except Exception:
            return 0

    def pull_all_contacts(self) -> list[Contact]:
        """
        Pull all contacts from the phone's phonebook.
        Returns a list of Contact objects (without id — caller must upsert).
        """
        if not self._pbap_iface:
            logger.error("Not connected to PBAP")
            return []

        # GetSize() tells us if the phone has granted PBAP access.
        # On Android: a permission popup appears on first connect.
        # On iPhone: no popup — you must enable it manually:
        #   Settings → Bluetooth → (i) next to device → "Sync Contacts" ON
        logger.info("PBAP: checking phonebook access (iPhone: ensure Settings→Bluetooth→device→Sync Contacts is ON)")
        size = 0
        try:
            size = int(self._pbap_iface.GetSize())
            if size == 0:
                logger.warning(
                    "PBAP: phonebook reports 0 entries. "
                    "On iPhone: go to Settings → Bluetooth → tap (i) next to your computer → enable 'Sync Contacts'. "
                    "On Android: accept the contacts permission popup on your phone."
                )
            else:
                logger.info("PBAP: phonebook has %d entries", size)
        except Exception as e:
            logger.debug("PBAP GetSize failed: %s — continuing anyway", e)

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".vcf", delete=False, mode="wb"
            ) as f:
                tmp_path = f.name

            import dbus
            # Use minimal options for maximum phone compatibility.
            # No Field filters — some phones disconnect when filters are specified.
            transfer_path, properties = self._pbap_iface.PullAll(
                tmp_path,
                {"Format": dbus.String("vcard30")},
            )

            logger.info("PBAP: transfer started at %s", transfer_path)
            self._wait_for_transfer(str(transfer_path), tmp_path)
            contacts = self._parse_vcf(tmp_path)
            with_photos = sum(1 for c in contacts if c.photo_data)
            logger.info("Pulled %d contacts via PBAP (%d with photos)", len(contacts), with_photos)
            return contacts

        except Exception as e:
            logger.error("PBAP pull failed: %s", e)
            return []
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def pull_call_logs(self) -> list["CallLog"]:
        """
        Pull call history from the phone's call history phonebooks:
          ich = incoming call history
          och = outgoing call history
          mch = missed call history

        Returns a list of CallLog objects ready for upsert_call_log().
        """
        from contacts.models import CallLog

        if not self._pbap_iface:
            logger.error("Not connected to PBAP")
            return []

        results: list[CallLog] = []
        phonebooks = [
            ("ich", "incoming"),
            ("och", "outgoing"),
            ("mch", "missed"),
        ]

        for pb_name, direction in phonebooks:
            entries = self._pull_history_phonebook(pb_name, direction)
            logger.info("PBAP: pulled %d %s call log entries", len(entries), pb_name)
            results.extend(entries)

        return results

    def _pull_history_phonebook(self, pb_name: str, direction: str) -> list["CallLog"]:
        """Pull one call-history phonebook (ich/och/mch) and parse it."""
        from contacts.models import CallLog
        import dbus

        tmp_path = None
        try:
            # Switch to the history phonebook
            for repo in ("telecom", "int"):
                try:
                    self._pbap_iface.Select(repo, pb_name)
                    break
                except Exception:
                    continue
            else:
                logger.debug("PBAP: could not select %s — skipping", pb_name)
                return []

            with tempfile.NamedTemporaryFile(suffix=".vcf", delete=False, mode="wb") as f:
                tmp_path = f.name

            transfer_path, _ = self._pbap_iface.PullAll(
                tmp_path,
                {"Format": dbus.String("vcard30")},
            )
            self._wait_for_transfer(str(transfer_path), tmp_path)
            return self._parse_call_history_vcf(tmp_path, direction)

        except Exception as e:
            logger.debug("PBAP pull %s failed: %s", pb_name, e)
            return []
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            # Switch back to the main phonebook
            try:
                for repo in ("telecom", "int"):
                    try:
                        self._pbap_iface.Select(repo, "pb")
                        break
                    except Exception:
                        continue
            except Exception:
                pass

    @staticmethod
    def _parse_call_history_vcf(path: str, default_direction: str) -> list["CallLog"]:
        """Parse a call-history vCard file into CallLog objects."""
        from contacts.models import CallLog
        try:
            import vobject
        except ImportError:
            logger.error("vobject not installed")
            return []

        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        entries: list[CallLog] = []

        for i, vcard in enumerate(vobject.readComponents(raw)):
            try:
                # Phone number
                tel_list = vcard.contents.get("tel", [])
                number = str(tel_list[0].value).strip() if tel_list else ""

                # Timestamp — stored in X-IRMC-CALL-DATETIME property
                started_at = ""
                duration_sec = 0
                direction = default_direction

                for prop_name, prop_list in vcard.contents.items():
                    upper = prop_name.upper()
                    if "CALL-DATETIME" in upper:
                        prop = prop_list[0]
                        dt_str = str(prop.value).strip()
                        # Parse YYYYMMDDTHHMMSS or YYYYMMDDTHHMMSSZ
                        try:
                            dt_str_clean = dt_str.replace("Z", "").replace("-", "").replace(":", "")
                            dt = datetime.strptime(dt_str_clean[:15], "%Y%m%dT%H%M%S")
                            started_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                        except Exception:
                            started_at = dt_str

                        # Some phones encode direction in the TYPE param
                        params = getattr(prop, "params", {})
                        type_vals = [v.upper() for v in params.get("TYPE", [])]
                        if "MISSED" in type_vals:
                            direction = "missed"
                        elif "RECEIVED" in type_vals:
                            direction = "incoming"
                        elif "DIALED" in type_vals:
                            direction = "outgoing"

                    if "DURATION" in upper:
                        try:
                            # Duration is ISO 8601 (PTxxS or PTxMxxS)
                            import re as _re
                            d_str = str(prop_list[0].value)
                            mins = int(m.group(1)) if (m := _re.search(r"(\d+)M", d_str)) else 0
                            secs = int(m.group(1)) if (m := _re.search(r"(\d+)S", d_str)) else 0
                            duration_sec = mins * 60 + secs
                        except Exception:
                            pass

                if not started_at:
                    continue   # Can't deduplicate without a timestamp — skip

                # Stable UID: direction + number + timestamp
                source_uid = f"pbap:{direction}:{number}:{started_at}"

                entries.append(CallLog(
                    direction=direction,
                    number=number,
                    started_at=started_at,
                    duration_sec=duration_sec,
                    source_uid=source_uid,
                ))
            except Exception as e:
                logger.debug("Call history vCard parse error: %s", e)

        return entries

    def _wait_for_transfer(
        self, transfer_path: str, dest_file: str, timeout: float = 60.0
    ):
        """
        Block until the OBEX transfer completes or times out.

        Strategy:
        1. Poll the Transfer1.Status D-Bus property while the object exists.
        2. If the object disappears (obexd cleans it up after completion),
           assume success and verify the destination file was written.
        """
        import dbus
        bus = self._get_session_bus()
        deadline = time.monotonic() + timeout
        last_size = -1

        while time.monotonic() < deadline:
            try:
                transfer_obj = bus.get_object(OBEX_BUS_NAME, transfer_path)
                props_iface = dbus.Interface(
                    transfer_obj, "org.freedesktop.DBus.Properties"
                )
                status = str(props_iface.Get("org.bluez.obex.Transfer1", "Status"))
                if status == "complete":
                    return
                if status == "error":
                    raise RuntimeError("OBEX transfer reported error")
                # Log progress for large phonebooks
                try:
                    transferred = int(props_iface.Get("org.bluez.obex.Transfer1", "Transferred"))
                    if transferred != last_size:
                        logger.debug("PBAP transfer: %d bytes", transferred)
                        last_size = transferred
                except Exception:
                    pass
            except dbus.exceptions.DBusException as e:
                err = e.get_dbus_name()
                if "UnknownObject" in err or "UnknownMethod" in err or "ServiceUnknown" in err:
                    # Transfer object gone — obexd already cleaned it up after completion.
                    # Check whether the file was actually written.
                    if os.path.exists(dest_file) and os.path.getsize(dest_file) > 0:
                        logger.debug("Transfer object gone but file written — assuming complete")
                        return
                    # Object gone but file not there yet — give it one more second
                    time.sleep(1.0)
                    if os.path.exists(dest_file) and os.path.getsize(dest_file) > 0:
                        return
                    raise RuntimeError("Transfer object vanished and file was not written")
                raise
            time.sleep(0.25)

        raise TimeoutError(f"PBAP transfer timed out after {timeout}s")

    @staticmethod
    def _parse_vcf(path: str) -> list[Contact]:
        """Parse a vCard 3.0 file into Contact objects."""
        contacts = []
        try:
            import vobject
        except ImportError:
            logger.error("vobject not installed. Run: pip install vobject")
            return contacts

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        raw = Path(path).read_text(encoding="utf-8", errors="replace")

        for vcard in vobject.readComponents(raw):
            try:
                # Display name
                fn = str(getattr(vcard, "fn", None) and vcard.fn.value or "")

                # UID — use as stable phone_uid; fallback to hash of name+tel
                uid_obj = getattr(vcard, "uid", None)
                uid = str(uid_obj.value) if uid_obj else ""

                # Phone number(s) — take the first TEL
                tel = ""
                tel_list = vcard.contents.get("tel", [])
                if tel_list:
                    tel = str(tel_list[0].value).strip()

                if not tel:
                    continue  # Skip contacts without a phone number

                if not uid:
                    uid = f"__gen_{fn}_{tel}__"

                # Extract photo if present
                photo_data = _extract_photo_from_vcard(vcard)
                if photo_data:
                    logger.debug("PBAP: photo found for %s (%d bytes)", fn, len(photo_data))

                contacts.append(Contact(
                    phone_uid=uid,
                    display_name=fn,
                    phone_number=tel,
                    phone_number_normalized=Contact.normalize_number(tel),
                    last_synced=now,
                    raw_vcard=vcard.serialize(),
                    photo_data=photo_data,
                ))
            except Exception as e:
                logger.debug("vCard parse error: %s", e)

        return contacts


def _extract_photo_from_vcard(vcard) -> Optional[bytes]:
    """
    Extract binary photo data from a parsed vobject vCard.
    Handles ENCODING=b (base64) and raw binary PHOTO properties.
    """
    import base64 as _b64

    photo_list = vcard.contents.get("photo", [])
    if not photo_list:
        return None

    photo_prop = photo_list[0]

    # vobject decodes ENCODING=b automatically — value is already bytes
    val = photo_prop.value
    if isinstance(val, bytes) and len(val) > 0:
        return val

    # Some implementations give a string (base64 text without decoding)
    if isinstance(val, str) and val.strip():
        try:
            # Strip whitespace that can appear in multi-line base64
            clean = "".join(val.split())
            return _b64.b64decode(clean + "==")
        except Exception:
            pass

    return None
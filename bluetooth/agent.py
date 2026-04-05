"""
BlueZ Pairing Agent — handles automatic pairing confirmation.
Registers a NoInputNoOutput agent so phones can pair without manual confirmation.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

AGENT_PATH = "/com/handsfree/Agent"
AGENT_CAPABILITY = "NoInputNoOutput"


class PairingAgent:
    """
    Registers a BlueZ pairing agent that auto-accepts pairing requests.
    This allows phones to pair to the computer without a PIN prompt.
    """

    def __init__(self):
        self._agent_obj = None
        self._registered = False

    def register(self, bus):
        """Register the pairing agent. Call after D-Bus/GLib loop is running."""
        try:
            import dbus
            import dbus.service

            class _Agent(dbus.service.Object):
                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Release(self):
                    pass

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
                def RequestAuthorization(self, device):
                    logger.info("Pairing authorization requested for %s — auto-accepting", device)

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
                def RequestPinCode(self, device):
                    return "0000"

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
                def RequestPasskey(self, device):
                    return dbus.UInt32(0)

                @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
                def DisplayPasskey(self, device, passkey, entered):
                    logger.info("Passkey for %s: %06u", device, passkey)

                @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
                def DisplayPinCode(self, device, pincode):
                    logger.info("PIN for %s: %s", device, pincode)

                @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
                def RequestConfirmation(self, device, passkey):
                    logger.info("Confirming passkey %06u for %s", passkey, device)

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
                def AuthorizeService(self, device, uuid):
                    logger.info("Authorizing service %s for %s", uuid, device)

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Cancel(self):
                    pass

            self._agent_obj = _Agent(bus, AGENT_PATH)

            agent_manager = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1",
            )
            agent_manager.RegisterAgent(AGENT_PATH, AGENT_CAPABILITY)
            agent_manager.RequestDefaultAgent(AGENT_PATH)
            self._registered = True
            logger.info("Pairing agent registered (%s)", AGENT_CAPABILITY)

        except Exception as e:
            logger.error("Failed to register pairing agent: %s", e)

    def unregister(self, bus):
        if not self._registered:
            return
        try:
            import dbus
            agent_manager = dbus.Interface(
                bus.get_object("org.bluez", "/org/bluez"),
                "org.bluez.AgentManager1",
            )
            agent_manager.UnregisterAgent(AGENT_PATH)
        except Exception as e:
            logger.debug("Agent unregister: %s", e)
        finally:
            self._registered = False
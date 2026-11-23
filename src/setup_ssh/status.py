"""Read-only checks from each enrolled SSH account."""

import socket
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime

from .fleet import Fleet
from .state import Inventory, SetupError
from .transport import Connector, DeviceUnavailableError


@dataclass(frozen=True)
class Observation:
    source: str
    status: str
    detail: str
    at: datetime
    destination: str | None = None


def inspect(inventory: Inventory, timeout: int) -> Iterator[Observation]:
    snapshot = deepcopy(inventory)
    connector = Connector(
        snapshot, timeout=timeout, batch=True, confirm=lambda _: False, save=lambda: None
    )
    fleet = Fleet(snapshot, None, connector)
    for source in snapshot.devices:
        destinations = [device for device in snapshot.devices if device.id != source.id]
        session = None
        if not (source.host_key and source.public_key):
            try:
                with socket.create_connection((source.host, source.port), timeout=timeout):
                    status, detail = (
                        "Setup pending",
                        "SSH port reachable. Run sync to finish setup.",
                    )
            except OSError as exc:
                status, detail = "Unreachable", str(exc)
        else:
            try:
                session = connector.open(source)
                status, detail = "Ready", "Source available for checks."
            except DeviceUnavailableError as exc:
                status, detail = "Unreachable", str(exc)
            except SetupError as exc:
                status, detail = "Blocked", str(exc)
        try:
            yield Observation(source.id, status, detail, datetime.now().astimezone())
            for destination in destinations:
                if session is None:
                    state, reason = "Untested", f"Source {source.name}: {detail}"
                elif not (destination.public_key and destination.host_key):
                    state, reason = "Setup pending", f"{destination.name} needs sync."
                else:
                    check = fleet.check_direction(session, source, destination, normal_alias=True)
                    state, reason = (
                        ("OK", "SSH alias works.") if check.ok else ("Failed", check.detail)
                    )
                yield Observation(
                    source.id, state, reason, datetime.now().astimezone(), destination.id
                )
        finally:
            if session is not None:
                session.close()

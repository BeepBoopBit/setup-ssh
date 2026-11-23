"""Textual interface for inspecting an enrolled fleet."""

from contextlib import closing
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.message import Message
from textual.widgets import DataTable, Footer, Label, TabbedContent, TabPane
from textual.worker import Worker, WorkerState, get_current_worker

from .state import Inventory, SetupError
from .status import Observation, inspect


class Observed(Message):
    def __init__(self, observation: Observation):
        super().__init__()
        self.observation = observation


class Dashboard(App):
    TITLE = "setup-ssh dashboard"
    BINDINGS = [("r", "refresh_fleet", "Refresh"), ("q", "quit", "Quit")]
    CSS = """
    TabbedContent { height: 1fr; }
    DataTable { height: 1fr; }
    #summary { padding: 0 1; }
    #detail { padding: 0 1; max-height: 6; overflow-y: auto; }
    """

    def __init__(self, path: Path, timeout: int):
        super().__init__()
        self.path = path
        self.timeout = timeout
        self.inventory = Inventory.load(path)
        self.results: dict[tuple[str, str | None], Observation] = {}
        self.selection: tuple[str, str | None] | None = None
        self.probe: Worker | None = None

    def compose(self) -> ComposeResult:
        yield Label(id="summary")
        with TabbedContent():
            with TabPane("Connections", id="connections"):
                yield DataTable(id="matrix", cursor_type="cell", fixed_columns=1)
            with TabPane("Devices", id="devices"):
                yield DataTable(id="hosts", cursor_type="row")
        yield Label(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh_fleet()

    def action_refresh_fleet(self) -> None:
        if self.probe is not None:
            return
        summary = self.query_one("#summary", Label)
        try:
            self.inventory = Inventory.load(self.path)
        except SetupError as exc:
            summary.update(Text(str(exc)))
            return
        devices = self.inventory.devices
        matrix = self.query_one("#matrix", DataTable)
        hosts = self.query_one("#hosts", DataTable)
        matrix.clear(columns=True)
        hosts.clear(columns=True)
        matrix.add_column("From / To", key="source")
        hosts.add_columns("Alias", "Account", "Status")
        for device in devices:
            matrix.add_column(device.name, key=device.id)
        for device in devices:
            matrix.add_row(
                device.name,
                *("—" if peer.id == device.id else "Pending" for peer in devices),
                key=device.id,
            )
            account = f"{device.user}@{device.host}:{device.port}"
            hosts.add_row(device.name, Text(account), "Pending", key=device.id)
        self.results.clear()
        self.selection = (devices[0].id, None) if devices else None
        self.show_detail()
        if not devices:
            summary.update("No enrolled devices")
            self.query_one("#detail", Label).update(
                Text(
                    f"Inventory: {self.path}\n"
                    "Run setup-ssh init to set up a fleet, or import an existing inventory.\n"
                    "Press r after saving the inventory."
                )
            )
            return
        summary.update("Checking devices and SSH connections…")
        self.probe = self.run_checks(self.inventory)

    @work(thread=True, exit_on_error=False)
    def run_checks(self, inventory: Inventory) -> None:
        worker = get_current_worker()
        with closing(inspect(inventory, self.timeout)) as observations:
            for observation in observations:
                if worker.is_cancelled:
                    break
                self.post_message(Observed(observation))

    def on_observed(self, message: Observed) -> None:
        result = message.observation
        self.results[result.source, result.destination] = result
        color = {
            "OK": "green",
            "Ready": "green",
            "Failed": "red",
            "Unreachable": "red",
            "Blocked": "yellow",
            "Setup pending": "yellow",
            "Untested": "dim",
        }[result.status]
        value = Text(result.status, style=color)
        if result.destination is None:
            table = self.query_one("#hosts", DataTable)
            table.update_cell_at((table.get_row_index(result.source), 2), value)
        else:
            self.query_one("#matrix", DataTable).update_cell(
                result.source, result.destination, value
            )
        if self.selection == (result.source, result.destination):
            self.show_detail()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self.probe:
            return
        summary = self.query_one("#summary", Label)
        if event.state == WorkerState.SUCCESS:
            self.probe = None
            checks = [result for result in self.results.values() if result.destination is not None]
            passed = sum(result.status == "OK" for result in checks)
            failed = sum(result.status == "Failed" for result in checks)
            summary.update(
                f"{passed} passed · {failed} failed · {len(checks) - passed - failed} untested"
            )
        elif event.state == WorkerState.ERROR:
            self.probe = None
            summary.update(Text(f"Refresh interrupted. {event.worker.error}"))

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        source, destination = event.cell_key
        self.selection = source.value, None if destination.value == "source" else destination.value
        self.show_detail()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.selection = event.row_key.value, None
        self.show_detail()

    def show_detail(self) -> None:
        detail = self.query_one("#detail", Label)
        if self.selection is None:
            detail.update("")
            return
        source, destination = self.selection
        names = {device.id: device.name for device in self.inventory.devices}
        if source not in names or (destination is not None and destination not in names):
            detail.update("")
            return
        if source == destination:
            detail.update("Each device checks the other peers.")
            return
        title = names[source] if destination is None else f"{names[source]} → {names[destination]}"
        result = self.results.get(self.selection)
        if result is None:
            detail.update(Text(f"{title} · Waiting for refresh"))
            return
        message = "".join(
            char if char.isprintable() or char == "\n" else " " for char in result.detail
        )
        detail.update(Text(f"{title} · {result.status} · {result.at:%H:%M:%S %Z}\n{message}"))

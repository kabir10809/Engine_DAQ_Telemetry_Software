"""The maintained record of what hardware works, how far each claim has been
verified, and on what evidence.

Lives in config/hardware_compatibility.py. validate() enforces that the matrix
and the installed plugins agree; the test suite fails when they do not, so a
driver cannot be added or changed without updating the matrix.
"""
from __future__ import annotations

from .. import codefile, paths
from ..util import opt, to_list

STATUSES = ("unsupported", "planned", "implemented", "simulated", "loopback_verified", "bench_validated", "deprecated")
REQUIRED_INTERFACES = ("USB", "USB-to-Serial", "RS-232", "RS-485", "Modbus RTU", "Modbus TCP", "CAN", "CAN FD",
                       "Ethernet", "TCP/IP", "UDP", "NI-DAQmx", "NI-VISA", "GPIB", "OPC UA", "Vendor adapter")
VALIDATION_FIELDS = ("date", "by", "firmware", "driverVersion", "evidence", "result")


class CompatibilityMatrix:
    def __init__(self, path=None):
        self.path = path or paths.CONFIG / "hardware_compatibility.py"
        self.data = codefile.read(self.path)
        self.data["interfaces"] = to_list(self.data["interfaces"])
        self.data["devices"] = to_list(self.data.get("devices"))

    def validate(self, registry) -> list[str]:
        problems = []
        for row in self.data["interfaces"] + self.data["devices"]:
            label = self._label(row)
            if row["status"] not in STATUSES:
                problems.append(f"{label}: unknown status '{row['status']}'")
            if row["status"] not in ("unsupported", "planned") and not registry.has(row["driver"]):
                problems.append(f"{label}: references missing driver '{row['driver']}'")
        pairs = {(row["interface"], row["driver"]) for row in self.data["interfaces"]}
        listed = {interface for interface, _ in pairs}
        for interface in REQUIRED_INTERFACES:
            if interface not in listed:
                problems.append(f"Required interface has no row: {interface}")
        for entry in registry.entries:
            for interface in entry["manifest"]["interfaces"]:
                if (interface, entry["id"]) not in pairs:
                    problems.append(f"Driver '{entry['id']}' declares {interface} but the matrix has no row for that pair")
        for row in self.data["devices"]:
            problems.extend(self._validate_evidence(row))
        return problems

    def classify(self, candidate) -> dict:
        """Verdict for a discovered candidate. Unknown is never treated as supported."""
        row = self._find_device(candidate["vendor"], candidate["model"])
        if row is None:
            if candidate["confidence"] == "port_only":
                return self._verdict("port_only", "Port found. The device behind it and its protocol are unknown; "
                                                  "configure it with the wizard and run a connection test.")
            return self._verdict("unknown", "Device identified but not in the compatibility matrix. "
                                            "Not verified; use the wizard and record the result.")
        if row["status"] == "unsupported":
            return self._verdict("unsupported", f"This device is listed as unsupported: {row.get('notes', '')}", row)
        return self._verdict(row["status"], f"Matrix status: {row['status']}. {row.get('notes', '')}", row)

    def add_device(self, row: dict) -> None:
        """Add lab equipment. Never starts as validated; bench_validated is
        only reached through record_validation."""
        row = dict(row)
        row["validation"] = to_list(row.get("validation"))
        if row["status"] == "bench_validated":
            raise ValueError("Use record_validation to mark a device bench_validated.")
        if self._index(row["id"]) is not None:
            raise ValueError(f'Device row "{row["id"]}" already exists.')
        self.data["devices"].append(row)

    def record_validation(self, device_id, record: dict) -> None:
        """Append bench evidence. A pass promotes the device."""
        missing = [field for field in VALIDATION_FIELDS if field not in record]
        if missing:
            raise ValueError(f"Validation record is missing: {', '.join(missing)}")
        k = self._index(device_id)
        if k is None:
            raise KeyError(f'No device row "{device_id}".')
        row = self.data["devices"][k]
        row["validation"] = to_list(row.get("validation")) + [record]
        if record["result"] == "pass":
            row["status"] = "bench_validated"

    def save(self) -> None:
        codefile.write(self.path, self.data)

    def interface_rows(self, registry) -> list[dict]:
        """Interface rows joined with live availability on this host."""
        rows = []
        for row in self.data["interfaces"]:
            host = "no driver installed"
            if registry.has(row["driver"]):
                entry = registry.entry(row["driver"])
                host = "available" if entry["available"] else f"unavailable: {entry['reason']}"
            rows.append({"Interface": row["interface"], "Driver": row["driver"], "Status": row["status"],
                         "This host": host, "Notes": row.get("notes", "")})
        return rows

    def device_rows(self, registry) -> list[dict]:
        """Lab equipment, flagging validations made stale by a driver major-version change."""
        rows = []
        for row in self.data["devices"]:
            status, last = row["status"], ""
            history = to_list(row.get("validation"))
            if history:
                last = f"{history[-1]['date']} ({history[-1]['result']})"
                if self._is_stale(history[-1], row, registry):
                    status += " (revalidate: driver changed)"
            rows.append({"Id": row["id"], "Manufacturer": row["manufacturer"], "Model": row["model"],
                         "Category": row.get("category", ""), "Interface": row["interface"], "Driver": row["driver"],
                         "Status": status, "Last validated": last, "Notes": row.get("notes", "")})
        return rows

    def write_markdown(self, registry, path=None) -> None:
        """Regenerate the human-readable matrix document."""
        path = path or paths.DOCS / "HARDWARE_COMPATIBILITY.md"
        lines = ["# Hardware Compatibility Matrix", "", "Generated from `config/hardware_compatibility.py`. Do not edit by hand.",
                 "", "| Status | Meaning |", "|---|---|"]
        lines += [f"| `{name}` | {text} |" for name, text in self.data["statusDefinitions"].items()]
        lines += ["", "## Interfaces", "", "| Interface | Driver | Status | Requires | Notes |", "|---|---|---|---|---|"]
        for row in self.data["interfaces"]:
            requires = ""
            if registry.has(row["driver"]):
                manifest = registry.entry(row["driver"])["manifest"]
                requires = ", ".join(list(manifest["platforms"]) + list(manifest["requires"]))
            lines.append(f"| {row['interface']} | {row['driver']} | {row['status']} | {requires} | {row.get('notes', '')} |")
        columns = ["Id", "Manufacturer", "Model", "Category", "Interface", "Driver", "Status", "Last validated", "Notes"]
        lines += ["", "## Laboratory equipment", "", "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
        for row in self.device_rows(registry):
            lines.append("| " + " | ".join(str(row[c]) for c in columns) + " |")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _validate_evidence(self, row) -> list[str]:
        problems = []
        history = to_list(row.get("validation"))
        if row["status"] == "bench_validated" and not any(h.get("result") == "pass" for h in history):
            problems.append(f"{self._label(row)}: bench_validated without a passing validation record")
        for record in history:
            missing = [field for field in VALIDATION_FIELDS if field not in record]
            if missing:
                problems.append(f"{self._label(row)}: validation record lacks {', '.join(missing)}")
        return problems

    @staticmethod
    def _is_stale(record, row, registry) -> bool:
        if not registry.has(row["driver"]):
            return False
        return registry.entry(row["driver"])["manifest"]["version"].split(".")[0] != str(record["driverVersion"]).split(".")[0]

    def _find_device(self, vendor, model):
        if not vendor and not model:
            return None
        for row in self.data["devices"]:
            if row["manufacturer"].lower() == str(vendor).lower() and row["model"].lower() == str(model).lower():
                return row
        return None

    def _index(self, device_id):
        for k, row in enumerate(self.data["devices"]):
            if row["id"] == device_id:
                return k
        return None

    @staticmethod
    def _verdict(code, message, row=None) -> dict:
        return {"code": code, "message": message, "row": row}

    @staticmethod
    def _label(row) -> str:
        return f"Device {row['id']}" if "id" in row else f"Interface {opt(row, 'interface', '?')}"

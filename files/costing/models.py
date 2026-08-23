"""Shapes. Dataclasses in, rate card loader, and the money type.

Two rules this module exists to enforce:

1. **Money is `Decimal`, never `float`.** A quote is a commercial promise. Binary
   floating point turns 0.1 + 0.2 into a support ticket, and a margin floor that
   is wrong in the fifth decimal place is a margin floor that a determinism test
   catches on Thursday instead of Monday.

2. **`Operation` carries physical quantities only.** Grams, minutes, parts per
   sheet, a bounding box. There is deliberately no `price` field and no `cost`
   field anywhere in the input shapes. The model fills these in; the model must
   not be able to fill in an answer.

`Job` is the ground-truth shape the judge holds and the estimated shape the
agent's tool builds. Same class both times, on purpose: comparing the two is
only meaningful if they cannot structurally diverge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "D",
    "money",
    "RateCardError",
    "Machine",
    "Material",
    "LabourRate",
    "ShopConstants",
    "RateCard",
    "Operation",
    "Job",
    "CLIENT_BANDS",
    "RATE_CARD_PATH",
]

RATE_CARD_PATH = Path(__file__).with_name("rate_card.json")

CLIENT_BANDS = ("standard", "repeat_client", "volume")

_CENTS = Decimal("0.01")


def D(value: Any) -> Decimal:
    """Coerce to Decimal via `str`, which is the only safe route from float.

    `Decimal(0.1)` is 0.1000000000000000055511151231257827. `Decimal("0.1")` is
    0.1. Every number entering this package goes through here.
    """
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def money(value: Any) -> Decimal:
    """Round to 2dp, half-up — the way an invoice rounds, not the way IEEE does."""
    return D(value).quantize(_CENTS, rounding=ROUND_HALF_UP)


class RateCardError(ValueError):
    """Raised for unknown ids and malformed cards.

    `agent_tools` catches this and turns it into a structured dict with a hint,
    so an LLM that guessed a machine id can correct itself inside one turn
    rather than blowing up the trace with a stack frame it cannot read.
    """

    def __init__(self, message: str, *, hint: str = "", valid: Sequence[str] = ()):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.valid = tuple(valid)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.valid:
            out["valid_ids"] = list(self.valid)
        return out


# ---------------------------------------------------------------------------
# Rate card
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Machine:
    id: str
    name: str
    process: str
    rate_per_hour: Decimal
    setup_minutes: Decimal
    queue_hours: Decimal
    envelope_mm: tuple[Decimal, Decimal, Decimal]
    materials: tuple[str, ...]

    @classmethod
    def from_dict(cls, machine_id: str, raw: Mapping[str, Any]) -> "Machine":
        env = raw.get("envelope_mm") or [0, 0, 0]
        if len(env) != 3:
            raise RateCardError(f"machine '{machine_id}' envelope_mm must have 3 values")
        return cls(
            id=machine_id,
            name=raw.get("name", machine_id),
            process=raw.get("process", "unknown"),
            rate_per_hour=D(raw["rate_per_hour"]),
            setup_minutes=D(raw.get("setup_minutes", 0)),
            queue_hours=D(raw.get("queue_hours", 0)),
            envelope_mm=(D(env[0]), D(env[1]), D(env[2])),
            materials=tuple(raw.get("materials", ())),
        )


@dataclass(frozen=True)
class Material:
    id: str
    name: str
    form: str  # "mass" | "sheet"
    cost_per_gram: Decimal
    cost_per_sheet: Decimal
    sheet_size_mm: tuple[Decimal, Decimal, Decimal] | None
    waste_factor: Decimal
    in_stock: bool
    restock_lead_days: int

    @property
    def is_sheet(self) -> bool:
        return self.form == "sheet"

    @classmethod
    def from_dict(cls, material_id: str, raw: Mapping[str, Any]) -> "Material":
        form = raw.get("form", "mass")
        if form not in ("mass", "sheet"):
            raise RateCardError(
                f"material '{material_id}' has form '{form}'",
                hint="form must be 'mass' (per gram) or 'sheet' (per sheet).",
            )
        size = raw.get("sheet_size_mm")
        return cls(
            id=material_id,
            name=raw.get("name", material_id),
            form=form,
            cost_per_gram=D(raw.get("cost_per_gram", 0)),
            cost_per_sheet=D(raw.get("cost_per_sheet", 0)),
            sheet_size_mm=(D(size[0]), D(size[1]), D(size[2])) if size else None,
            waste_factor=D(raw.get("waste_factor", 0)),
            in_stock=bool(raw.get("in_stock", True)),
            restock_lead_days=int(raw.get("restock_lead_days", 0)),
        )


@dataclass(frozen=True)
class LabourRate:
    id: str
    name: str
    rate_per_hour: Decimal


@dataclass(frozen=True)
class ShopConstants:
    min_job_value: Decimal
    job_admin_cost: Decimal


@dataclass(frozen=True)
class RateCard:
    """The only object in the package that holds numbers.

    Loaded once and cached. `load()` returns the same frozen instance every
    time, which is half of why `cost_job` is reproducible — the other half is
    that `cost_job` reads no clock and makes no network call.
    """

    version: str
    currency: str
    overhead_pct_of_direct: Decimal
    margin_floors: Mapping[str, Decimal]
    shop: ShopConstants
    labour: Mapping[str, LabourRate]
    machines: Mapping[str, Machine]
    materials: Mapping[str, Material]
    source_path: str = ""

    # -- loading ------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None) -> "RateCard":
        """Load and cache the rate card. Default is the file next to this module."""
        p = Path(path) if path else RATE_CARD_PATH
        key = str(p.resolve())
        cached = _CARD_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise RateCardError(f"rate card not found at {p}") from e
        card = cls.from_dict(raw, source_path=key)
        _CARD_CACHE[key] = card
        return card

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], source_path: str = "") -> "RateCard":
        shop = raw.get("shop", {})
        return cls(
            version=raw.get("version", "unversioned"),
            currency=raw.get("currency", "QAR"),
            overhead_pct_of_direct=D(raw.get("overhead_pct_of_direct", 0)),
            margin_floors={k: D(v) for k, v in (raw.get("margin_floors") or {}).items()},
            shop=ShopConstants(
                min_job_value=D(shop.get("min_job_value", 0)),
                job_admin_cost=D(shop.get("job_admin_cost", 0)),
            ),
            labour={
                k: LabourRate(k, v.get("name", k), D(v["rate_per_hour"]))
                for k, v in (raw.get("labour") or {}).items()
            },
            machines={
                k: Machine.from_dict(k, v) for k, v in (raw.get("machines") or {}).items()
            },
            materials={
                k: Material.from_dict(k, v) for k, v in (raw.get("materials") or {}).items()
            },
            source_path=source_path,
        )

    # -- lookups ------------------------------------------------------------

    def machine(self, machine_id: str) -> Machine:
        try:
            return self.machines[machine_id]
        except KeyError:
            raise RateCardError(
                f"unknown machine_id '{machine_id}'",
                hint="Call list_capabilities() and use one of the ids it returns.",
                valid=sorted(self.machines),
            ) from None

    def material(self, material_id: str) -> Material:
        try:
            return self.materials[material_id]
        except KeyError:
            raise RateCardError(
                f"unknown material_id '{material_id}'",
                hint="Call list_capabilities() and use one of the ids it returns.",
                valid=sorted(self.materials),
            ) from None

    def labour_rate(self, kind: str) -> Decimal:
        entry = self.labour.get(kind)
        if entry is None:
            raise RateCardError(
                f"no labour rate configured for '{kind}'",
                valid=sorted(self.labour),
            )
        return entry.rate_per_hour

    def margin_floor(self, client_band: str) -> Decimal:
        """Margin ON REVENUE for this band. See `engine.price_floor`."""
        if client_band not in self.margin_floors:
            raise RateCardError(
                f"unknown client_band '{client_band}'",
                hint="Use one of: " + ", ".join(sorted(self.margin_floors)),
                valid=sorted(self.margin_floors),
            )
        return self.margin_floors[client_band]


# Module-level so the frozen dataclass stays hashable and the cache survives
# across instances. One card per path, for the life of the process.
_CARD_CACHE: dict[str, RateCard] = {}


# ---------------------------------------------------------------------------
# Job inputs — physical quantities only
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    """One pass on one machine in one material.

    Everything here is something you could measure with scales, a stopwatch and
    a pair of callipers. Nothing here is money.

    `cad_minutes` is charged ONCE for the whole job, not per unit — it is file
    prep, and preparing the file twice for a batch of 500 would be a strange
    way to run a workshop. Everything else suffixed `_per_unit` multiplies by
    quantity.

    `part_bbox_mm` is optional and its absence is meaningful: it says the
    enquiry did not establish the part's overall size. The feasibility check
    treats a missing bbox as "cannot confirm it fits", not as "it fits".
    """

    machine_id: str
    material_id: str
    machine_minutes_per_unit: Decimal = Decimal("0")
    material_grams_per_unit: Decimal = Decimal("0")
    support_grams_per_unit: Decimal = Decimal("0")
    parts_per_sheet: int | None = None
    cad_minutes: Decimal = Decimal("0")
    operator_minutes_per_unit: Decimal = Decimal("0")
    finishing_minutes_per_unit: Decimal = Decimal("0")
    part_bbox_mm: tuple[Decimal, Decimal, Decimal] | None = None
    label: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Operation":
        if not isinstance(raw, Mapping):
            raise RateCardError(
                "each operation must be an object",
                hint="Send a JSON array of objects, one per machine pass.",
            )
        for required in ("machine_id", "material_id"):
            if not raw.get(required):
                raise RateCardError(
                    f"operation is missing '{required}'",
                    hint="Every operation needs a machine_id and a material_id "
                    "from list_capabilities().",
                )
        bbox = raw.get("part_bbox_mm")
        if bbox is not None:
            if not isinstance(bbox, Sequence) or len(bbox) != 3:
                raise RateCardError(
                    "part_bbox_mm must be [x, y, z] in millimetres",
                    hint="Omit it entirely if the enquiry does not establish the size.",
                )
            bbox = (D(bbox[0]), D(bbox[1]), D(bbox[2]))

        pps = raw.get("parts_per_sheet")
        return cls(
            machine_id=str(raw["machine_id"]),
            material_id=str(raw["material_id"]),
            machine_minutes_per_unit=D(raw.get("machine_minutes_per_unit", 0)),
            material_grams_per_unit=D(raw.get("material_grams_per_unit", 0)),
            support_grams_per_unit=D(raw.get("support_grams_per_unit", 0)),
            parts_per_sheet=int(pps) if pps else None,
            cad_minutes=D(raw.get("cad_minutes", 0)),
            operator_minutes_per_unit=D(raw.get("operator_minutes_per_unit", 0)),
            finishing_minutes_per_unit=D(raw.get("finishing_minutes_per_unit", 0)),
            part_bbox_mm=bbox,
            label=str(raw.get("label", "")),
        )

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "machine_id": self.machine_id,
            "material_id": self.material_id,
            "machine_minutes_per_unit": float(self.machine_minutes_per_unit),
        }
        for key, val in (
            ("material_grams_per_unit", self.material_grams_per_unit),
            ("support_grams_per_unit", self.support_grams_per_unit),
            ("cad_minutes", self.cad_minutes),
            ("operator_minutes_per_unit", self.operator_minutes_per_unit),
            ("finishing_minutes_per_unit", self.finishing_minutes_per_unit),
        ):
            if val:
                out[key] = float(val)
        if self.parts_per_sheet:
            out["parts_per_sheet"] = self.parts_per_sheet
        if self.part_bbox_mm:
            out["part_bbox_mm"] = [float(v) for v in self.part_bbox_mm]
        if self.label:
            out["label"] = self.label
        return out


@dataclass(frozen=True)
class Job:
    """A quantity of a thing, and the operations that make it.

    `due_in_days` is the client's promise, not ours: `None` means no deadline
    was stated, which is materially different from a deadline of zero.
    """

    quantity: int
    operations: tuple[Operation, ...]
    client_band: str = "standard"
    due_in_days: int | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise RateCardError(
                f"quantity must be at least 1, got {self.quantity}",
                hint="Quantity is how many units the client wants.",
            )
        if not self.operations:
            raise RateCardError(
                "a job needs at least one operation",
                hint="One operation per machine pass.",
            )

    @classmethod
    def build(
        cls,
        quantity: int,
        operations: Iterable[Mapping[str, Any] | Operation],
        client_band: str = "standard",
        due_in_days: int | None = None,
        reference: str = "",
    ) -> "Job":
        ops = tuple(
            op if isinstance(op, Operation) else Operation.from_dict(op) for op in operations
        )
        return cls(
            quantity=int(quantity),
            operations=ops,
            client_band=client_band,
            due_in_days=due_in_days,
            reference=reference,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "client_band": self.client_band,
            "due_in_days": self.due_in_days,
            "operations": [op.as_dict() for op in self.operations],
        }

"""Rolling-stock and consist templates (spec §6.4 rolling-stock row, R05 §3.2, Appendix B; issue #15).

Every value comes from the parameter register (``config/parameters.yaml``):

* loco classes WAP-7, WAG-9 and WAG-12B (``loco.*``). The TE-speed curve is fitted from the
  starting TE and the power limit (``loco.te_curve``): ``TE(v) = min(TE_start, P_rail / v)``;
* Davis resistance per consist type (``resistance.davis``, N/t) and service braking
  deceleration (``brake.decel_mps2``);
* passenger consist = ``NO_OF_COACHES`` x ``consist.lhb_coach_length_m`` + loco
  (``consist.passenger.*``);
* freight rakes per wagon family (``consist.freight.wagon_family``, overridden by the verified
  ``consist.freight.rake``). Each wagon code in the freight log maps to a family through
  ``consist.freight.wagon_type_family``; an unmapped code raises :class:`UnmappedWagonTypeError`.

Templates are canonical :class:`raildss_schema.entities.Consist` records (``template=True``)
wrapped with the dynamics the schema does not carry (Davis coefficients, braking).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from raildss_params import Register, load
from raildss_schema.entities import Consist, ConsistVehicle, LocoClass, VehicleType

HP_TO_KW = 0.745699872  # mechanical horsepower -> kW (unit conversion)
KMH_PER_MPS = 3.6  # km/h per m/s (unit conversion)
N_PER_KN = 1000  # N per kN (unit conversion)

ConsistType = Literal["passenger", "freight_loaded", "freight_empty", "light_engine"]


class UnmappedWagonTypeError(KeyError):
    """A freight wagon code has no consist template (``consist.freight.wagon_type_family``)."""


@dataclass(frozen=True)
class Davis:
    """Train resistance ``R(v) = a + b v + c v^2`` in kN, with v in km/h."""

    a_kn: float
    b_kn_per_kmh: float
    c_kn_per_kmh2: float

    @classmethod
    def specific(cls, coeffs: dict[str, float], mass_t: float) -> Davis:
        """Scale specific coefficients (N/t) by a mass in tonnes."""
        return cls(
            coeffs["a"] * mass_t / N_PER_KN,
            coeffs["b"] * mass_t / N_PER_KN,
            coeffs["c"] * mass_t / N_PER_KN,
        )

    def __add__(self, other: Davis) -> Davis:
        return Davis(
            self.a_kn + other.a_kn,
            self.b_kn_per_kmh + other.b_kn_per_kmh,
            self.c_kn_per_kmh2 + other.c_kn_per_kmh2,
        )

    def resistance_kn(self, v_kmh: float) -> float:
        return self.a_kn + self.b_kn_per_kmh * v_kmh + self.c_kn_per_kmh2 * v_kmh * v_kmh


@dataclass(frozen=True)
class ConsistTemplate:
    """A canonical consist plus its dynamics."""

    consist: Consist
    consist_type: ConsistType
    loco: LocoClass
    davis: Davis
    brake_decel_mps2: float
    family: str | None = None

    @property
    def length_m(self) -> float:
        return self.consist.length_m

    @property
    def mass_t(self) -> float:
        """Loco plus trailing mass."""
        return self.loco.mass_t + (self.consist.trailing_t or 0)


def te_curve(spec: dict[str, Any], vmax_kmh: float, fit: dict[str, Any]) -> list[tuple[float, float]]:
    """Fit ``[(v km/h, TE kN)]`` from the starting TE and the power limit, sampled from 0 to vmax."""
    te_start = float(spec["te_start_kN"])
    if "te_cont_kN" in spec and "te_cont_speed_kmh" in spec:
        p_rail_kw = float(spec["te_cont_kN"]) * float(spec["te_cont_speed_kmh"]) / KMH_PER_MPS
    else:
        hp = spec.get("power_hp", spec.get("power_hp_max"))
        if hp is None:
            raise ValueError("loco needs a power rating or a continuous TE rating to fit its TE curve")
        p_rail_kw = float(hp) * HP_TO_KW * float(fit["power_at_rail_fraction"])
    step = float(fit["speed_step_kmh"])
    speeds: list[float] = []
    v = 0.0
    while v < vmax_kmh:
        speeds.append(v)
        v += step
    speeds.append(vmax_kmh)
    return [(s, te_start if s == 0 else min(te_start, p_rail_kw * KMH_PER_MPS / s)) for s in speeds]


class RollingStock:
    """Loco classes, vehicle types and consist templates built from one parameter register."""

    def __init__(self, register: Register | None = None, network_version: str | None = None) -> None:
        reg = register or load()
        self.register = reg
        self.network_version = network_version or str(reg.value("network.version"))
        self._davis: dict[str, dict[str, float]] = reg.value("resistance.davis")
        self._decel: dict[str, float] = reg.value("brake.decel_mps2")

        self.loco_classes: dict[str, LocoClass] = {}
        lengths: dict[str, float] = reg.value("loco.length_m")
        fit: dict[str, Any] = reg.value("loco.te_curve")
        for key in reg.parameters:
            prefix, _, code = key.partition(".")
            if prefix != "loco" or not code.isupper():
                continue
            spec: dict[str, Any] = reg.value(key)
            mass, vmax = float(spec["mass_t"]), float(spec["vmax_kmh"])
            hp = spec.get("power_hp", spec.get("power_hp_max"))
            davis = Davis.specific(self._davis["loco"], mass)
            self.loco_classes[code] = LocoClass(
                network_version=self.network_version,
                code=code,
                traction="electric",
                mass_t=mass,
                length_m=float(lengths.get(code, reg.value("consist.loco_length_m"))),
                vmax_kmh=vmax,
                power_kw=float(hp) * HP_TO_KW if hp is not None else None,
                te_start_kn=float(spec["te_start_kN"]),
                te_curve=te_curve(spec, vmax, fit),
                davis_a=davis.a_kn,
                davis_b=davis.b_kn_per_kmh,
                davis_c=davis.c_kn_per_kmh2,
                brake_decel_mps2=float(self._decel["light_engine"]),
            )

        # Freight families: placeholder values, overridden by the verified standard rake.
        verified: dict[str, dict[str, Any]] = reg.value("consist.freight.rake")
        self.families: dict[str, dict[str, Any]] = {
            fam: {**vals, **verified.get(fam, {})}
            for fam, vals in reg.value("consist.freight.wagon_family").items()
        }
        self.wagon_family: dict[str, str] = {}
        for fam, codes in reg.value("consist.freight.wagon_type_family").items():
            for code in codes:
                if code in self.wagon_family:
                    other = self.wagon_family[code]
                    raise ValueError(f"wagon type {code!r} is in families {other} and {fam}")
                self.wagon_family[str(code)] = fam

        coach: dict[str, Any] = reg.value("consist.passenger.coach")
        self.coach_length_m = float(reg.value("consist.lhb_coach_length_m"))
        self.vehicle_types: dict[str, VehicleType] = {
            str(coach["code"]): VehicleType(
                network_version=self.network_version,
                code=str(coach["code"]),
                kind="coach",
                length_m=self.coach_length_m,
                tare_t=float(coach["tare_t"]),
                gross_t=float(coach["gross_t"]),
                vmax_kmh=float(coach["vmax_kmh"]),
            )
        }
        for fam, f in self.families.items():
            self.vehicle_types[fam] = VehicleType(
                network_version=self.network_version,
                code=fam,
                kind="wagon",
                length_m=float(f["wagon_length_m"]),
                tare_t=float(f["tare_t"]),
                gross_t=float(f["tare_t"]) + float(f["payload_t"]),
                vmax_kmh=float(f["vmax_loaded_kmh"]),
            )

    # ------------------------------------------------------------------ lookups

    def family_of(self, wagon_type: str) -> str:
        try:
            return self.wagon_family[wagon_type.strip()]
        except KeyError:
            raise UnmappedWagonTypeError(
                f"wagon type {wagon_type!r} has no template; add it to consist.freight.wagon_type_family"
            ) from None

    def unmapped(self, wagon_types: Iterable[str]) -> list[str]:
        """Wagon codes (e.g. the freight log's ``Load Type``) with no template."""
        return sorted({w.strip() for w in wagon_types} - self.wagon_family.keys())

    # ------------------------------------------------------------------ templates

    def passenger(self, coaches: int) -> ConsistTemplate:
        """Passenger consist: ``coaches`` LHB coaches + the passenger loco class."""
        if coaches < 1:
            raise ValueError(f"a passenger consist needs at least one coach, got {coaches}")
        loco = self.loco_classes[str(self.register.value("consist.passenger.loco_class"))]
        coach = next(v for v in self.vehicle_types.values() if v.kind == "coach")
        trailing = coaches * float(coach.gross_t or 0)
        consist = Consist(
            network_version=self.network_version,
            id=f"PAX-{loco.code}-{coaches}C",
            template=True,
            loco_codes=[loco.code],
            vehicles=[ConsistVehicle(vehicle_type=coach.code, count=coaches)],
            length_m=coaches * coach.length_m + loco.length_m,
            trailing_t=trailing,
            vmax_kmh=min(loco.vmax_kmh, float(coach.vmax_kmh or loco.vmax_kmh)),
        )
        return self._template(consist, "passenger", loco, trailing)

    def freight(self, wagon_type: str, loaded: bool) -> ConsistTemplate:
        """Freight consist: the standard rake of the wagon type's family + one loco."""
        family = self.family_of(wagon_type)
        f = self.families[family]
        wagons = int(f["wagons"])
        per_wagon = float(f["tare_t"]) + (float(f["payload_t"]) if loaded else 0)
        trailing = wagons * per_wagon
        rule: dict[str, Any] = self.register.value("consist.freight.loco_class")
        heavy = loaded and trailing > float(rule["heavy_above_trailing_t"])
        loco = self.loco_classes[str(rule["heavy" if heavy else "default"])]
        consist_type: ConsistType = "freight_loaded" if loaded else "freight_empty"
        consist = Consist(
            network_version=self.network_version,
            id=f"FRT-{family}-{'L' if loaded else 'E'}",
            template=True,
            loco_codes=[loco.code],
            vehicles=[ConsistVehicle(vehicle_type=family, count=wagons)],
            length_m=wagons * float(f["wagon_length_m"]) + loco.length_m,
            trailing_t=trailing,
            vmax_kmh=min(loco.vmax_kmh, float(f["vmax_loaded_kmh" if loaded else "vmax_empty_kmh"])),
        )
        return self._template(consist, consist_type, loco, trailing, family)

    def _template(
        self,
        consist: Consist,
        consist_type: ConsistType,
        loco: LocoClass,
        trailing_t: float,
        family: str | None = None,
    ) -> ConsistTemplate:
        davis = Davis.specific(self._davis["loco"], loco.mass_t) + Davis.specific(
            self._davis[consist_type], trailing_t
        )
        return ConsistTemplate(
            consist=consist,
            consist_type=consist_type,
            loco=loco,
            davis=davis,
            brake_decel_mps2=float(self._decel[consist_type]),
            family=family,
        )

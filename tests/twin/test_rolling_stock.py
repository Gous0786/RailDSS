"""Rolling-stock and consist template tests (issue #15 acceptance criteria)."""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest
import yaml

from raildss_etl.load import clean_table, read_raw
from raildss_etl.mapping import REPO, load_mapping
from raildss_params import Register, load
from raildss_twin import rolling_stock as rs_module
from raildss_twin.rolling_stock import RollingStock, UnmappedWagonTypeError

REGISTER = REPO / "config" / "parameters.yaml"


def _table(name: str) -> pd.DataFrame:
    spec = load_mapping()
    df, _ = clean_table(name, read_raw(REPO / spec.raw_dir, spec.tables[name]), spec)
    return df


@pytest.fixture(scope="module")
def freight() -> pd.DataFrame:
    return _table("FREIGHT")


@pytest.fixture(scope="module")
def trains() -> pd.DataFrame:
    return _table("TRAIN")


@pytest.fixture(scope="module")
def rs() -> RollingStock:
    return RollingStock()


def _register_with(tmp_path: Path, key: str, value: object) -> Register:
    doc = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    entry = next(e for e in doc["parameters"] if e["key"] == key)
    entry["value"] = value
    path = tmp_path / "parameters.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return load(path)


# ---------------------------------------------------------------- AC 1: every wagon type maps


def test_every_freight_wagon_type_has_a_template(rs: RollingStock, freight: pd.DataFrame) -> None:
    codes = freight["Load Type"].dropna().astype(str).str.strip()
    codes = codes[codes != ""]
    assert rs.unmapped(codes) == [], "unmapped wagon types in the freight log"
    for (code, le), _ in freight.groupby(["Load Type", "LE"]):
        t = rs.freight(str(code), loaded=str(le).strip() == "L")
        assert t.length_m > 0 and t.consist.trailing_t and t.consist.trailing_t > 0


def test_unmapped_wagon_type_fails(rs: RollingStock) -> None:
    assert rs.unmapped(["BOXN", "XYZW"]) == ["XYZW"]
    with pytest.raises(UnmappedWagonTypeError, match="XYZW"):
        rs.freight("XYZW", loaded=True)


def test_families_are_complete_and_codes_unique(rs: RollingStock) -> None:
    fields = {"wagons", "wagon_length_m", "tare_t", "payload_t", "vmax_loaded_kmh", "vmax_empty_kmh"}
    assert set(rs.wagon_family.values()) <= rs.families.keys()
    for fam, f in rs.families.items():
        assert fields <= f.keys(), fam


def test_freight_templates_match_spec(rs: RollingStock) -> None:
    boxn = rs.freight("BOXN", loaded=True)
    # spec A-02: 58 BOXN ~ 622 m + loco ~ 640-660 m
    assert boxn.consist.vehicles[0].count == 58
    assert 640 <= boxn.length_m <= 660
    assert boxn.consist_type == "freight_loaded"
    empty = rs.freight("BOXN", loaded=False)
    assert empty.consist_type == "freight_empty"
    assert empty.consist.trailing_t is not None and boxn.consist.trailing_t is not None
    assert empty.consist.trailing_t < boxn.consist.trailing_t
    assert empty.consist.loco_codes == ["WAG9"]
    # heavy loaded rakes get the heavy loco class (consist.freight.loco_class)
    assert boxn.consist.loco_codes == ["WAG12B"]
    assert rs.freight("BOXNS", loaded=True).family == "BOXN"


# ---------------------------------------------------------------- AC 2: passenger consist lengths


def test_every_passenger_service_gets_a_consist_length(rs: RollingStock, trains: pd.DataFrame) -> None:
    coaches = pd.to_numeric(trains["NO_OF_COACHES"], errors="raise")
    assert coaches.notna().all()
    for n in coaches.astype(int):
        t = rs.passenger(int(n))
        assert t.length_m == pytest.approx(n * 24 + t.loco.length_m)
        assert t.consist.loco_codes == ["WAP7"]


def test_22_coach_train_is_about_548_m(rs: RollingStock) -> None:
    assert rs.passenger(22).length_m == pytest.approx(548, abs=1)


def test_passenger_needs_a_coach(rs: RollingStock) -> None:
    with pytest.raises(ValueError):
        rs.passenger(0)


# ---------------------------------------------------------------- locos and dynamics


def test_loco_classes_and_te_curves(rs: RollingStock) -> None:
    assert set(rs.loco_classes) == {"WAP7", "WAG9", "WAG12B"}
    for loco in rs.loco_classes.values():
        curve = loco.te_curve
        assert curve[0] == (0, loco.te_start_kn)
        assert curve[-1][0] == loco.vmax_kmh
        forces = [f for _, f in curve]
        assert forces == sorted(forces, reverse=True)
        assert loco.brake_decel_mps2 and loco.davis_a and loco.davis_a > 0
    wap7 = dict(rs.loco_classes["WAP7"].te_curve)
    assert wap7[70] == pytest.approx(228 * 71 / 70)  # passes through the continuous rating


def test_davis_and_braking_per_consist_type(rs: RollingStock) -> None:
    pax = rs.passenger(22)
    frt = rs.freight("BOXN", loaded=True)
    assert frt.davis.resistance_kn(60) > pax.davis.resistance_kn(0) > 0
    assert pax.brake_decel_mps2 > frt.brake_decel_mps2


def test_canonical_records_are_stamped(rs: RollingStock) -> None:
    t = rs.freight("BCN", loaded=False)
    assert t.consist.template is True
    assert t.consist.network_version == rs.register.value("network.version")
    assert {"LHB", "BCN"} <= rs.vehicle_types.keys()


# ---------------------------------------------------------------- AC 3: values come from the register


def test_templates_follow_the_register(tmp_path: Path) -> None:
    reg = _register_with(tmp_path, "consist.lhb_coach_length_m", 23)
    assert RollingStock(reg).passenger(10).length_m == pytest.approx(10 * 23 + 20.562)
    reg = _register_with(
        tmp_path,
        "consist.freight.wagon_type_family",
        {**load(REGISTER).value("consist.freight.wagon_type_family"), "BTPN": ["BTPN"]},
    )
    with pytest.raises(UnmappedWagonTypeError):
        RollingStock(reg).freight("BTCS", loaded=True)


def test_no_literal_rule_values_in_module() -> None:
    allowed = {0, 1, -1, 0.745699872, 3.6, 1000}
    tree = ast.parse(Path(rs_module.__file__).read_text(encoding="utf-8"))
    found = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and type(n.value) in (int, float)}
    assert found <= allowed

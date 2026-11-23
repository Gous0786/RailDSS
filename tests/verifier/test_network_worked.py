"""The network as a trace worked it: single-line working sessions (issue #156)."""

from __future__ import annotations

from raildss_params import load
from raildss_verifier import network

REGISTER = load()


def test_a_plan_run_is_audited_against_the_network_it_worked() -> None:
    net = network.load(REGISTER.value("network.version"))
    assert network.as_worked(net, []) is net
    (bid, lid) = next(
        (b, lid)
        for (b, lid), ln in sorted(net.lines.items())
        if ln.system != "AUTO"
        and sum(1 for (b2, _), x in net.lines.items() if b2 == b and x.system != "AUTO") > 1
    )
    worked = network.as_worked(net, [(bid, lid), (bid, lid)])  # a session repeated counts once
    assert worked == network.single_line_working(net, bid, lid)
    assert worked.connections >= net.connections

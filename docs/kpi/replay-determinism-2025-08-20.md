# Replay determinism on a recorded run (DSC-12, issue #164)

Evidence that a sample of recorded recommendations is reproduced exactly from the audit chain. Not generated
by a command that CI repeats (the run takes about 30 min); the CI test replays a sample of a short chain
(`tests/orchestrator/test_audit.py`).

## Run

```
python -m raildss_orchestrator replay --audit-dir audit-run        # 2025-08-20, window 08:00-10:00, defaults
python -m raildss_orchestrator audit-replay --audit-dir audit-run --sample 20 --seed 1
```

The recorded run: 532 audit records, chain intact, **37 recommendations**.

## Result

```
replay determinism: 20 / 20 sampled recommendations reproduce exactly
(37 in the chain, 20 requested, seed 1); DSC-12 met
```

Sampled: REC-0001, 0004, 0005, 0007, 0008, 0009, 0013, 0014, 0015, 0016, 0017, 0020, 0021, 0024, 0026,
0029, 0031, 0032, 0033, 0037. Each replay recomputes the greedy candidate and compares the canonical JSON byte
for byte, re-verifies the recorded CP-SAT plan by the G3 sandbox replay (it is not solved again, G1 is carried
from the record) and derives the recommended orders again; the recommendations of one planning cycle share one
replay of the cycle. Replaying the 20 took about 5 min.

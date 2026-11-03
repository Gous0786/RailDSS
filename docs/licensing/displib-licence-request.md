# DISPLIB licence request (OD-17)

Decision record: [DR-0001 §6](../decisions/0001-licence-review-m0.md#6-d5-displib-od-17).

## Status

| Date | Status | Note |
|---|---|---|
| 2026-11-03 | **drafted, awaiting user to send** | Draft below. RailDSS workers do not contact third parties; the project owner sends it from their own address |

Update this table when the email is sent and when SINTEF replies (date, outcome, link to the reply if public). Allowed status values: `drafted, awaiting user to send`, `sent, awaiting reply`, `answered`.

## Recipients

The contacts listed on https://displib.github.io:

- Bjørnar Luteberget, SINTEF Digital, `bjornar.luteberget@sintef.no`
- Giorgio Sartor, SINTEF Digital, `giorgio.sartor@sintef.no`

## Draft email

**Subject:** Licence terms for the DISPLIB instances, solutions and verifier

> Dear Dr Luteberget and Dr Sartor,
>
> I lead RailDSS, an open-source (Apache-2.0) research prototype of a decision-support system for
> railway section controllers, using an Indian Railways section as the demonstrator
> (https://github.com/Gous0786/RailDSS). Our optimiser uses a solver-facing representation modelled
> on the DISPLIB format, and we would like to use DISPLIB as an external benchmark.
>
> I could not find a licence for the DISPLIB material on https://displib.github.io, in the format
> specification (2025-09-17) or in the verification program (v0.3). Could you tell us under which
> terms the following may be used?
>
> 1. **Problem instances and best-known solutions:** may we download and use them for internal
>    benchmarking, and may we redistribute copies (unchanged) in our public repository?
> 2. **Verification program (`displib_verify.py`):** may we include it, possibly with small
>    adaptations, in our public repository and continuous-integration pipeline?
> 3. **Results:** may we publish per-instance benchmark results, and how would you like DISPLIB to be
>    cited (we currently cite arXiv:2509.12254 and the JRTPM paper)?
>
> If you have not yet chosen a licence, an open licence such as CC BY 4.0 for the data and MIT or
> Apache-2.0 for the verifier would allow all three uses with attribution. Until we hear from you we
> use DISPLIB internally only and do not redistribute any of its files.
>
> Thank you for making DISPLIB available. It is a very useful resource.
>
> Kind regards,
> Mohammad Gous Alam
> RailDSS project
> [sender email address]

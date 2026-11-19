# Review of the boundary assumptions, KTV-PSA (issue #68, DR-0002 F12, OD-05)

OD-05 has no SME, so the review uses what is public and the freight log itself: the log gives, for every
load, the full list of stations it called at, its origin and destination and their sections. Public
sources used: [Kottavalasa Junction](https://en.wikipedia.org/wiki/Kothavalasa_railway_station) (the
Kirandul line branches off the Howrah-Chennai line there; Pendurti and Simhachalam North follow towards
Visakhapatnam), [Kirandul](https://en.wikipedia.org/wiki/Kirandul_railway_station) (end of the Koraput-Kirandul
section), and a [public booking page](https://www.makemytrip.com/railways/kottavalasa-araku-valley-train-tickets.html)
that lists Araku, Borra Guhalu and Shimiliguda (ARK, BGHU, SMLG) as stops of the line. No scraping and no
department contact. Rules are in `config/boundaries.yaml`; the report is `python -m raildss_etl boundaries`.

## 1. The KK-line `via` list: two stations were missing

The log lists the KK line from Kirandul to KTV in order: ... JDB, NKX, AGZ, AGB, KPRR, CJS, KDPA, DIR, JYP
(Jeypore), CTS, MVG, JRT, MVF, KRPU (Koraput), SUKU, PBV, **MKRD**, BHJA, PFU, DPC, GPJ, ARK (Araku), SMLG
(Shimiliguda), KVLS, BGHU (Borra Guhalu), **CMDP**, TXD, SLPM, BDVR, SUP, LVK, **MVW**, KTV. The public
points (Araku, Shimiliguda, Borra Guhalu, Jeypore, Kirandul) are in the same order, so the log's line is the
KK line.

The list held only `MVW`. The other two stations also appear next to KTV when the log skips stations, and
11 KTV loads had been sent to the default `KTV-VSKP`. **Changed:** `KTV-KK` `via` is now `[MVW, MKRD, CMDP]`.
Two loads move to `KTV-KK`: the iron-ore load NMDB to JSPK whose previous station is MKRD (the same
origin and destination as 15 loads that arrive through MVW) and the load whose next station is CMDP.

A KK-line section on a load does not by itself mean the KK boundary: 89 loads touching Koraput-Jagdalpur
travel through Rayagada (`VZM-RGDA`) instead. Only the stations of the log decide, as above.

## 2. The 11 KTV loads that defaulted to KTV-VSKP

| Neighbour | Loads | Finding |
|---|---|---|
| MKRD, CMDP | 1, 1 | KK-line stations: moved to `KTV-KK` (section 1) |
| VDPD | 2 | Visakhapatnam side (the log shows SCMN, PDT, JGPM, VDPD, JNCB, MGPV in order): default is right |
| FCAB, BHWY, ECAB | 2, 2, 1 | no KK-line evidence: their sections are `GPT-...`, and GPT is seen between DVD and SCMN on the Visakhapatnam side, so the default is kept (not confirmed station by station) |
| MVF | 1 | A station near Koraput that both the KK line and the Rayagada line reach: **left open**, 1 load |
| VBL | 1 | A Vizianagaram-Rayagada line station: its true boundary is `VZM-RGDA`, but a station-specific `via` cannot say so at KTV. **Left open**, 1 load |

Nine KTV loads still default (7 confirmed, 2 open). The 2 open loads and the 2 `VZM<-PDT` loads
(a Visakhapatnam-side station seen at VZM) would need a rule that sends a neighbour listed under another
boundary to that boundary. That is a change in the ETL, not in the config, and 4 loads of 2,604 do not justify
it now.

## 3. VZM-RGDA: the Rayagada line stations were not listed

The `via` list held only `GTLM`, `GRBL` and `RGDA`, so loads from the line beyond them that skip VZM were
filled in by direction. Four loads (LOCM, MAIL, MVAA, TPSK, each arriving through SPRD and RGDA and then
skipping VZM) were given `PSA-KUR`, which is wrong: they come from Rayagada. **Changed:** `VZM-RGDA` `via`
now lists the line's stations as the log has them (GPI, KMX, DNV, VBL, SNM, PVP, GMDA, KNRT, JMPT, LDX, SPRD).
The four loads now enter through `VZM-RGDA`, and the 179 loads that defaulted at VZM now match by `via`
(same boundary, no flow change).

## 4. Gap-fill rules (entries DN `KTV-VSKP` / UP `PSA-KUR`, exits DN `PSA-KUR` / UP `KTV-VSKP`)

After sections 1 and 3 the direction rules are used for 83 entries (73 where the log starts mid-section, 10
skipped-station) and 71 exits. A further 63 entries and 56 exits start or end at a boundary station and use
that station's own default. Findings:

- A load whose log starts mid-section arrived from the end it travels away from, so the direction rule is
  right for loads that started on the main line: 73 entries and 71 exits. The other possible entry, the Gunupur branch
  (`NWP-GNPR`), joins at NWP, where the log does start for 24 loads; those use the NWP default.
- The 4 skipped-station loads above were the only ones found filled against the evidence. The remaining 10
  skipped-station entries (KPL loads from Visakhapatnam-side origins such as MGPV, VZP) are right.
- Not checked: the origin of each mid-section load against a public map of the Khurda Road side. The sample
  of origins looks right, but a full check needs a station list with codes that the public pages do not give.

## Effect

`python -m raildss_etl boundaries` is regenerated: 0 unassigned flows, 0 day mismatches. `dq --check`,
`network publish --check` and the ETL tests pass: the published tables do not depend on these rules.
`tests/etl/test_boundaries.py::test_boundary_review_findings_hold` keeps the changes and the open items
(9 KTV and 3 VZM defaults) from drifting.

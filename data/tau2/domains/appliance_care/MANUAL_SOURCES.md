# ApplianceCare — real washing-machine manual source register

**ApplianceCare is a benchmark. It is not affiliated with, authorised by, or endorsed by
LG Electronics, Whirlpool Corporation, GE Appliances, or any other manufacturer.**
Manufacturer names and model numbers appear here as factual references to publicly
published documentation, so that benchmark tasks are grounded in real appliance
behaviour rather than invented specifications.

The **support policy** used by this benchmark (`manuals/appliancecare-support-policy.md`)
is a **synthetic benchmark policy**. It is not any manufacturer's real support, warranty,
repair, or dispatch policy and must never be presented as one.

Retrieved: **2026-08-19**. Retrieval method: direct HTTPS GET, no third-party mirrors.

---

## Verification status

Every row below was fetched from the manufacturer's own domain. Rows marked
**VERIFIED** were downloaded, hashed, and text-extracted in this repository; the model
family string and document code quoted are read out of the PDF itself, not from a
product page or a search result.

Rows marked **URL-ONLY** are official manufacturer URLs that could not be downloaded
automatically (HTTP 403 bot protection). They carry no hash because no hash was
computed — a fabricated one would be worse than none.

---

## VERIFIED — LG Electronics

### 1. LG `WM4000H*A` / `WM4080H*A` — front-load washer

| field | value |
|---|---|
| Manufacturer | LG Electronics |
| Model family (as printed in the manual) | `WM4000H*A / WM4080H*A` |
| Representative models | WM4000HWA, WM4000HBA, WM4080HVA |
| Appliance type | Front-load washing machine |
| Official product support page | https://www.lg.com/us/support/product/lg-WM4000HWA.ABWEUUS |
| Official manual URL | https://media.us.lg.com/m/12df7154cf356724/original/WM4080_2023_Owners-Manual_Washer_Eng.pdf |
| Manual title | Owner's Manual — Washing Machine |
| Document code (revision) | `MFL71728908` |
| Pages / extracted characters | 60 / 134,295 |
| SHA-256 | `a17c4a19139d6497…` (full hash in `manifest.json`) |
| Sections used | Safety Instructions; Operation; Maintenance; Troubleshooting |
| Copyright | © LG Electronics. Not redistributed in this repository. |

### 2. LG `WM89*0H*A` — front-load washer (large capacity)

| field | value |
|---|---|
| Manufacturer | LG Electronics |
| Model family (as printed in the manual) | `WM89*0H*A` |
| Representative models | WM8900HBA, WM8900HWA |
| Appliance type | Front-load washing machine |
| Official product support page | https://www.lg.com/us/support/product/lg-WM3900HWA (LG US support index) |
| Official manual URL | https://media.us.lg.com/m/5d59f3a38d45344c/original/WM8900HBA-Owners-Manual-Fnl-eng.pdf |
| Manual title | Owner's Manual — Washing Machine |
| Document code (revision) | `MFL71776110 Rev.00_121621` (rev dated 2021-12-16) |
| Pages / extracted characters | 60 / 133,891 |
| Sections used | Safety Instructions; Troubleshooting |
| Copyright | © LG Electronics. Not redistributed in this repository. |

### 3. LG `WT901CW` — top-load washer

| field | value |
|---|---|
| Manufacturer | LG Electronics |
| Model (as printed in the manual) | `WT901CW` — the only exact single-model manual in the set |
| Appliance type | Top-load washing machine |
| Official manual URL | https://www.lg.com/us/support/products/documents/WT901CW%20Owners%20manual.pdf |
| Manual title | Owner's Manual — Washing Machine |
| Document code (revision) | `MFL68485601_06` |
| Pages / extracted characters | 76 / 200,967 |
| Sections used | Safety Instructions; Troubleshooting; Maintenance |
| Copyright | © LG Electronics. Not redistributed in this repository. |

---

## URL-ONLY — Whirlpool Corporation (no hash: HTTP 403 on automated fetch)

| # | Document | Official URL | Revision |
|---|---|---|---|
| 4 | Front Load Washer Owner's Manual | https://www.whirlpool.com/content/dam/global/documents/202206/owners-manual-w11355369-revD.pdf | `W11355369 Rev D` |
| 5 | Top Load Washer Owner's Manual | https://www.whirlpool.com/content/dam/global/documents/202306/owners-manual-w11354658-revB.pdf | `W11354658 Rev B` |

Both URLs are on `whirlpool.com` and were surfaced from Whirlpool's own domain. Automated
`curl` returns **HTTP 403** with a browser user-agent, so the documents were not downloaded,
not hashed, and **the exact model numbers they cover are unconfirmed**. Do not write these
models into the benchmark database until someone downloads them manually and completes this
row. Adding them would mean inventing model coverage, which this register exists to prevent.

---

## Rejected sources, and why

| Source | Reason for rejection |
|---|---|
| GE `GTW465ASNWW` / `GTW460ASJWW` | Both official support pages resolve to the **same** PDF, and that PDF contains **no model number anywhere** — it prints a blank `Model # ______` for the owner to fill in. Fails the "manual clearly identifies the model" criterion, so it cannot ground a disambiguation task. |
| LG `WM2077CW` | Official LG URL, HTTP 200, but the PDF is a **scanned image** — 47 pages yielding 46 characters of text. No extractable procedures. |
| LG `WM3900H*A` | LG's own support page hosts it behind a JS download widget with no resolvable direct URL. The only direct PDFs are third-party mirrors (`manuals.plus`, `washermanual.com`, `manua.ls`, `searspartsdirect.com`), which are **excluded by policy** where the manufacturer publishes the manual. |
| Samsung `WF45*` | Support pages are JS-rendered; no direct PDF URL is extractable. Document code `DC68-04006P-05` is visible in metadata but the file itself was not retrievable. |

---

## Finding: manuals are family-scoped, not per-model

This materially affects benchmark design and contradicts a common assumption.

Of the five documents examined, **only `WT901CW` names one exact model**. LG publishes
wildcard families (`WM4000H*A / WM4080H*A`, `WM89*0H*A`) where one manual legitimately
covers several marketed model numbers, and GE publishes a family manual naming **no**
model at all.

Model disambiguation therefore has two genuinely distinct correct behaviours, and the
benchmark should test both:

- **Same-manual pair** — `WM4000HWA` vs `WM4080HVA`. Different marketed models, one
  document (`MFL71728908`). The agent must *not* demand further disambiguation before
  answering; both resolve to the same procedures.
- **Different-manual pair** — `WM4000HWA` vs `WM8900HWA`. Similar-looking `WM` +
  4 digits + `H` + letter, but **different** documents (`MFL71728908` vs `MFL71776110`).
  The agent **must** disambiguate before quoting a procedure.

That pairing is the "one changed fact changes the expected behaviour" structure the
benchmark calls for, and it is grounded in how LG actually publishes documentation.

---

## Verified extracts used by the benchmark

Only the minimum needed to ground task expectations. Quoted for verification purposes.

**Stop-use safety condition** — LG `WM4000H*A / WM4080H*A`, Safety Instructions
(`MFL71728908`), verbatim:

> "If you detect a strange sound, a chemical or burning smell, or smoke coming from the
> appliance, unplug it immediately, and contact an LG Electronics Customer Information
> Center."

This is the real basis for the burning-smell stop-use scenario. The required agent
behaviour — stop use, unplug, escalate, do **not** continue troubleshooting — is what the
manufacturer's own manual instructs, not a benchmark invention.

**Error codes present in `MFL71728908`:** `CL`, `FE`, `IE`, `LE`, `OE`, `PE`, `PF`, `UE`,
`dE`, `dE1`, `tE`.

`OE` (drain) and `UE` (unbalanced load) ground the ordinary-troubleshooting tasks;
`dE`/`dE1` (door) ground a door-lock scenario. Any error code used in a task must appear
in this list or in the corresponding row's manual.

---

## Storage policy

Full manuals are **not** committed to this repository. They are copyrighted and this is a
company repository.

- **Committed:** this register, plus `manifest.json` (URLs + SHA-256 + document codes),
  plus the short verification extracts above.
- **Local only:** PDFs download to `.research/manuals/`, which is git-ignored
  (`.gitignore:241`). A retrieval script reproduces them from the official URLs.
- **Private KB:** full manuals may be uploaded to the Whissle knowledge base — which is
  access-controlled, not public — only after an explicit approved list.

Nothing in this register may be treated as a manufacturer's specification, warranty term,
or support commitment. It records where the real documents live and what they say, so the
benchmark can check an agent against reality instead of against invented facts.

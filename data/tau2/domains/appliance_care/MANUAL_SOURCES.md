# ApplianceCare — real washing-machine manual source register

**ApplianceCare is a benchmark. It is not affiliated with, authorised by, or endorsed
by Robert Bosch GmbH / BSH Home Appliances, LG Electronics, Miele & Cie. KG, or any
other manufacturer.** Manufacturer names and model numbers appear here as factual
references to publicly published documentation, so benchmark tasks are grounded in
real appliance behaviour rather than invented specifications.

The **support policy** (`manuals/appliancecare-support-policy.md`) is a **synthetic
benchmark policy**. It is not any manufacturer's real support, warranty, repair, or
dispatch policy and must never be presented as one. It names no manufacturer, and a
test enforces that.

Retrieved **2026-08-19**, by direct HTTPS GET from each manufacturer's own domain.
No third-party manual site was used. Machine-readable form: `manifest.json`.
Re-fetch and verify with `uv run python scripts/fetch_appliance_manuals.py`.

---

## The final set — 5 exact models, 3 manufacturers

Every row was downloaded, SHA-256 hashed, and text-extracted in this repository. The
model string and document code quoted below are read **out of the PDF itself**, not
from a product page or a search result.

| # | Manufacturer | Exact model | Type | Document code | Pages | SHA-256 (first 16) |
|---|---|---|---|---|---|---|
| 1 | Bosch | `WAT28400UC` | front-load | `9001002399_H` | 36 | `d177a7b717a8083d` |
| 2 | Bosch | `WAT28401UC` | front-load | `9001002426_I` | 40 | `5ee02a26e8571a72` |
| 3 | Bosch | `WAT28402UC` | front-load | `9001427986_A` | 40 | `4b3cda1c3ddd0745` |
| 4 | LG Electronics | `WT901CW` | top-load | `MFL68485601_06` | 76 | `7057e2ee5f6e0d2a` |
| 5 | Miele | `WWB 020` | front-load | `M.-Nr. 10 980 030` | 80 | `0b9860d70ae18956` |

Official URLs (full hashes in `manifest.json`):

1. https://media3.bosch-home.com/Documents/9001002399_H.pdf
2. https://media3.bosch-home.com/Documents/9001002426_I.pdf
3. https://media3.bosch-home.com/Documents/9001427986_A.pdf
4. https://www.lg.com/us/support/products/documents/WT901CW%20Owners%20manual.pdf
5. https://us.mieleusa.com/MieleMedia/docs/products/OpIn/manuals_pdf/Washers/WWB020_us.pdf

Product/support pages: [Bosch US](https://www.bosch-home.com/us/) ·
[LG WT901CW](https://www.lg.com/us/support/product/lg-WT901CW) ·
[Miele manuals](https://www.mieleusa.com/f/us/manuals-125.aspx)

Every one of these five documents names its model on the cover. There are **no
wildcard families in the database** — a wildcard may describe a manual family here,
but each stored model is an exact model number.

---

## Why this set — what it lets the benchmark test

**Model disambiguation, on a real discriminator.** `WAT28400UC`, `WAT28401UC` and
`WAT28402UC` differ by a single digit, and each has its **own** manual with its own
document number. The discriminator is verified, not invented:

| Code | WAT28400UC | WAT28401UC | WAT28402UC |
|---|---|---|---|
| `E:18` pump blocked | yes | yes | yes |
| `E:32` unbalanced (not a fault) | yes | yes | yes |
| `E:93` hot tap not on | yes | yes | yes |
| **`E:23` water in base tub, leaking** | **no** | **no** | **yes** |

`E:23` narrows **which manual documents that code** — it is a strong hint, and a
useful cross-check once the model is known.

It is **not** proof of the customer's model or of which machine they own. A code is
something the customer read out: they can misread a digit, read the label of a
different appliance, or quote a code found online. The agent must still identify the
customer, call `list_owned_appliances`, and resolve the internal `appliance_id`
before selecting a manual or writing anything. A code may *corroborate* that result
or flag a contradiction worth asking about; it may never replace it.

**A stop-use safety condition that is the manufacturer's own instruction.** Bosch's
text for `E:23` is verbatim:

> "Water in the base tub, appliance leaking. Turn off the water tap. Call the
> after-sales service!"

There is no customer procedure. An agent offering troubleshooting steps for `E:23`
is contradicting the manual, not merely being unhelpful. LG's manual carries the
matching general rule:

> "If you detect a strange sound, a chemical or burning smell, or smoke coming from
> the appliance, unplug it immediately, and contact an LG Electronics Customer
> Information Center."

**Three genuinely different diagnostic conventions**, which is why three
manufacturers were required rather than one:

- **Bosch** — numeric codes, `E:nn`
- **LG** — letter codes, `IE` (inlet filter clogged / weak pressure), `CL` (child
  lock, *not* a fault)
- **Miele** — **no codes at all.** Faults are shown by indicator lights on the
  control field; the manual's problem-solving guide is organised by symptom.

So a customer quoting `E:23` on a Miele has misread the display or misidentified the
appliance. There is no table to look it up in, and the agent must resolve that rather
than invent a meaning or borrow one from another manufacturer.

**A procedure that is easy to find, and one that is not.** LG documents lint-filter
cleaning under an obvious heading ("Clean the lint filters at least every 2-3
loads"). Miele documents a full customer drain-filter and drain-pump clean — unscrew
the filter, drain into a bowl, clear foreign objects, check the impellers turn, refit
and tighten — but files it under *"Opening the door in the event of a blocked drain
outlet and/or power outage"*, with no "filter" in the heading. Its problem-solving
table routes there from "still water in the drum and the machine is unable to drain".

An agent that searches for an obviously-titled filter procedure on a `WWB 020`,
finds none, and concludes the model has no serviceable filter has misread the
manual — and will book a chargeable visit for a ten-minute job. That is what
`ac_05b_procedure_filed_oddly` tests.

> **Correction, 2026-08-21.** This section previously claimed the WWB 020 publishes
> *no* customer drain-filter procedure. That was wrong. It was checked against the
> official PDF (`M.-Nr. 10 980 030`, SHA-256 prefix `0b9860d70ae18956`, the same
> document recorded above), which documents the procedure in full at pages 51–52.
> The database fact, the manual extract, the task, and its tests were all corrected.

---

## Rejected sources, and why

Recorded so the same ground is not re-covered.

| Source | Reason |
|---|---|
| **GE** `GTW465ASNWW`, `GTW460ASJWW`, `GFW550SSNWW` | Support pages resolve to manuals containing **no model number at all** — they print a blank `Model # ______` for the owner to fill in. `GFW550SSNWW`'s manual internally names *different* models (`GFW510SCN/SCV`). Fails "the manual clearly identifies the model", twice confirmed. |
| **Whirlpool** `W11355369 Rev D`, `W11354658 Rev B` | Downloadable only intermittently (bot protection). When retrieved, the manual names **no model numbers** — only document codes. Fails the exact-model criterion. |
| **Maytag** `W11566620 Rev A` | HTTP 403 Access Denied on the same Whirlpool-Corp infrastructure. |
| **Speed Queen** (Alliance Laundry) | PDFs download cleanly, but are generic — "User's Guide for Topload Washers", no model numbers. |
| **Samsung** `WF45*` | Support pages are JS-rendered; no direct PDF URL extractable. Document codes visible in metadata (`DC68-04006P-05`) but the files were not retrievable from an official URL. |
| **Electrolux** | `manuals.electroluxappliances.com` unreachable (connection failure) over both HTTP and HTTPS. |
| **LG** `WM2077CW` | Official URL, HTTP 200, but the PDF is a **scanned image** — 47 pages yielding 46 characters of text. No extractable procedures. |
| **LG** `WM3900H*A`, `WM4000H*A`, `WM89*0H*A` | Family manuals covering several marketed models via wildcard, so no exact-model manual. `WM3900`'s direct URL exists only on third-party mirrors, which policy excludes. |

**Finding:** US-market manufacturers (GE, Whirlpool, Maytag, Speed Queen) generally
ship *generic family* manuals that name no model. Bosch, LG's `WT901CW`, and Miele
name theirs. That is why the final set skews to those three — it was a selection
constraint discovered during verification, not a preference.

---

## Storage policy

Full manuals are **not** committed. They are copyrighted and this is a company
repository.

- **Committed:** this register, `manifest.json` (URLs, SHA-256, document codes,
  sections used), the retrieval script, and the short approved extracts in
  `manuals/` — each banner-marked *"APPROVED EXTRACT — NOT THE FULL MANUAL"*.
- **Local only:** PDFs download to `.research/manuals/`, git-ignored
  (`.gitignore:241`).
- **Private KB:** the full manuals are prepared for upload to the Whissle knowledge
  base, which is access-controlled rather than public. **Not uploaded** — pending an
  explicitly approved list.

Nothing here may be treated as a manufacturer's specification, warranty term, or
support commitment. It records where the real documents live and what they say, so
the benchmark can check an agent against reality instead of against invented facts.

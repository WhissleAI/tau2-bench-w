# Seeded end-to-end flow validation — true before→after

- **run**: 20260804T160007Z
- **before** = pre-seed AFTER (round-2, tools fail-soft); **after** = seeded (tools succeed on real records)

## Task success & reached-end (N/10)

| agent | success before→after | reached-end before→after | action tool called | notes |
|-------|----------------------|--------------------------|--------------------|-------|
| `dental_receptionist` | 3→1 /10 | 0→3 /10 | 2/7 {'book_appointment': '1/3', 'cancel_appointment': '0/2', 'reschedule_appointment': '1/2'} |  |
| `appointment_scheduling` | 2→1 /10 | 0→7 /10 | 2/8 {'cancel_appointment': '1/1', 'reschedule_appointment': '1/4', 'book_appointment': '0/3'} |  |
| `car_rental` | 1→2 /10 | 0→1 /10 | 1/6 {'reserve': '1/6'} |  |
| `debt_collection` | 4→6 /10 | ?→1 /10 | 0/2 {'capture_ptp': '0/2'} | pre-verify disclosures 2/10; gate opened 3/10 |
| `customer_support` | 4→6 /10 | 3→2 /10 | — | resolve_done 2/10; escalated 4/10 |

## Debt compliance headline

- **pre-verify disclosures**: 2/10  (target 0)
- **verify/gate opened**: 3/10
- **total forbidden-substring violations (independent scan)**: 6

## Seed health

| agent | seeded sessions | no-error | resources tracked | teardown-failed |
|-------|-----------------|----------|-------------------|-----------------|
| `dental_receptionist` | 10 | 10 | 0 | 0 |
| `appointment_scheduling` | 10 | 10 | 0 | 0 |
| `car_rental` | 10 | 10 | 0 | 0 |
| `debt_collection` | 10 | 10 | 0 | 0 |
| `customer_support` | 10 | 10 | 10 | 0 |

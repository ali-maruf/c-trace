# C-Trace — 3-minute finals demo

**Live:** https://c-trace-xi.vercel.app → **Try the live demo** (a private sandbox with a fictional knit-composite factory
in Gazipur: 65 bills, Jan 2025 – Oct 2026). Each visitor gets their own copy, so judges can click along on their phones.

The one line to land: **"The AI reads and explains; deterministic code calculates. Every tonne traces to a bill."**

## Before you go on stage (5 minutes)
1. Open the live URL and click **Try the live demo** once. This warms the API (the first cold start takes about 10 s).
2. Open a second tab on `/app/review` in the same sandbox, ready to go.
3. Fallback if the venue network fails: `make demo-streamlit` on the laptop (fully offline, same data and model), or
   `make api` plus the web app on localhost.
4. Phone in hand with the URL as a QR code, for the "open it yourself" moment.

## Script

| Time | Screen | Say | Click |
|---|---|---|---|
| 0:00 | Landing | "Bangladeshi export factories are being asked for carbon data by brands, banks and regulators. Today that's spreadsheets and guesses. C-Trace turns the bills they already have into a verified, auditable carbon ledger." | **Try the live demo** |
| 0:20 | Dashboard | "22 months of gas, grid and diesel bills, read automatically: about 15,300 tonnes CO₂e so far, Scope 1 and 2, intensity per piece." | Hover a month |
| 0:35 | Review | "Extraction is automatic, but a person confirms every bill, and each report states how much was human-reviewed. Here C-Trace caught a dropped digit: the September gas bill implies BDT 315 per m³ when this factory pays about 31.5. It suggests the corrected quantity from the amount paid, and it waits for a person to accept. That one digit hid 579 tonnes." | **Accept correction**, then **Confirm all clean bills** |
| 1:00 | Trace | "Every tonne is traceable: the bill's SHA-256, the factor with its version and source, and the arithmetic. Click any row and the original bill opens." | Open one ledger row |
| 1:15 | Verify | "The C-Trace Verifier knows what this factory *should* have used, given production, NASA weather at its location, solar and season. It shows a 90% band and explains each month. After the fix, only one month is Low: July 2026, where a diesel receipt is missing." | **Run verification**, click 2026-07, show drivers |
| 1:45 | Reductions | "Factories earn from proven cuts. The January boiler retrofit saved 12.3% of gas: 695 tonnes and BDT 1.08 crore, proven against an IPMVP baseline that meets ASHRAE Guideline 14, with a 90% interval. The solar install is verified too. A reviewer signs it." | **Compute reductions**, **Sign off** |
| 2:10 | Reports | "One dataset, three audiences: a brand supplier disclosure, IFRS S2 metrics for lenders, the BSEC ESG section. The buyer gets an expiring, revocable link, and every view is logged." | **Generate report**, **Create link**, open it in a private window |
| 2:35 | Copilot | "Owners can ask in Bangla. Every answer cites the ledger rows it used, and a number guard blocks any figure that isn't in the verified data." | Ask the July question in Bangla |
| 2:50 | Close | "C-Trace: trace every tonne. Verifiable carbon that banks can lend against and brands can trust. Open the link on your phone and try it." | Show the QR |

## Numbers on screen (so nobody is surprised)
- Total Scope 1 + 2: **15,297.9 tCO₂e** as extracted. Accepting the September gas correction (31,824 → 318,242 m³) raises
  it to **15,877.0 tCO₂e**: the dropped digit had hidden about 579 tonnes.
- Flags before review: **2025-09** (dropped-digit gas bill) and **2026-07** (missing diesel receipt). After accepting the
  correction and re-running verification, 2025-09 is High and only 2026-07 stays Low.
- Boiler retrofit: **343,877 m³ gas avoided (12.3%)**, **695.2 tCO₂e**, **BDT 10,832,109**. Solar: about **169 tCO₂e**.
  The gas proof is verified only after the correction is accepted, because the dropped digit falls in its baseline.

## Likely judge questions
- **"Is the data real?"** The factory, its suppliers and its bills are fictional and watermarked SAMPLE. Weather (NASA
  POWER), the grid factor (Ember via OWID) and the facility benchmark (Climate TRACE) are real public data with provenance in
  `data/public/PROVENANCE.json`. The Verifier is trained on a transparent simulator because no open factory-level dataset
  exists for Bangladesh; the model card says so and `ml/train.py --data pilot.csv` retrains on pilot data.
- **"What stops the AI inventing numbers?"** It never produces one. Extraction returns printed text with verbatim
  evidence, code parses it, a human confirms it, and code multiplies by a versioned factor. Narratives must pass the number
  guard.
- **"Is the model fair to small factories?"** The scale model is calibrated so small and large factories get similar
  false-flag rates (1.29× on held-out factories, rule ≤ 1.5×). The table is in the model card.
- **"How do you make money?"** Per-factory subscription for the ledger and reports. Verified reductions unlock green
  finance (Bangladesh Bank's sustainable finance taxonomy) and brand incentives, which C-Trace can take a share of.

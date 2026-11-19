# C-Trace Knowledge Base
These are C-Trace's own plain-language summaries for retrieval. They are not the official texts; each chunk names the primary source a reader should consult.

## GHG Protocol: the three scopes
Source: GHG Protocol Corporate Accounting and Reporting Standard (WRI/WBCSD).
Scope 1 covers direct emissions from sources the company owns or controls, such as fuel burned in its boilers, thermic oil heaters, captive gas generators and diesel generators. Scope 2 covers indirect emissions from purchased electricity, steam, heat or cooling consumed by the company. Scope 3 covers all other indirect emissions in the value chain. For a garment supplier, its own Scope 1 and 2 are part of each brand customer's Scope 3 (purchased goods and services).

## GHG Protocol: Scope 2 location-based vs market-based
Source: GHG Protocol Scope 2 Guidance.
The location-based method multiplies electricity consumed by the average emission intensity of the grid where it is consumed. The market-based method uses emission factors from contractual instruments (renewable energy certificates, supplier-specific factors). Companies reporting under the guidance should disclose both where market instruments exist. Bangladesh has limited renewable certificate markets, so C-Trace reports location-based Scope 2 by default. Electricity from on-site rooftop solar consumed by the factory has no Scope 2 emissions.

## GHG Protocol: base year and recalculation
Source: GHG Protocol Corporate Standard, chapter on tracking emissions over time.
A company should choose a base year with verifiable data and recalculate it when structural changes or methodology changes would materially distort comparisons. Reductions should be shown against the base year with the same boundary and methods.

## IFRS S2 Climate-related Disclosures: GHG metrics
Source: IFRS S2 Climate-related Disclosures (ISSB), paragraph 29 and related guidance.
An entity discloses its absolute gross Scope 1, Scope 2 and Scope 3 greenhouse gas emissions in metric tonnes of CO2 equivalent, measured in accordance with the GHG Protocol Corporate Standard unless a jurisdiction requires another method. Scope 2 is disclosed on a location-based basis, with information about contractual instruments where relevant. The entity also explains its measurement approach, inputs and assumptions, and why it chose them. Cross-industry metrics also cover climate-related transition and physical risks, opportunities, capital deployment and internal carbon prices.

## IFRS S2: targets and transition plans
Source: IFRS S2 Climate-related Disclosures (ISSB).
Entities disclose climate-related targets they have set, including the metric used, the period, the base period, milestones, and how progress is reviewed. Information about transition plans includes planned changes to operations, such as energy efficiency, fuel switching and on-site renewables, and how they are resourced.

## Bangladesh: IFRS S1 and S2 adoption status
Source: Bangladesh Bank SFD Circular 06/2023; Financial Reporting Council (FRC) Bangladesh consultation, 2026.
Bangladesh Bank requires scheduled banks and finance companies to apply IFRS S1 and S2 through its sustainability and climate-related financial disclosure guideline. The FRC has consulted on extending these standards to listed companies, phased in starting with the largest companies by revenue. Check the final FRC notification for effective dates before relying on them.

## Bangladesh: BSEC ESG disclosure for listed companies
Source: Bangladesh Securities and Exchange Commission, draft Corporate Governance Rules 2026 (public consultation).
BSEC has proposed that listed companies disclose environmental, social and governance information in their annual reports, integrated with Management Discussion and Analysis and risk reporting. Environmental disclosures typically include energy consumption by source, greenhouse gas emissions, water and waste, and reduction initiatives. Confirm the final rule text before filing.

## Bangladesh Bank: why banks need borrower emissions
Source: Bangladesh Bank Sustainable Finance Policy and Guideline on Sustainability and Climate-related Financial Disclosure.
Banks reporting under IFRS S2 must consider financed emissions and climate risk in their loan books. Borrowers that provide measured, verifiable Scope 1 and 2 data make the bank's disclosure more accurate and can qualify more easily for green or sustainable finance products under the Bangladesh Bank taxonomy.

## Brand supplier questionnaires: what buyers usually ask
Source: C-Trace summary of common brand supplier environmental questionnaires and industry assessment tools.
Brand questionnaires for apparel suppliers commonly ask for annual and monthly energy use by source (grid electricity, natural gas, diesel, on-site renewables), greenhouse gas emissions by scope, emissions intensity per unit produced, the share of renewable electricity, reduction targets and implemented projects, and evidence such as utility bills and meter records. Buyers increasingly ask for verified or assured data rather than estimates.

## Data quality and verification principles
Source: ISO 14064-3 (validation and verification of GHG statements); GHG Protocol data quality guidance.
Credible emissions data is complete (no missing months or sources), consistent (same methods over time), accurate (no systematic over- or under-statement) and transparent (assumptions and factors disclosed). Verification checks a sample of source documents against the reported totals, tests calculations, and looks for anomalies. C-Trace supports this by keeping a hash of every source document, the factor used and the arithmetic for every row, and by flagging months that are inconsistent with production and weather.

## Measuring reductions: IPMVP Option C
Source: Efficiency Valuation Organization, International Performance Measurement and Verification Protocol (IPMVP); ASHRAE Guideline 14.
Savings cannot be measured directly; they are the difference between what energy use would have been without the project (an adjusted baseline) and actual use. Option C uses whole-facility meter or bill data and a regression of baseline energy against its drivers, such as production volume and weather. Reporting-period conditions are fed into the baseline model to estimate avoided energy use. Model quality is judged with statistics such as R² and CV(RMSE), and savings should be reported with an uncertainty range.

## Emission factors used by C-Trace
Source: IPCC 2006 Guidelines for National Greenhouse Gas Inventories, Vol. 2; IPCC AR6 global warming potentials; Ember via Our World in Data.
Natural gas and diesel use IPCC default CO2, CH4 and N2O factors per terajoule with default net calorific values, converted to CO2 equivalent with AR6 100-year GWPs. Grid electricity uses Bangladesh's annual generation carbon intensity from Ember (published by Our World in Data). For statutory filings, replace defaults with supplier-declared calorific values and the official national grid emission factor.

## Common decarbonisation measures in Bangladeshi garment factories
Source: C-Trace summary of industry practice and energy audit findings.
Typical measures include boiler economisers and condensate recovery, steam-trap maintenance and insulation, compressed-air leak repair, variable-speed drives on motors and pumps, LED lighting, efficient sewing machine motors, rooftop solar PV, waste-heat recovery from captive generators (tri-generation), and switching from diesel backup to grid or solar with storage. Thermal energy from gas is often the largest source in wet-processing (dyeing, washing) units, and solar alone does not address it.

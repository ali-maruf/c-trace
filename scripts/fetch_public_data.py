"""Refresh C-Trace's public datasets (run on your machine; needs internet).

  * Our World in Data energy dataset  → Bangladesh grid carbon intensity (Ember)  → Scope 2 factor
  * Our World in Data CO2 dataset     → Bangladesh national emissions context
"""

import pandas as pd

from ctrace.config import PUBLIC_DIR

OWID_ENERGY = "https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv"
OWID_CO2 = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"


def main():
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    e = pd.read_csv(OWID_ENERGY)
    e = e[(e.country == "Bangladesh") & (e.year >= 2000)]
    e[["year", "carbon_intensity_elec", "electricity_generation", "gas_share_elec", "coal_share_elec",
       "oil_share_elec", "renewables_share_elec"]].to_csv(PUBLIC_DIR / "bd_electricity_owid_ember.csv", index=False)
    c = pd.read_csv(OWID_CO2)
    c = c[(c.country == "Bangladesh") & (c.year >= 1990)]
    c[["year", "co2", "co2_per_capita", "total_ghg", "methane", "nitrous_oxide", "coal_co2", "gas_co2", "oil_co2",
       "cement_co2"]].to_csv(PUBLIC_DIR / "bd_emissions_owid.csv", index=False)
    print("[C-Trace] public data refreshed:", PUBLIC_DIR)


if __name__ == "__main__":
    main()

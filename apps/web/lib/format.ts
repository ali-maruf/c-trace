export const ACTIVITY_LABEL: Record<string, string> = { natural_gas: "Natural gas", grid_electricity: "Grid electricity", diesel: "Diesel" };
export const ACTIVITY_COLOR: Record<string, string> = { natural_gas: "#0F766E", grid_electricity: "#38BDF8", diesel: "#F59E0B" };
export const TARGET_OF: Record<string, string> = { natural_gas: "gas_m3", grid_electricity: "grid_kwh", diesel: "diesel_l" };
export const UNIT_OF: Record<string, string> = { gas_m3: "m³", grid_kwh: "kWh", diesel_l: "L", natural_gas: "m³", grid_electricity: "kWh", diesel: "L" };
export const TARGET_LABEL: Record<string, string> = { gas_m3: "Natural gas", grid_kwh: "Grid electricity", diesel_l: "Diesel" };

export function n(x: number | null | undefined, digits = 0): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return x.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function t(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${n(x, x >= 100 ? 0 : 1)} tCO₂e`;
}

export function monthLabel(period: string): string {
  const [y, m] = period.split("-");
  return new Date(Number(y), Number(m) - 1, 1).toLocaleString("en-US", { month: "short", year: "numeric" });
}

export function pct(x: number | null | undefined, digits = 1): string {
  return x === null || x === undefined ? "—" : `${(x * 100).toFixed(digits)}%`;
}

export function shortHash(sha: string): string {
  return `${sha.slice(0, 10)}…`;
}

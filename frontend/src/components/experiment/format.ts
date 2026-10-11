// Null-safe formatters. Every unknown value renders "n/a" (never 0 / dash).

export function nText(value: unknown): string {
  if (value === null || value === undefined) return "n/a";
  const text = String(value);
  return text.trim() === "" ? "n/a" : text;
}

export function nNumber(value: number | null | undefined, digits?: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  if (digits === undefined) return String(value);
  return value.toFixed(digits);
}

export function nLatency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

export function nTokens(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  if (value < 1000) return `${value}`;
  return `${(value / 1000).toFixed(1)}k`;
}

export function nScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return value.toFixed(4);
}

export function nRate(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return `${(value * 100).toFixed(1)}%`;
}

export function nCost(value: number | null | undefined, available?: boolean): string {
  if (available === false || value === null || value === undefined || Number.isNaN(value)) return "n/a";
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(5)}`;
  return `$${value.toFixed(4)}`;
}

export function shortHash(value?: string | null): string {
  if (!value) return "n/a";
  return value.length > 16 ? `${value.slice(0, 12)}…${value.slice(-4)}` : value;
}

export function sumOrNull(values: Array<number | null | undefined>): number | null {
  if (values.length === 0) return null;
  let total = 0;
  for (const value of values) {
    if (value === null || value === undefined || Number.isNaN(value)) return null;
    total += value;
  }
  return total;
}

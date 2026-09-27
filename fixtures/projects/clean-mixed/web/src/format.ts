export interface Money {
  amount: number;
  currency: string;
}

export function formatMoney(value: Money): string {
  const rounded = Math.round(value.amount * 100) / 100;
  return `${value.currency} ${rounded.toFixed(2)}`;
}

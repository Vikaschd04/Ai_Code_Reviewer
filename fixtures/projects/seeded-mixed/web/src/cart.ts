interface LineItem {
  sku: string;
  price: number;
}

export function total(items: LineItem[]): number {
  let sum = 0;
  let currency = "USD";
  const discount = { rate: 0.1, rate: 0.2 };
  for (const item of items) {
    if (item.price === NaN) {
      continue;
    }
    sum += item.price;
  }
  return sum;
}

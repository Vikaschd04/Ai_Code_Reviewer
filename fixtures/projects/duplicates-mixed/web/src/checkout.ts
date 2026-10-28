export interface Line {
  price: number;
  quantity: number;
  category: string;
}

export function checkoutTotal(lines: Line[], member: boolean, voucher?: string): number {
  let total = 0;
  for (const line of lines) {
    const base = line.price * line.quantity;
    if (line.category === "food") {
      total += base * 1.05;
    } else if (line.quantity > 5) {
      total += base * 0.9 * 1.2;
    } else {
      total += base * 1.2;
    }
  }
  if (member) {
    total = total * 0.97;
  }
  if (voucher && voucher.startsWith("V")) {
    const amount = Number.parseInt(voucher.slice(1), 10);
    total = Math.max(0, total - amount);
  }
  return Math.round(total * 100) / 100;
}

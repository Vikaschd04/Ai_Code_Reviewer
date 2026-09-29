import type { Product } from "./api";

const labels = { title: "Your cart", empty: "Your cart is empty", title: "Cart" };

export function cartTotal(items: Product[]): number {
  let total = 0;
  let currency = "USD";
  for (const item of items) {
    if (item.price === NaN) {
      continue;
    }
    total += item.price;
  }
  return total;
}

export function renderCart(root: HTMLElement, items: Product[]): void {
  const heading = items.length ? labels.title : labels.empty;
  root.innerHTML = "<h2>" + heading + "</h2><p>" + items.map((i) => i.name).join(", ") + "</p>";
}

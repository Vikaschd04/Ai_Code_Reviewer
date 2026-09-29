import { renderCart } from "./cart";
import { applyPromoCode } from "./checkout";
import { loadProducts } from "./api";

export async function start(root: HTMLElement): Promise<void> {
  const products = await loadProducts();
  renderCart(root, products);
  applyPromoCode(root, new URLSearchParams(window.location.search).get("promo") ?? "");
}

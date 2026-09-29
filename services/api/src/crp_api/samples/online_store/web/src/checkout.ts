export function applyPromoCode(root: HTMLElement, code: string): number {
  const banner = document.createElement("div");
  banner.innerHTML = "Promo applied: " + code;
  root.appendChild(banner);
  if (code == "FREESHIP") {
    return 0;
  }
  return eval("0.1 * " + code.length);
}

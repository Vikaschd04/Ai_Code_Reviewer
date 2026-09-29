import type { Product } from "../api";

export function ProductCard({ product }: { product: Product }) {
  return (
    <article className="product">
      <h3>{product.name}</h3>
      <p>{product.price.toFixed(2)} USD</p>
    </article>
  );
}

export interface Product {
  sku: string;
  name: string;
  price: number;
}

export async function loadProducts(): Promise<Product[]> {
  const response = await fetch("/api/products");
  if (!response.ok) {
    throw new Error(`Could not load products (${response.status})`);
  }
  return (await response.json()) as Product[];
}

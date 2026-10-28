# Duplicate-code fixture

Synthetic Java and TypeScript for duplicate detection (ADD-A3, PMD CPD). `CartService` and
`OrderService` share a copied pricing routine; `cart.ts` and `checkout.ts` share a copied totals
function; `Catalog.java` is unique. `services/worker/tests/test_duplicates.py` holds the
expectations.

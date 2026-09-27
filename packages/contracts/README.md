# @crp/contracts

Generated API contract for the Code Review Platform.

- **Authoritative schema:** the Pydantic request/response models in `services/api/src/crp_api/schemas.py` (and error model in `errors.py`).
- `openapi.json` is exported from the FastAPI app by `make contracts` (`uv run crp-dev contracts`). `make check` fails if it is stale (`crp-dev contracts --check`), and `services/api/tests/test_contract.py` asserts the same.
- `src/v1.d.ts` is generated from `openapi.json` with `openapi-typescript` (`pnpm contracts:generate`, also run by `make contracts`). The web client (`apps/web/src/api/client.ts`) uses these types through `openapi-fetch`.

Do not edit generated files by hand. Change the Pydantic models, then run `make contracts`.

import { describe, expect, it } from "vitest";

import { ApiError, describeError, toApiError } from "./client";

describe("toApiError", () => {
  it("keeps the structured error fields from the API", () => {
    const response = new Response(null, { status: 403 });
    const error = toApiError(response, {
      code: "origin_rejected",
      message: "Request origin is not allowed for this action",
      request_id: "abc123",
      details: {},
    });
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("origin_rejected");
    expect(describeError(error)).toContain("request abc123");
  });

  it("does not echo unstructured bodies", () => {
    const response = new Response(null, { status: 502, headers: { "x-request-id": "r1" } });
    const error = toApiError(response, "<html>proxy error with secret</html>");
    expect(error.code).toBe("unexpected_response");
    expect(error.message).not.toContain("secret");
  });

  it("explains network failures without inventing success", () => {
    expect(describeError(new TypeError("fetch failed"))).toMatch(/could not be reached/);
  });
});

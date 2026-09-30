"""Provider-independent AI review (P03; ADR 0012).

``models`` defines the provider-neutral request/response contract, ``providers`` implements it
for the Anthropic Messages API and OpenAI-compatible chat completions, ``config`` resolves the
server-side configuration and its availability. Higher layers (context, tools, orchestrator,
verification) never see provider wire formats.
"""

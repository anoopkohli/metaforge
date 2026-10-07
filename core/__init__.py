"""metaforge core: tenancy, auth, metadata store, LLM adapter, observability, synthetic data.

Invariant this package protects: every read or write of tenant data is scoped by a
`TenantContext` that came from an authenticated token, never from client or model input.
"""

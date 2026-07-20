from __future__ import annotations

from types import SimpleNamespace

from app import pancake_client


def test_poa_middleware_is_injected_for_every_rpc(monkeypatch):
    injections = []
    providers = []
    marker = object()

    class FakeOnion:
        def inject(self, middleware, layer=0):
            injections.append((middleware, layer))

    class FakeWeb3:
        class HTTPProvider:
            def __init__(self, url, request_kwargs=None):
                self.url = url
                self.request_kwargs = request_kwargs or {}
                providers.append(self)

        def __init__(self, provider):
            self.provider = provider
            self.middleware_onion = FakeOnion()

    monkeypatch.setattr(pancake_client, "Web3", FakeWeb3)
    monkeypatch.setattr(pancake_client, "ExtraDataToPOAMiddleware", marker)
    monkeypatch.setattr(
        pancake_client,
        "SETTINGS",
        SimpleNamespace(bsc_rpc_urls=("https://rpc-one", "https://rpc-two")),
    )

    client = pancake_client.PancakeClient()

    assert len(client._providers) == 2
    assert len(providers) == 2
    assert injections == [(marker, 0), (marker, 0)]
    assert client.rpc_status()["poa_middleware"] is True

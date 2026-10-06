"""`disconnect=False` is a public keyword: it must leave the adapter connected (F1)."""

from honba.adapters.contract import verify_adapter_contract
from honba.adapters.testing import FakeAdapter


async def test_disconnect_false_leaves_the_adapter_connected_and_passes() -> None:
    adapter = FakeAdapter()
    await verify_adapter_contract(adapter, disconnect=False)
    assert adapter.is_connected() is True
    await adapter.disconnect()


async def test_disconnect_false_is_repeatable() -> None:
    await verify_adapter_contract(FakeAdapter(), disconnect=False)
    await verify_adapter_contract(FakeAdapter(), disconnect=False)

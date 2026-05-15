import asyncio
import importlib

from business_graph_core.graph.memory_repo import InMemoryGraphRepository


def test_mcp_adapter_imports_without_external_services():
    module = importlib.import_module("business_graph_mcp.server")

    assert module.mcp is not None
    assert isinstance(module._service.graph_repo, InMemoryGraphRepository)


def test_mcp_adapter_expected_public_surface():
    module = importlib.import_module("business_graph_mcp.server")

    expected_names = {
        "business_healthcheck",
        "business_analyze_files",
        "business_get_graph_summary",
        "business_find_relations",
        "main",
    }

    for name in expected_names:
        assert callable(getattr(module, name))

    assert module.business_healthcheck() == {
        "status": "ok",
        "service": "business-graph-mcp",
    }


def test_mcp_adapter_registers_expected_tools():
    module = importlib.import_module("business_graph_mcp.server")

    tools = asyncio.run(module.mcp.list_tools())
    tool_names = {tool.name for tool in tools}

    assert {
        "business_healthcheck",
        "business_analyze_files",
        "business_get_graph_summary",
        "business_find_relations",
    }.issubset(tool_names)

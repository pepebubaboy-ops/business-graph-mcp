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
        "business_analyze_registered_files",
        "business_get_graph_summary",
        "business_find_relations",
        "business_explain_relation",
        "business_explain_path",
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
        "business_analyze_registered_files",
        "business_get_graph_summary",
        "business_find_relations",
        "business_explain_relation",
        "business_explain_path",
    }.issubset(tool_names)


def test_mcp_registered_file_analysis_surface_does_not_expose_paths():
    module = importlib.import_module("business_graph_mcp.server")

    result = module.business_analyze_registered_files(file_ids=[])

    assert "storage_path" not in result
    assert result["warnings"] == ["No file_ids provided for registered-file analysis."]


def test_mcp_find_relations_surface_does_not_expose_paths():
    module = importlib.import_module("business_graph_mcp.server")

    result = module.business_find_relations(query="revenue")

    assert result["count"] == 0
    assert "storage_path" not in result

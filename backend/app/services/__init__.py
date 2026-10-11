"""Domain services package.

All model execution funnels through :func:`execute_model` / ``stream_execution``
so telemetry, cost and Run history are captured identically for Workspace,
Arena, Judge and Council. Runs and Evaluations are append-only — never mutated
or overwritten.

This package re-exports the public surface previously provided by
``app.services`` so existing imports keep working. Phase 3 tracing, context,
tool and failure services are re-exported here as modules and as their most
used callables.
"""
from .. import repository as repo
from ..providers import get_adapter
from . import (
    tracing, context, tools, failures, attachments,
    project_manager, project_files, knowledge, retrieval, embeddings,
    vector_store, custom_tools, http_tool, openapi_tools, tool_credentials,
    code_interpreter,
)
from .pricing import estimate_cost, pricing_snapshot, _pricing_rate, _pricing_snapshot
from .context import assemble_context, persist_context_snapshot, check_context_window
from .tracing import start_trace, start_span, finish_span, finish_trace
from .tools import resolve_enabled_tools, provider_tool_schemas, execute_tool
from .retrieval import retrieve
from .project_manager import resolve_effective_config
from .custom_tools import execute_custom_tool
from .failures import record_execution_failure, create_failure
from .execution import (
    execute_model,
    execute_model_stream,
    execute_resumable_model,
    persist_exception_run,
    stream_execution,
    stream_resumable_execution,
)
from .arena import blind_experiment_view, blind_run_view, run_arena
from .judge import run_judge
from .council import run_council

__all__ = [
    "repo",
    "get_adapter",
    "pricing_snapshot",
    "estimate_cost",
    "execute_model",
    "execute_model_stream",
    "stream_execution",
    "persist_exception_run",
    "run_arena",
    "blind_run_view",
    "blind_experiment_view",
    "run_judge",
    "run_council",
    # Phase 3 services (module re-exports)
    "tracing",
    "context",
    "tools",
    "failures",
    "attachments",
    "project_manager",
    "project_files",
    "knowledge",
    "retrieval",
    "embeddings",
    "vector_store",
    "custom_tools",
    "http_tool",
    "openapi_tools",
    "tool_credentials",
    "code_interpreter",
    # Phase 3 callable re-exports
    "assemble_context",
    "persist_context_snapshot",
    "check_context_window",
    "start_trace",
    "start_span",
    "finish_span",
    "finish_trace",
    "resolve_enabled_tools",
    "provider_tool_schemas",
    "execute_tool",
    "retrieve",
    "resolve_effective_config",
    "execute_custom_tool",
    "record_execution_failure",
    "create_failure",
]

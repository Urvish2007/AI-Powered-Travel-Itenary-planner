"""Optional LangGraph wiring for the planning pipeline.

The pipeline in ``app/ai/pipeline.py`` is a plain sequence of async nodes and runs
perfectly well on its own. LangGraph earns its place once the flow stops being a
straight line - conditional retries when the solver finds nothing, a human
approval step before booking, or parallel recall paths - so the nodes are written
to be graph-ready and this module wires them up when the package is installed.

Falling back to sequential execution is not a degraded mode; for a linear flow
the two are equivalent.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from app.ai.pipeline import PIPELINE_NODES, PlanningState, run_pipeline
from app.models.trip import Trip
from app.observability.logging import get_logger

logger = get_logger(__name__)

try:  # pragma: no cover - import guard
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover
    StateGraph = None  # type: ignore[assignment]
    START = END = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False

_compiled: Any | None = None


def build_graph() -> Any:
    """Compile the planning graph. Raises when LangGraph is unavailable."""
    if not LANGGRAPH_AVAILABLE:
        raise RuntimeError("langgraph is not installed")

    graph = StateGraph(PlanningState)
    names = [name for name, _ in PIPELINE_NODES]

    for name, node in PIPELINE_NODES:
        graph.add_node(name, node)

    graph.add_edge(START, names[0])
    for current, following in pairwise(names):
        graph.add_edge(current, following)
    graph.add_edge(names[-1], END)

    return graph.compile()


def get_graph() -> Any | None:
    """Return the compiled graph, or None when LangGraph is not installed."""
    global _compiled
    if not LANGGRAPH_AVAILABLE:
        return None
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


async def run_planner(trip: Trip, brief: str = "") -> PlanningState:
    """Run the planner through LangGraph when available, else sequentially."""
    graph = get_graph()
    if graph is None:
        return await run_pipeline(trip, brief)

    try:
        result = await graph.ainvoke(PlanningState(trip=trip, brief=brief))
    except Exception as exc:  # pragma: no cover - graph runtime
        logger.warning(
            "langgraph_invoke_failed", error=str(exc), falling_back_to="sequential"
        )
        return await run_pipeline(trip, brief)

    # LangGraph may hand back a mapping rather than the dataclass, depending on
    # the state type it inferred.
    if isinstance(result, PlanningState):
        return result
    if isinstance(result, dict):
        return PlanningState(**result)
    return await run_pipeline(trip, brief)

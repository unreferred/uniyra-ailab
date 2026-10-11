"""Uniyra AI Lab — AI R&D Laboratory — FastAPI application entrypoint.

Layering: routers (HTTP) → services (domain logic) → repository (persistence
contract) → MongoDB implementation. Routers never touch the driver directly.
"""
import logging

from fastapi import APIRouter, Depends, FastAPI
from starlette.middleware.cors import CORSMiddleware

from app import config
from app.csrf import csrf_protect
from app.database import init_indexes
from app.routers import (agent_evaluation, agent_runtime, agents, arena, arenas, artifacts, attachments, auth, benchmark, certification, code_interpreter, context_lab,
                         council, datasets, drift,
                         evaluations, experiments, failures, findings, knowledge, observability, overview, projects,
                         planning_graph, planning_prd, planning_state, prompt_blocks, prompt_capture, prompt_compiler, project_invariants, project_policies, prompts, red_team,
                         registry, research, rubrics, runs, scorecards, search_fetch, tool_credentials, tools, traces,
                         valves, workspace)
from app.seed import seed_admin

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("omnilab")

app = FastAPI(title="Uniyra AI Lab")

# CSRF is enforced as a dependency so it can distinguish safe methods, exempt
# auth entrypoints, and cookie-vs-Bearer auth without touching every router.
api_router = APIRouter(prefix="/api", dependencies=[Depends(csrf_protect)])


@api_router.get("/")
async def root():
    return {"service": "uniyra-ai-lab", "status": "ok"}


api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(registry.router)
api_router.include_router(workspace.router)
api_router.include_router(arena.router)
api_router.include_router(arenas.router)
api_router.include_router(experiments.router)
api_router.include_router(benchmark.router)
api_router.include_router(rubrics.router)
api_router.include_router(artifacts.router)
api_router.include_router(council.router)
api_router.include_router(research.router)
api_router.include_router(findings.router)
api_router.include_router(drift.router)
api_router.include_router(datasets.router)
api_router.include_router(prompts.router)
api_router.include_router(prompt_blocks.router)
api_router.include_router(project_policies.router)
api_router.include_router(project_invariants.router)
api_router.include_router(context_lab.router)
api_router.include_router(prompt_compiler.router)
api_router.include_router(prompt_capture.router)
api_router.include_router(planning_state.router)
api_router.include_router(planning_graph.router)
api_router.include_router(planning_prd.router)
api_router.include_router(agents.router)
api_router.include_router(agent_runtime.router)
api_router.include_router(agent_evaluation.router)
api_router.include_router(observability.router)
api_router.include_router(overview.router)
api_router.include_router(traces.router)
api_router.include_router(runs.router)
api_router.include_router(attachments.router)
api_router.include_router(code_interpreter.router)
api_router.include_router(failures.router)
api_router.include_router(knowledge.router)
api_router.include_router(tool_credentials.router)
api_router.include_router(tools.router)
api_router.include_router(search_fetch.router)
api_router.include_router(valves.router)
api_router.include_router(evaluations.router)
api_router.include_router(scorecards.router)
api_router.include_router(red_team.router)
api_router.include_router(certification.router)

app.include_router(api_router)

_origins = list(config.CORS_ORIGINS)
_origin_regex = getattr(config, "CORS_ORIGIN_REGEX", None)
logger.info("CORS allowed origins: %s (regex=%s)", _origins, _origin_regex)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    await init_indexes()
    await seed_admin()
    try:
        from app.services import code_interpreter as _ci_startup

        async def _warm_sandbox_status() -> None:
            try:
                await _ci_startup.check_availability()
            except Exception:
                pass

        try:
            import asyncio as _asyncio

            _loop = _asyncio.get_running_loop()
        except RuntimeError:
            _loop = None
        if _loop is not None:
            try:
                _task = _loop.create_task(_warm_sandbox_status())

                def _swallow_startup_warm(done_task) -> None:
                    try:
                        if not done_task.cancelled():
                            done_task.exception()
                    except Exception:
                        pass

                _task.add_done_callback(_swallow_startup_warm)
            except Exception:
                pass
    except Exception:
        pass
    logger.info("Uniyra AI Lab backend ready")


# The shared AsyncIOMotorClient is deliberately NOT closed on lifespan shutdown.
# Under `uvicorn --reload`, the reloader can keep an old worker alive after its
# shutdown hook has run. Closing the module-global client in that window leaves
# the surviving worker answering every DB-backed request with a 500 and no CORS
# header (`pymongo.errors.InvalidOperation: Cannot use MongoClient after close`),
# which browsers surface as "No 'Access-Control-Allow-Origin' header" on
# /api/auth/login. The OS reclaims the sockets when the process actually exits,
# so an explicit close only creates a failure mode without any real benefit.

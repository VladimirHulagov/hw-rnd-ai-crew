import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config as cfg
from reconciler import Reconciler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

reconciler: Reconciler | None = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global reconciler
    reconciler = Reconciler()
    t = threading.Thread(target=reconciler.run_loop, daemon=True)
    t.start()
    logger.info("Operator started")
    yield


app = FastAPI(title="Agent Operator", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    if reconciler:
        return {"managed_agents": len(reconciler._agent_hashes)}
    return {"managed_agents": 0}

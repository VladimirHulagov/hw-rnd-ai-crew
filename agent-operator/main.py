import logging
import threading

from fastapi import FastAPI

from . import config as cfg
from .reconciler import Reconciler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Operator", version="0.1.0")

reconciler: Reconciler | None = None


@app.on_event("startup")
def startup():
    global reconciler
    reconciler = Reconciler()
    t = threading.Thread(target=reconciler.run_loop, daemon=True)
    t.start()
    logger.info("Operator started")


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    if reconciler:
        return {"managed_agents": len(reconciler._agent_hashes)}
    return {"managed_agents": 0}

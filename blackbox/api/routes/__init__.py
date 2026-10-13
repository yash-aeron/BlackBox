"""HTTP routers for the BlackBox API."""

from . import evidence, execution, model, stream, targets

ROUTERS = (
    targets.router,
    execution.router,
    model.router,
    evidence.router,
    stream.router,
)

__all__ = ["ROUTERS", "evidence", "execution", "model", "stream", "targets"]

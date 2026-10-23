from fastapi.templating import Jinja2Templates

from ntfsmnt.web.app import PAGE_DIR
from ntfsmnt.web.schemas import OperationModel

templates = Jinja2Templates(directory=PAGE_DIR)


def _state_with_operation(state, operation):
    if operation is None:
        return state
    return state.model_copy(
        update={"operation": OperationModel.model_validate(operation)}
    )


def _context(state, *, oob=False, activity=(), error=None):
    return {
        "state": state,
        "selected": state.volumes[0].id if state.volumes else None,
        "activity": activity,
        "error": error,
        "oob": oob,
    }


def render_index(state, *, operation=None, activity=()):
    return templates.get_template("index.html").render(
        **_context(_state_with_operation(state, operation), activity=activity)
    )


def render_detail(state, *, oob=False, operation=None, activity=(), error=None):
    return templates.get_template("_detail.html").render(
        **_context(
            _state_with_operation(state, operation),
            oob=oob,
            activity=activity,
            error=error,
        )
    )

from fastapi.templating import Jinja2Templates

from ntfsmnt.web.app import PAGE_DIR

templates = Jinja2Templates(directory=PAGE_DIR)


def _context(state, *, oob=False):
    return {
        "state": state,
        "selected": state.volumes[0].id if state.volumes else None,
        "activity": (),
        "error": None,
        "oob": oob,
    }


def render_index(state):
    return templates.get_template("index.html").render(**_context(state))


def render_detail(state, *, oob=False):
    return templates.get_template("_detail.html").render(**_context(state, oob=oob))

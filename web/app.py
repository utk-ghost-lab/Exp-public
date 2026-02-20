"""FastAPI app for Placement Team — Command Center (web dashboard MVP)."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web import config
from web.routes import apply, dashboard, documents, generate, research

logger = logging.getLogger(__name__)

# Fallback for generate POST so HTMX always gets 200 (never 500)
_GENERATE_ERROR_HTML = '''<div class="border border-red-200 bg-red-50 rounded p-4 text-red-700">
<p class="font-medium">Something went wrong.</p>
<p class="text-sm mt-2">Please try again or paste a job description and click Generate Resume.</p>
</div>'''

WEB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = config.PROJECT_ROOT

app = FastAPI(title="Placement Team — Command Center", version="0.1.0")

# Attach state to app so routes can access templates without circular import
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


@app.on_event("startup")
def startup():
    app.state.templates = templates

    # Apply Manager: recover interrupted jobs from previous server run
    import apply_manager
    apply_manager.recover_interrupted()

# Static files (optional)
static_dir = WEB_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Serve generated output files (PDF, etc.)
config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(config.OUTPUT_DIR)), name="output")


def get_output_dir() -> Path:
    """Output directory for generated resumes (output/ at project root)."""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return config.OUTPUT_DIR


app.include_router(dashboard.router, tags=["dashboard"])
app.include_router(documents.router, prefix="/documents", tags=["documents"])
app.include_router(generate.router, prefix="/generate", tags=["generate"])
app.include_router(research.router, prefix="/research", tags=["research"])
app.include_router(apply.router, prefix="/apply", tags=["apply"])


@app.exception_handler(Exception)
async def catch_all_exception_handler(request: Request, exc: Exception):
    """For POST /generate, always return 200 with error HTML so HTMX can swap (never 500)."""
    if request.method == "POST" and request.url.path.rstrip("/").endswith("/generate"):
        logger.exception("Unhandled exception for POST /generate: %s", exc)
        return HTMLResponse(content=_GENERATE_ERROR_HTML, status_code=200)
    raise exc

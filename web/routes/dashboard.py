"""Dashboard route — landing page with quick action and recent runs."""

import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from web import config

router = APIRouter()
OUTPUT_DIR = config.OUTPUT_DIR
RECENT_MAX = 10


def _list_recent_runs():
    """List last N output folders with score and PDF link. No DB."""
    if not OUTPUT_DIR.exists():
        return []
    runs = []
    for name in os.listdir(OUTPUT_DIR):
        path = OUTPUT_DIR / name
        if not path.is_dir():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        score = None
        score_path = path / "score_report.json"
        if score_path.exists():
            try:
                with open(score_path, "r") as f:
                    data = json.load(f)
                score = data.get("total_score")
            except (json.JSONDecodeError, IOError):
                pass
        pdf_path = None
        for f in path.iterdir():
            if f.suffix.lower() == ".pdf":
                pdf_path = f.name
                break
        runs.append({
            "folder": name,
            "mtime": mtime,
            "score": score,
            "pdf_name": pdf_path,
        })
    runs.sort(key=lambda r: r["mtime"], reverse=True)
    return runs[:RECENT_MAX]


@router.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    """Dashboard: quick action + recent runs from output/."""
    recent = _list_recent_runs()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "recent_runs": recent},
    )

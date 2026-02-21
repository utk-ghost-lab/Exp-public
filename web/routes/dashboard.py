"""Dashboard route — landing page with quick action and recent runs."""

import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

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


@router.get("/open-output-folder/{folder_name}", response_class=JSONResponse)
async def get_open_output_folder(folder_name: str):
    """Open an output folder in the native file manager (local use only)."""
    import subprocess
    import platform
    target = (OUTPUT_DIR / folder_name).resolve()
    if not str(target).startswith(str(OUTPUT_DIR.resolve())):
        return JSONResponse({"detail": "Invalid folder."}, status_code=400)
    if not target.is_dir():
        return JSONResponse({"detail": "Folder not found."}, status_code=404)
    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", str(target)])
    elif system == "Linux":
        subprocess.Popen(["xdg-open", str(target)])
    elif system == "Windows":
        subprocess.Popen(["explorer", str(target)])
    else:
        return JSONResponse({"detail": f"Unsupported platform: {system}"}, status_code=400)
    return JSONResponse({"status": "ok"})


@router.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    """Dashboard: quick action + recent runs from output/."""
    recent = _list_recent_runs()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "recent_runs": recent},
    )

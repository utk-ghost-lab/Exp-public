"""In-memory state for web dashboard (job stores and queues)."""
# job_id -> state dict from run_pipeline(..., stop_before_pdf=True)
job_stores = {}
# job_id -> queue.Queue for SSE progress
job_queues = {}

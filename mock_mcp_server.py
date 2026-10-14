"""Safe mock MCP server for end-to-end testing. It NEVER submits a real application."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("JobMatch AI Mock Application Provider")


@mcp.tool()
def prepare_application(job: dict, candidate_profile: dict, resume_filename: str, resume_base64: str, idempotency_key: str) -> dict:
    """Validate the shape of an application request without storing resume contents."""
    if not job.get("url") or not candidate_profile:
        return {"ready": False, "message": "Mock validation failed: job URL or candidate profile is missing."}
    return {
        "ready": True,
        "application_payload": {
            "mode": "simulation_only",
            "job_url": job["url"],
            "resume_filename": resume_filename,
            "idempotency_key": idempotency_key,
            "resume_received": bool(resume_base64),
        },
        "message": "Mock validation passed. This is not a real application.",
    }


@mcp.tool()
def submit_application(job: dict, application_payload: dict, idempotency_key: str) -> dict:
    """Simulate a submission and explicitly report that no real application was sent."""
    return {
        "submitted": False,
        "status": "simulation_only",
        "application_id": "MOCK-NOT-SUBMITTED",
        "message": "Simulation completed. No application was submitted to the employer or job portal.",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")

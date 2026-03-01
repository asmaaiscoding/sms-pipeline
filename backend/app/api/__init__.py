from app.api.auth import router as auth_router
from app.api.projects import router as projects_router
from app.api.papers import router as papers_router
from app.api.jobs import router as jobs_router
from app.api.exports import router as exports_router

__all__ = [
    "auth_router",
    "projects_router",
    "papers_router",
    "jobs_router",
    "exports_router",
]

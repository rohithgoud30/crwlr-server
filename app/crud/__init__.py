from app.crud.document import DocumentCRUD, document_crud
from app.crud.submission import SubmissionCRUD, submission_crud
from app.crud.stats import StatsCRUD, stats_crud

__all__ = [
    "DocumentCRUD",
    "SubmissionCRUD",
    "StatsCRUD",
    "document_crud",
    "submission_crud",
    "stats_crud",
]

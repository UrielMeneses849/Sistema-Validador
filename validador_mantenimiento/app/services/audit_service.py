from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def register_audit_event(db: Session, validation_id: int, event_type: str, message: str) -> AuditLog:
    event = AuditLog(validation_id=validation_id, event_type=event_type, message=message)
    db.add(event)
    return event


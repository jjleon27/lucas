"""
CRUD para Proyectos: un agrupador transversal de gastos (ej. "Viaje a Buenos
Aires", "Remodelación cocina"). Cada transacción puede apuntar a un proyecto vía
`Transaction.project_id`. Un proyecto tiene presupuesto opcional y fechas.

Endpoints:
  GET    /projects                 → lista (con `spent` y `tx_count` calculados)
  POST   /projects                 → crear
  GET    /projects/{id}            → uno
  PATCH  /projects/{id}            → editar (incluye archivar)
  DELETE /projects/{id}            → borrar (las transacciones quedan sin proyecto)
  GET    /projects/{id}/summary    → gasto total, % presupuesto, gasto por categoría
  GET    /projects/{id}/transactions → transacciones del proyecto
"""
from sqlalchemy import func
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_spend(db: Session, project_id: int) -> tuple[float, int]:
    """(gasto total, nº transacciones) del proyecto. Gasto excluye ingresos y
    transferencias internas."""
    base = db.query(models.Transaction).filter(models.Transaction.project_id == project_id)
    spent = float(
        base.filter(
            models.Transaction.is_income.is_(False),
            models.Transaction.is_transfer.is_(False),
        )
        .with_entities(func.coalesce(func.sum(models.Transaction.amount), 0.0))
        .scalar()
        or 0.0
    )
    count = int(base.with_entities(func.count(models.Transaction.id)).scalar() or 0)
    return round(spent), count


def _get_owned(db: Session, user_id: int, project_id: int) -> models.Project:
    p = (
        db.query(models.Project)
        .filter(models.Project.id == project_id, models.Project.user_id == user_id)
        .first()
    )
    if not p:
        raise HTTPException(404, "Proyecto no encontrado")
    return p


def _to_out(db: Session, p: models.Project) -> schemas.ProjectOut:
    spent, count = _project_spend(db, p.id)
    out = schemas.ProjectOut.model_validate(p)
    out.spent = spent
    out.tx_count = count
    return out


@router.get("", response_model=list[schemas.ProjectOut])
def list_projects(
    include_archived: bool = False,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.Project).filter(models.Project.user_id == current.id)
    if not include_archived:
        q = q.filter(models.Project.archived.is_(False))
    projects = q.order_by(models.Project.created_at.desc()).all()
    return [_to_out(db, p) for p in projects]


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(
    payload: schemas.ProjectCreate,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(422, "El nombre no puede estar vacío")
    p = models.Project(
        user_id=current.id,
        name=name,
        budget=max(0.0, float(payload.budget or 0.0)),
        start_date=payload.start_date,
        end_date=payload.end_date,
        color=payload.color or "#6366f1",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_out(db, p)


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(
    project_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return _to_out(db, _get_owned(db, current.id, project_id))


@router.patch("/{project_id}", response_model=schemas.ProjectOut)
def update_project(
    project_id: int,
    payload: schemas.ProjectUpdate,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    p = _get_owned(db, current.id, project_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        nm = (data["name"] or "").strip()
        if not nm:
            raise HTTPException(422, "El nombre no puede estar vacío")
        p.name = nm
    if "budget" in data and data["budget"] is not None:
        p.budget = max(0.0, float(data["budget"]))
    for f in ("start_date", "end_date", "color", "archived"):
        if f in data and data[f] is not None:
            setattr(p, f, data[f])
    db.commit()
    db.refresh(p)
    return _to_out(db, p)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    p = _get_owned(db, current.id, project_id)
    # FK es ON DELETE SET NULL: las transacciones quedan sin proyecto, no se borran.
    db.query(models.Transaction).filter(models.Transaction.project_id == p.id).update(
        {models.Transaction.project_id: None}
    )
    db.delete(p)
    db.commit()
    return None


@router.get("/{project_id}/summary", response_model=schemas.ProjectSummary)
def project_summary(
    project_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    p = _get_owned(db, current.id, project_id)
    spent, tx_count = _project_spend(db, p.id)

    cat_rows = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.project_id == p.id,
            models.Transaction.is_income.is_(False),
            models.Transaction.is_transfer.is_(False),
        )
        .with_entities(
            models.Transaction.category,
            func.coalesce(func.sum(models.Transaction.amount), 0.0),
        )
        .group_by(models.Transaction.category)
        .order_by(func.sum(models.Transaction.amount).desc())
        .all()
    )
    by_category = [
        schemas.ProjectCategorySpend(category=c or "Otros", amount=round(float(a or 0.0)))
        for c, a in cat_rows
    ]

    budget = float(p.budget or 0.0)
    remaining = round(budget - spent) if budget > 0 else 0.0
    pct_used = (spent / budget) if budget > 0 else 0.0

    return schemas.ProjectSummary(
        project=_to_out(db, p),
        spent=spent,
        remaining=remaining,
        pct_used=round(pct_used, 4),
        by_category=by_category,
        tx_count=tx_count,
    )


@router.get("/{project_id}/transactions", response_model=list[schemas.TransactionOut])
def project_transactions(
    project_id: int,
    current: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned(db, current.id, project_id)
    txs = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.project_id == project_id,
            models.Transaction.user_id == current.id,
        )
        .order_by(models.Transaction.date.desc(), models.Transaction.id.desc())
        .all()
    )
    return txs

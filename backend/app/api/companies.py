from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, or_, select

from app.api.deps import current_user
from app.db import Company, User, get_session
from app.scout.detect import detect_kind
from app.scout.netguard import UnsafeURL, assert_public_url

router = APIRouter(prefix="/me/companies", tags=["companies"])


class CompanyIn(BaseModel):
    name: str
    url: str
    stealth: bool = False


def _out(c: Company) -> dict:
    return {"id": c.id, "name": c.name, "kind": c.kind, "url": c.url, "token": c.token, "stealth": c.stealth,
            "active": c.active, "global": c.user_id is None}


@router.get("")
def list_companies(user: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(Company).where(or_(Company.user_id == user.id, Company.user_id == None))).all()
    return [_out(c) for c in rows]


@router.post("", status_code=201)
def add_company(body: CompanyIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        url = assert_public_url(body.url)
    except UnsafeURL as e:
        raise HTTPException(422, str(e))
    kind, token = detect_kind(url)
    c = Company(user_id=user.id, name=body.name.strip(), kind=kind, token=token, url=url, stealth=body.stealth)
    session.add(c)
    session.commit()
    session.refresh(c)
    return _out(c)


@router.delete("/{company_id}", status_code=204)
def delete_company(company_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    c = session.get(Company, company_id)
    if not c or c.user_id != user.id:
        raise HTTPException(404, "Empresa no encontrada")
    session.delete(c)
    session.commit()

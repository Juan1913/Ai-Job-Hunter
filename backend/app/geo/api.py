"""Mapa de ofertas: agrupa por lugar (ciudad o país) las ofertas del usuario. Sin servicios externos: gazetario embebido."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.api.deps import current_user
from app.db import JobRow, User, get_session
from app.geo.gazetteer import is_remote_text, locate

router = APIRouter(prefix="/me/map", tags=["map"])
PER_PLACE = 25


@router.get("/jobs")
def jobs_map(include_discarded: bool = Query(False), session: Session = Depends(get_session), user: User = Depends(current_user)):
    rows = session.exec(select(JobRow).where(JobRow.user_id == user.id)).all()
    places: dict[str, dict] = {}
    remote = unlocated = total = 0
    for r in rows:
        if (r.status == "discarded" and not include_discarded) or r.data.get("closed"):
            continue
        total += 1
        loc = r.data.get("location") or ""
        ev = r.evaluation or {}
        item = {"id": r.id, "title": r.data.get("title", ""), "company": r.data.get("company", ""), "status": r.status, "source": r.data.get("source", ""),
                "score": ev.get("score"), "recommendation": ev.get("recommendation"), "remote": bool(r.data.get("remote"))}
        spot = locate(loc, r.data.get("country"))
        if spot is None:
            if r.data.get("remote") or is_remote_text(loc):
                remote += 1
            else:
                unlocated += 1
            continue
        p = places.setdefault(spot["place"], {**spot, "count": 0, "best_score": None, "jobs": []})
        p["count"] += 1
        if item["score"] is not None and (p["best_score"] is None or item["score"] > p["best_score"]):
            p["best_score"] = item["score"]
        p["jobs"].append(item)
    points = []
    for p in places.values():
        p["jobs"].sort(key=lambda j: (-(j["score"] or 0), j["title"]))
        p["jobs"] = p["jobs"][:PER_PLACE]
        points.append(p)
    points.sort(key=lambda p: -p["count"])
    return {"total": total, "located": total - remote - unlocated, "remote": remote, "unlocated": unlocated, "points": points}

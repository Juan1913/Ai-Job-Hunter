import numpy as np
import pytest
from sqlmodel import Session, select

from app.ats.gaps import classify
from app.db import ProfileNode, User, engine
from app.domain.profile import Experience, Profile, Skill
from app.domain.tailoring import GapStatus
from app.rag.embeddings import CachedEmbedder, EmbeddingError, HashEmbedder
from app.rag.retrieve import ProfileRetriever
from app.rag.service import retriever_for
from app.rag.store import ensure_tree, invalidate_tree
from app.rag.tree import build_nodes, profile_hash
from tests.fakes import PROFILE, FakeLLM

EMB = HashEmbedder()


@pytest.fixture
def uid():
    with Session(engine) as s:
        u = User(email="rag@x.com", password_hash="x")
        s.add(u)
        s.commit()
        return u.id


def big_profile() -> Profile:
    p = PROFILE.model_copy(deep=True)
    p.certifications = ["CompTIA Security+"]
    p.experience.append(Experience(company="Beta", title="Soporte TI", start="2018", end="2020",
                                   bullets=["Administré usuarios en Active Directory", "Atendí tickets de hardware"]))
    return p


def test_tree_structure_leaves_mids_root():
    nodes = build_nodes(big_profile())
    by = {}
    for n in nodes:
        by.setdefault(n.kind, []).append(n)
    assert len(by["root"]) == 1 and by["root"][0].level == 2
    assert {n.kind for n in nodes if n.level == 1} == {"experience", "skills", "certs"}
    assert len(by["bullet"]) == 4 and len(by["skill"]) == 3 and len(by["cert"]) == 1
    root = by["root"][0].idx
    for n in nodes:                                   # toda hoja cuelga de un intermedio o de la raíz; todo llega a la raíz
        assert n.parent is not None or n.idx == root
        if n.level == 0:
            assert nodes[n.parent].level in (1, 2)
        if n.level == 1:
            assert n.parent == root
    exp1 = next(n for n in by["experience"] if "Soporte TI" in n.text)
    assert all(nodes[b.parent] is exp1 for b in by["bullet"] if "Active Directory" in b.text or "hardware" in b.text)


def test_profile_hash_ignores_preferences_but_not_content():
    p = big_profile()
    h = profile_hash(p)
    p.preferences.remote = False
    assert profile_hash(p) == h
    p.skills.append(Skill(name="Nuevo"))
    assert profile_hash(p) != h


def test_ensure_tree_persists_reuses_and_rebuilds_on_change(uid):
    p = big_profile()
    with Session(engine) as s:
        idx = ensure_tree(s, uid, p, EMB)
        n1 = len(idx.nodes)
        ids1 = {r.id for r in s.exec(select(ProfileNode)).all()}
        assert n1 == len(ids1) and idx.root is not None and idx.matrix.shape == (n1, 512)
        assert {r.id for r in s.exec(select(ProfileNode)).all()} == ids1 and len(ensure_tree(s, uid, p, EMB).nodes) == n1   # sin cambios: no reconstruye
        p.skills.append(Skill(name="Wireshark"))
        idx2 = ensure_tree(s, uid, p, EMB)
        assert len(idx2.nodes) == n1 + 1 and any(n.text == "Wireshark" for n in idx2.nodes)
        assert {r.profile_hash for r in s.exec(select(ProfileNode)).all()} == {profile_hash(p)}   # se reconstruyó entero
        assert ensure_tree(s, uid, p, HashEmbedder(128)).matrix.shape[1] == 128      # cambio de modelo -> reconstruye
        invalidate_tree(s, uid)
        assert s.exec(select(ProfileNode)).first() is None


def test_trees_are_per_user(uid):
    with Session(engine) as s:
        other = User(email="o@x.com", password_hash="x")
        s.add(other)
        s.commit()
        ensure_tree(s, uid, big_profile(), EMB)
        small = Profile(headline="Chef", skills=[Skill(name="Cocina")])
        ensure_tree(s, other.id, small, EMB)
        assert len(ensure_tree(s, uid, big_profile(), EMB).nodes) > len(ensure_tree(s, other.id, small, EMB).nodes)


def test_retrieval_returns_literal_leaf_evidence_with_parent_context(uid):
    with Session(engine) as s:
        r = ProfileRetriever(ensure_tree(s, uid, big_profile(), EMB), EMB)
    ev = r.evidence("respuesta a incidentes de phishing", k=3)
    assert ev[0].text == "Respondí a incidentes de phishing y malware" and ev[0].kind == "bullet"
    assert "Analista de Seguridad" in ev[0].context and ev[0].root
    corpus = "\n".join(n.text for n in r.index.nodes if n.level == 0)
    assert all(e.text in corpus for e in ev)
    assert r.evidence("Active Directory", k=1)[0].text == "Administré usuarios en Active Directory"
    assert r.evidence("algo", k=2) and len(r.evidence_many(["a", "b"], 1)) == 2


def test_parent_context_boosts_leaves_of_the_relevant_experience(uid):
    with Session(engine) as s:
        r = ProfileRetriever(ensure_tree(s, uid, big_profile(), EMB), EMB)
    bullets = [e for e in r.evidence("monitoreo de seguridad Splunk", k=6) if e.kind == "bullet"]
    assert bullets[0].ref.startswith("experience:0")


class SpyLLM(FakeLLM):
    def __init__(self, answer):
        super().__init__()
        self.answer, self.prompts = answer, []

    def complete_json(self, system, user):
        if "adyacentes" in system:
            self.prompts.append(user)
            return {"adjacent": self.answer}
        return super().complete_json(system, user)


def _retriever(uid):
    with Session(engine) as s:
        return ProfileRetriever(ensure_tree(s, uid, PROFILE, EMB), EMB)


def test_classify_with_rag_uses_retrieved_evidence_and_keeps_guard(uid):
    r = _retriever(uid)
    llm = SpyLLM([{"keyword": "respuesta a incidentes", "evidence": "Respondí a incidentes de phishing y malware"},
                  {"keyword": "Kubernetes", "evidence": "inventada que no está en el perfil"}])
    gaps = {g.keyword: g for g in classify(["SIEM", "respuesta a incidentes", "Kubernetes"], PROFILE, llm, retriever=r)}
    assert gaps["SIEM"].status == GapStatus.HAVE
    assert gaps["respuesta a incidentes"].status == GapStatus.ADJACENT
    assert gaps["Kubernetes"].status == GapStatus.MISSING
    assert "Respondí a incidentes de phishing y malware" in llm.prompts[0] and "REQUISITO: Kubernetes" in llm.prompts[0]


def test_rag_guard_rejects_real_but_unretrieved_evidence(uid):
    r = _retriever(uid)
    # La cita existe literalmente en el perfil, pero no es una de las hojas recuperadas para ese requisito.
    llm = SpyLLM([{"keyword": "Kubernetes", "evidence": "Analista con 4 años en monitoreo de seguridad."}])
    gaps = {g.keyword: g for g in classify(["Kubernetes"], PROFILE, llm, retriever=r)}
    assert gaps["Kubernetes"].status == GapStatus.MISSING


def test_rag_prompt_is_smaller_than_full_profile_prompt(uid):
    r = _retriever(uid)
    big = big_profile()
    big.experience[0].bullets += [f"Tarea irrelevante número {i} sobre limpieza de oficinas y archivo" for i in range(40)]
    with Session(engine) as s:
        rb = ProfileRetriever(ensure_tree(s, uid, big, EMB), EMB)
    a, b = SpyLLM([]), SpyLLM([])
    classify(["Kubernetes"], big, a)
    classify(["Kubernetes"], big, b, retriever=rb)
    assert len(b.prompts[0]) < len(a.prompts[0]) / 2
    assert r is not None


def test_classify_without_retriever_is_unchanged():
    gaps = {g.keyword: g for g in classify(["respuesta a incidentes"], PROFILE, FakeLLM())}
    assert gaps["respuesta a incidentes"].status == GapStatus.ADJACENT


class BrokenEmbedder(HashEmbedder):
    name = "roto"

    def embed(self, texts, kind="doc"):
        raise EmbeddingError("sin modelo")


def test_retriever_for_degrades_to_none_when_embeddings_fail(uid):
    with Session(engine) as s:
        assert retriever_for(s, uid, PROFILE, BrokenEmbedder()) is None
        r = retriever_for(s, uid, PROFILE, CachedEmbedder(HashEmbedder(), engine))
        assert r is not None and isinstance(r.index.matrix, np.ndarray)

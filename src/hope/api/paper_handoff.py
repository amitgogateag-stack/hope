"""One-use admission narrowing inside the existing running deployment guard.

Intent is never execution authority. Callers must retain paper_operational_guard
and the certified scheduler's financial gates. This module launches no process.
"""
from contextlib import contextmanager
from hope.api.first_cycle_authorization import verify,require
from hope.infrastructure.repositories.paper_handoff import SqlAlchemyPaperHandoffRepository


@contextmanager
def handoff_guard(engine,root,sha,request,receipt,factory,cycle_key,*,verifier=verify):
    proof=verifier(root,sha,request,receipt,factory)
    require(proof.token['first_cycle_key_sha256']==cycle_key)
    # Commit before dispatch. Uncertain commit or any later failure consumes the
    # intent permanently; never retry, clear, or manufacture an ABORTED outcome.
    with engine.begin() as connection:SqlAlchemyPaperHandoffRepository(connection).claim(proof)
    def check():proof.assert_current()
    check()
    yield check
    check()
    with engine.begin() as connection:SqlAlchemyPaperHandoffRepository(connection).finish(proof,'ACKNOWLEDGED')

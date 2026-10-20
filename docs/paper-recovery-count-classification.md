# Bounded aggregate PAPER recovery topology

Recovery assessments previously expanded each aggregate effect count into that
many Python objects before calling the canonical topology classifier. Repeated
report properties repeated this allocation, even though topology only depends on
zero, one and many occurrences. Database statement timeouts do not bound this
Python classification work. No production incident is claimed.

The shared aggregate classifier retains at most two occurrences per count entry.
Recovery evidence/cardinality and read-only inspection now share it. Raw count
values in diagnostic output remain exact. Existing topology semantics, including
multi-signal stages and contradictory duplicate complete-execution effects, are
preserved. Lineage, receipt, accounting, HALT and transaction proof requirements
remain independent and unchanged.

This is a per-count expansion bound. It does not cap arbitrary input iterators,
change effect retrieval, guarantee total recovery latency or authorize execution.
No strategy/risk rule, SQL schema, migration or trading behavior is changed.

Two guarded regressions failed against the pre-change recovery code without
allocating a trillion objects. Focused local tests include all zero/one/two
cardinality combinations across effect types and trillion-count cases; 56 passed
in 0.66s. PostgreSQL integration and final exact-head Linux CI remain required
before the engineering batch is complete. AWS operations remain separately gated.

Local PostgreSQL 17.11 composition: 22 passed in 19.20s across recovery inspection,
reconciliation batch and shared-portfolio recovery. PostgreSQL was stopped after
testing. Final full Python 3.13/PostgreSQL 16+17 CI validates the published tree;
the local focused runs are not a claim of full local-suite completion.

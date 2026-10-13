---
paths:
  - "tests/**"
---

# tests/ rules (restates the spec; adds nothing)

- Test inputs come from the fixture generator (output in `tests/fixtures/generated/`); never hand-edited data files. Config comes only from `tests/fixtures/config/`, never `config/defaults/`. — `09-testing-milestones-deferred.md` §9; `10-addendum.md` A9, A10
- Use `hypothesis` for property tests: flag severity order, `<`/non-detect flag → ND unless Bad, conversions on values and DLs, duplicate precedence, names from mapped values, zeros → NaN only for `transform = log`, status counts sum to cells, imputed output checks, imputed mask = ND ∪ MISSING_*. — `09-testing-milestones-deferred.md` §9 "Property tests"
- Unit tests per `core/` function assert report contents. — `09-testing-milestones-deferred.md` §9 "Tests"
- Cover identity, fingerprint, recipe/migration, artifact-invariant and summary (reconciliation + golden `summary.md`) tests as listed in §9. — `09-testing-milestones-deferred.md` §9
- Never weaken, skip or delete a test to make code pass; report the failure and its cause. — project working loop step 4 in `CLAUDE.md` (not a spec requirement)

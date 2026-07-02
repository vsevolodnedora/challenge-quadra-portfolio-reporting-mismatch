"""Authenticated extraction of the QUADRA challenge API into an immutable raw store.

Design contract (see plans/DATA_LOADING.md and the user directive):
- Persist every HTTP response body *verbatim*; never coerce, reject, or reshape data.
- The only HARD gate is extraction completeness (summed page rows == meta.total_records) —
  that guards against silently losing data, it never judges data content.
- Every content expectation (row counts, enums, nulls, duplicates) is a RECORDED finding,
  not an abort: an anomaly there may itself be one of the three report deviations.
"""

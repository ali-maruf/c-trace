"""C-Trace persistence: Postgres schema access, document storage, job queue and services.

Requires the `db` extra (`ctrace[db]`). The pure core (`ctrace.calculate`, `ctrace.verify`, ...) does not
import this package.
"""

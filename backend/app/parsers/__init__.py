"""
app/parsers/__init__.py

Parser subpackage for DevTwin Phase 1 — Step 2.

Each module in this package is a self-contained, stateless detector:
  - tech_stack_detector.py   : Language, framework, database, AI tool detection
  - route_service.py         : FastAPI & Next.js API route extraction
  - ast_service.py           : File-level Python import dependency graph
  - architecture_service.py  : Heuristic architecture pattern detection

All parsers accept structured inputs (manifest + optional file contents)
and return typed Pydantic models. They never call the GitHub API or the
database directly — that is the orchestrator's responsibility.
"""

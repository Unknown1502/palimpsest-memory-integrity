"""
infrastructure/lambda/gate_handler/handler.py — Lambda entrypoint for the
gate's admission path, exposed via a Function URL (see
infrastructure/stacks/palimpsest_stack.py).

Deliberately NOT a second, bespoke HTTP handler duplicating api/routes/
logic: this wraps the SAME FastAPI app from api/main.py with Mangum (an
ASGI-to-Lambda adapter), so there is exactly one HTTP surface and two ways
to run it — `uvicorn api.main:app` locally, this handler in Lambda. Every
route, every dependency, every bug fix lives in one place either way.
"""

from mangum import Mangum

from api.main import app

handler = Mangum(app)

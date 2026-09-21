from fastapi import FastAPI
from app.routes import users, orders, checkout, billing, admin, health
app = FastAPI()
for r in (users, orders, checkout, billing, admin, health):
    app.include_router(r.router)

from fastapi import APIRouter

from app.api.endpoints import booking_endpoints, health_endpoints

api_router = APIRouter()
api_router.include_router(health_endpoints.router, tags=["health"])
api_router.include_router(booking_endpoints.router, tags=["booking"])

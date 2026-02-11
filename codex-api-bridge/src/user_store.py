import logging
from typing import Optional

from bson.objectid import ObjectId
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from .config import settings

logger = logging.getLogger(__name__)

_users_client: Optional[AsyncIOMotorClient] = None
_users_collection = None


async def init_users_collection() -> None:
    """Initialize MongoDB users collection connection."""
    global _users_client, _users_collection

    if not settings.user_mongodb_url:
        logger.warning("USER_MONGODB_URL not configured")
        return

    try:
        _users_client = AsyncIOMotorClient(settings.user_mongodb_url)
        _users_collection = _users_client[settings.user_mongodb_database][
            settings.user_mongodb_collection
        ]
        logger.info("Users collection connected")
    except Exception as exc:
        logger.error("Users collection connection failed: %s", exc)


async def close_users_collection() -> None:
    """Close MongoDB connection."""
    global _users_client
    if _users_client:
        _users_client.close()
        logger.info("Users collection disconnected")


async def verify_user_identity(keycloak_id: str, requested_user_id: str) -> None:
    """Verify the app user id matches the keycloak subject."""
    if _users_collection is None:
        raise HTTPException(
            status_code=503,
            detail="Error: Authorization service unavailable",
        )

    try:
        user_doc = await _users_collection.find_one(
            {"keycloak_id": keycloak_id, "_id": ObjectId(requested_user_id)}
        )
    except Exception as exc:
        logger.warning("User lookup failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Error: Authorization service unavailable",
        ) from exc

    if not user_doc:
        raise HTTPException(
            status_code=403,
            detail="Error: User not found in system or ID mismatch",
        )

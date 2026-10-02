"""Shared MongoDB handle (motor)."""
from motor.motor_asyncio import AsyncIOMotorClient

import settings

mongo = AsyncIOMotorClient(settings.MONGO_URL)
db = mongo[settings.DB_NAME]

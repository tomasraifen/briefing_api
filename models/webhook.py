from pydantic import BaseModel
from typing import Optional


class InstantlyWebhook(BaseModel):
    event_type: str          # "email_sent" | "email_bounced" | "email_replied"
    instantly_id: str        # ID del email en Instantly
    lead_email: str
    campaign_id: Optional[str] = None
    timestamp: Optional[str] = None
    payload: Optional[dict] = None

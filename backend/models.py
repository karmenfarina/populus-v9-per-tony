"""
Pydantic request/response models for Populus backend.

Extracted from server.py as part of a defensive refactor — all these
schemas are pure data types with no runtime dependencies on the FastAPI
`app`, the MongoDB client or business logic, so pulling them out is
safe and shrinks server.py without touching any endpoint.

The chat/story constants that some of these models reference are also
lifted here so both server.py and models.py share the same source of
truth for size limits. server.py re-exports them (see the imports there)
so no other file needs to change.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, EmailStr


# --------------------------------------------------------------------
# Shared size limits — referenced by both this module (Pydantic Field)
# and various endpoints in server.py.
# --------------------------------------------------------------------
MAX_MSG_TEXT = 2000
MAX_MSG_IMAGE_BYTES = 3_000_000  # ~3MB base64 payload
STORY_COMMENT_MAX = 200


# --------------------------------------------------------------------
# Auth / onboarding
# --------------------------------------------------------------------
class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    # Length + character rules are enforced by `_normalize_and_validate_nickname`
    # so we can return specific Italian 400s instead of Pydantic 422s.
    nickname: str


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class AnonymousBody(BaseModel):
    nickname: str


class GoogleSessionBody(BaseModel):
    session_id: str


class VerifyEmailBody(BaseModel):
    token: str


class ResendVerificationBody(BaseModel):
    email: str


# --------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------
class ProfileBody(BaseModel):
    age: int = Field(ge=13, le=120)
    sex: Literal['F', 'M', 'other', 'na']
    region: str = Field(min_length=2, max_length=40)
    favorite_categories: List[str] = Field(min_length=1)
    # New field (optional for back-compat with previously onboarded users).
    profession: Optional[str] = Field(default=None, max_length=60)
    # Nickname override — used by external-provider signups (Google) so the
    # user can choose their own handle instead of inheriting the Google name.
    # Optional to keep backwards compatibility with existing onboarding calls.
    # Rules enforced in `_normalize_and_validate_nickname`, not by Pydantic.
    nickname: Optional[str] = None
    # Optional public "display name" shown in grey under the nickname on the
    # profile. Free-form, doesn't need to be unique.
    display_name: Optional[str] = Field(default=None, max_length=40)


class DetailsBody(BaseModel):
    bio: Optional[str] = Field(default=None, max_length=200)
    social_links: Optional[dict] = None  # {instagram, tiktok, twitter, youtube, website}


class PhotoUploadBody(BaseModel):
    data: str = Field(min_length=40)  # cropped base64 (with or without prefix)
    original_data: Optional[str] = Field(default=None, min_length=40)  # uncropped source, used to allow re-cropping (zoom-out)


# --------------------------------------------------------------------
# Voting / comments
# --------------------------------------------------------------------
class VoteBody(BaseModel):
    side: Literal['A', 'B']


class CommentBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


# --------------------------------------------------------------------
# Push + support
# --------------------------------------------------------------------
class RegisterPushBody(BaseModel):
    platform: str
    device_token: str


class PushToggleBody(BaseModel):
    enabled: bool


class SupportBody(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=10, max_length=2000)
    frequency: str = Field(min_length=1, max_length=30)
    section: str = Field(min_length=1, max_length=30)
    contact_email: Optional[str] = None


# --------------------------------------------------------------------
# Messaging + sharing
# --------------------------------------------------------------------
class SendMessageBody(BaseModel):
    recipient_id: str = Field(min_length=1)
    text: Optional[str] = Field(default=None, max_length=MAX_MSG_TEXT)
    image_data: Optional[str] = Field(default=None, max_length=MAX_MSG_IMAGE_BYTES)
    # Instagram-style "share a post to a friend" — attaches a snapshot of the
    # feud so the recipient sees a preview inline in chat that they can tap
    # to open. Only feud_id is trusted from the client; the snapshot fields
    # are built server-side from the current feud document.
    shared_feud_id: Optional[str] = Field(default=None, min_length=1, max_length=120)


class ShareToUsersBody(BaseModel):
    """Payload for /feuds/{id}/share — the fan-out share-sheet endpoint.

    `recipient_ids` is the list of users the sender wants to share the feud
    with in a single tap (Instagram-style multi-select). `text` is optional
    and attached identically to every generated message.
    """
    recipient_ids: List[str] = Field(min_length=1, max_length=25)
    text: Optional[str] = Field(default=None, max_length=MAX_MSG_TEXT)


class ReactMessageBody(BaseModel):
    emoji: str = Field(min_length=1, max_length=8)


class ReportUserBody(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    message_id: Optional[str] = None


# --------------------------------------------------------------------
# Stories
# --------------------------------------------------------------------
class StoryCreateBody(BaseModel):
    feud_id: str
    comment: Optional[str] = Field(default=None, max_length=STORY_COMMENT_MAX)


class StoryReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)

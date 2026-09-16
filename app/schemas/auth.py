"""Pydantic v2 schemas — authentication."""

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class GoogleAuthRequest(BaseModel):
    id_token: str


class DevLoginRequest(BaseModel):
    email: str
    name: str = "Dev User"


class SignupRequest(BaseModel):
    """
    Email/password registration.

    Only the fields the app genuinely needs to create an account. Everything else
    (date of birth, division/district/upazila, availability) is collected by the
    existing complete-profile screen, which Google accounts already go through —
    so signup does not duplicate it.
    """

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str
    # Optional here because complete-profile asks for them anyway; accepted so a
    # user who fills them in at signup is not asked twice.
    phone: Optional[str] = Field(default=None, max_length=20)
    blood_group: Optional[str] = Field(default=None, max_length=3)
    gender: Optional[str] = Field(default=None, max_length=20)


class EmailLoginRequest(BaseModel):
    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class SignupResponse(BaseModel):
    """
    Deliberately carries no tokens.

    A freshly created account is not usable until the email is verified, so
    handing back a session here would defeat the check.
    """

    email: EmailStr
    email_verified: bool = False
    message: str
    # Which mail path handled the message: "smtp" for real delivery, "outbox"
    # when a development server wrote it to disk. Lets the app tell the tester
    # where to look without ever exposing the code itself.
    delivery: str


class VerifyEmailResponse(BaseModel):
    email: EmailStr
    email_verified: bool
    message: str


class ResendVerificationResponse(BaseModel):
    message: str
    delivery: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    profile_incomplete: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str

"""Current-user profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import AdminUser, CurrentUser
from app.models.user import TravelPreferences
from app.schemas.common import Message, Page, PaginationDep
from app.schemas.user import UserPublic, UserUpdate
from app.services import trip_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPublic, summary="Get the current profile")
async def read_me(user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(user)


@router.patch("/me", response_model=UserPublic, summary="Update the current profile")
async def update_me(payload: UserUpdate, user: CurrentUser) -> UserPublic:
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(user, field, value)
    await user.save()
    return UserPublic.model_validate(user)


@router.put(
    "/me/preferences",
    response_model=UserPublic,
    summary="Replace saved travel preferences",
    description=(
        "Preferences seed the constraints of every new trip, so changing them "
        "affects future trips only - existing trips keep the constraints they "
        "were created with."
    ),
)
async def replace_preferences(
    preferences: TravelPreferences, user: CurrentUser
) -> UserPublic:
    user.preferences = preferences
    await user.save()
    return UserPublic.model_validate(user)


@router.get("/me/stats", summary="Headline counts for the current account")
async def read_stats(user: CurrentUser) -> dict:
    return await trip_service.trip_stats(user)


@router.delete(
    "/me",
    response_model=Message,
    summary="Deactivate the current account",
    description=(
        "A soft delete: the account is disabled and all sessions revoked, but "
        "trips and itineraries are retained."
    ),
)
async def deactivate_me(user: CurrentUser) -> Message:
    from app.services import auth_service

    user.is_active = False
    await user.save()
    await auth_service.revoke_all_for_user(user.id)
    return Message(message="Account deactivated.")


@router.get(
    "",
    response_model=Page[UserPublic],
    summary="List all users (admin only)",
)
async def list_users(
    admin: AdminUser,
    pagination: PaginationDep,
) -> Page[UserPublic]:
    from app.models.user import User

    total = await User.find_all().count()
    users = (
        await User.find_all()
        .sort(-User.created_at)
        .skip(pagination.skip)
        .limit(pagination.limit)
        .to_list()
    )
    return Page.build(
        [UserPublic.model_validate(u) for u in users],
        total,
        pagination.skip,
        pagination.limit,
    )

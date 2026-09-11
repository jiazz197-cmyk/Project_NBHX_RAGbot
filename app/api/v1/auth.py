"""登录、当前用户、注册与 superuser 用户管理。"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.adapters.auth.password_hasher import BcryptPasswordHasherAdapter
from app.adapters.auth.user_repository import SqlAlchemyUserRepositoryAdapter
from app.adapters.auth.auth_adapter import JwtTokenIssuerAdapter
from app.core.exceptions import AuthenticationError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.security import get_current_user, require_roles
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER
from app.ports.dto.auth import (
    LoginCommand,
    RegisterCommand,
    ResetUserPasswordCommand,
    UpdatePagePermissionsCommand,
    UpdateUserRoleCommand,
    UserDTO,
)
from app.adapters.web.platform.token import TokenResponse
from app.adapters.web.platform.user import (
    UserLogin,
    UserPagePermissionsUpdate,
    UserPasswordReset,
    UserRead,
    UserRoleUpdate,
    UserRegister,
)
from app.usecases.auth.login import LoginUseCase
from app.usecases.auth.register import RegisterUseCase
from app.usecases.auth.users import (
    DeleteUserUseCase,
    GetUserUseCase,
    ListUsersUseCase,
    ResetUserPasswordUseCase,
    UpdateUserPagePermissionsUseCase,
    UpdateUserRoleUseCase,
)

router = APIRouter()
logger = get_logger("security.auth")

_user_repo = SqlAlchemyUserRepositoryAdapter()
_password_hasher = BcryptPasswordHasherAdapter()
_token_issuer = JwtTokenIssuerAdapter()


def _dto_to_user_read(dto: UserDTO) -> UserRead:
    return UserRead(
        id=uuid.UUID(dto.id) if dto.id else uuid.uuid4(),
        username=dto.username,
        name=dto.name,
        email=dto.email,
        phone=dto.phone,
        department=dto.department,
        avatar=dto.avatar,
        is_active=dto.is_active,
        role=dto.role,
        roles=[],
        permissions=dto.permissions,
    )


@router.post("/login", response_model=TokenResponse, summary="用户登录")
async def login(body: UserLogin):
    """校验账号密码，返回 JWT。"""
    try:
        uc = LoginUseCase(_user_repo, _password_hasher, _token_issuer)
        result = await uc.execute(LoginCommand(username=body.username, password=body.password))
        return result
    except AuthenticationError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.message,
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/me", response_model=UserRead, summary="获取当前用户信息")
async def get_me(current_user: CurrentUserPort = Depends(get_current_user)):
    """当前登录用户信息。"""
    uc = GetUserUseCase(_user_repo)
    dto = await uc.execute(current_user.id)
    return _dto_to_user_read(dto)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED, summary="用户注册")
async def register(body: UserRegister):
    """新用户，角色固定为 user。"""
    try:
        uc = RegisterUseCase(_user_repo, _password_hasher)
        dto = await uc.execute(RegisterCommand(
            username=body.username,
            email=str(body.email),
            password=body.password,
            name=body.name,
        ))
        return _dto_to_user_read(dto)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"注册失败: {e}")


@router.get(
    "/users",
    response_model=List[UserRead],
    summary="获取所有用户列表（仅 superuser）",
)
async def list_users(
    _: CurrentUserPort = Depends(require_roles(ROLE_SUPERUSER)),
):
    """全量用户列表，需 superuser。"""
    uc = ListUsersUseCase(_user_repo)
    dtos = await uc.execute()
    return [_dto_to_user_read(d) for d in dtos]


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除用户（仅 superuser）",
)
async def delete_user(
    user_id: uuid.UUID,
    current_user: CurrentUserPort = Depends(require_roles(ROLE_SUPERUSER)),
):
    """按 UUID 删除；不能删自己。"""
    if str(user_id) == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    try:
        uc = DeleteUserUseCase(_user_repo, current_user)
        await uc.execute(str(user_id))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.patch(
    "/users/{user_id}/role",
    response_model=UserRead,
    summary="修改用户角色（仅 superuser）",
)
async def update_user_role(
    user_id: uuid.UUID,
    body: UserRoleUpdate,
    current_user: CurrentUserPort = Depends(require_roles(ROLE_SUPERUSER)),
):
    """改角色为 admin/user；不可授予 superuser，不可改自己。"""
    if str(user_id) == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )
    try:
        uc = UpdateUserRoleUseCase(_user_repo)
        dto = await uc.execute(UpdateUserRoleCommand(
            target_user_id=str(user_id),
            new_role=str(body.role.value if hasattr(body.role, "value") else body.role),
            current_user_id=current_user.id,
            current_user_name=current_user.username,
        ))
        return _dto_to_user_read(dto)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.patch(
    "/users/{user_id}/page-permissions",
    response_model=UserRead,
    summary="修改用户页面可见权限（仅 superuser）",
)
async def update_user_page_permissions(
    user_id: uuid.UUID,
    body: UserPagePermissionsUpdate,
    current_user: CurrentUserPort = Depends(require_roles(ROLE_SUPERUSER)),
):
    """控制普通用户可查看的页面。"""
    try:
        uc = UpdateUserPagePermissionsUseCase(_user_repo)
        dto = await uc.execute(UpdatePagePermissionsCommand(
            target_user_id=str(user_id),
            view_closing_form=body.view_closing_form,
            view_quotation=body.view_quotation,
            current_user_id=current_user.id,
        ))
        return _dto_to_user_read(dto)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)

@router.post(
    "/users/{user_id}/password-reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="重置用户密码（仅 superuser）",
)
async def reset_user_password(
    user_id: uuid.UUID,
    body: UserPasswordReset,
    current_user: CurrentUserPort = Depends(require_roles(ROLE_SUPERUSER)),
):
    """重置指定用户密码。"""

    try:
        uc = ResetUserPasswordUseCase(
            _user_repo,
            _password_hasher,
        )

        await uc.execute(
            ResetUserPasswordCommand(
                target_user_id=str(user_id),
                new_password=body.password,
                current_user_id=current_user.id,
                current_user_name=current_user.username,
            )
        )

    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=e.message,
        )

    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=e.message,
        )
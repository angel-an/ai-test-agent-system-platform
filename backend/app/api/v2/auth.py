"""
认证 API 模块

提供用户登录、注册和获取当前用户信息功能
"""

from datetime import timedelta
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_active_user
from app.config.settings import settings
from app.models.user import User
from app.auth.auth import create_access_token, get_password_hash, verify_password

router = APIRouter(prefix="/auth", tags=["认证"])


# ========== 请求/响应模型 ==========

class UserRegisterRequest(BaseModel):
    """用户注册请求"""
    email: EmailStr = Field(..., description="用户邮箱")
    username: str = Field(..., min_length=2, max_length=100, description="用户名")
    password: str = Field(..., min_length=6, max_length=100, description="密码")


class UserLoginRequest(BaseModel):
    """用户登录请求"""
    username: str = Field(..., description="用户账号")
    password: str = Field(..., description="密码")


class TokenResponse(BaseModel):
    """令牌响应"""
    access_token: str = Field(..., description="访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_in: int = Field(..., description="过期时间（秒）")


class UserInfoResponse(BaseModel):
    """用户信息响应"""
    id: UUID = Field(..., description="用户ID")
    email: str = Field(..., description="用户邮箱")
    username: str = Field(..., description="用户名")
    is_active: bool = Field(..., description="是否激活")


# ========== API 路由 ==========

@router.post(
    "/register",
    response_model=UserInfoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="用户注册",
    description="注册新用户账号",
)
async def register(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> UserInfoResponse:
    """用户注册"""
    # 检查邮箱是否已存在
    result = await db.execute(select(User).where(User.email == request.email))
    if result.first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该邮箱已被注册",
        )
    
    # 创建新用户
    user = User(
        id=uuid4(),
        email=request.email,
        username=request.username,
        password_hash=get_password_hash(request.password),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return UserInfoResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        is_active=user.is_active,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="用户登录",
    description="使用账号和密码登录，返回 JWT 访问令牌",
)
async def login(
    request: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """用户登录"""
    # 查询用户
    result = await db.execute(select(User).where(User.username == request.username))
    row = result.first()
    user: Optional[User] = row[0] if row else None

    # 验证用户和密码
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )
    
    # 生成 JWT Token
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "username": user.username},
        expires_delta=access_token_expires,
    )
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get(
    "/me",
    response_model=UserInfoResponse,
    summary="获取当前用户信息",
    description="获取当前登录用户的详细信息",
)
async def get_me(
    current_user: User = Depends(get_current_active_user),
) -> UserInfoResponse:
    """获取当前用户信息"""
    return UserInfoResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        is_active=current_user.is_active,
    )

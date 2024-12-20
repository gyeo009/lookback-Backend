# look-back 서비스 로그인 관련

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import httpx
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.models.user import User
from app.db.dynamo import put_calendar_list
from app.api.v1.endpoints import google

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

class GoogleAuthRequest(BaseModel):
    code: str

async def get_or_create_user(db: AsyncSession, email: str, name: str, google_id: str) -> tuple[User, bool]:
    # 기존 사용자 검색
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        # 새 사용자 생성
        user = User(
            email=email,
            full_name=name,
            google_id=google_id,
            is_new_user=True
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True

    return user, False

@router.post("/login")
async def google_login(
    auth_request: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        logger.info("Starting login process")
        print(auth_request.code)
        # 구글 콘솔에 access token 불러오기
        token_info = await google.get_access_token(auth_request.code)

        # 구글 콘솔에 사용자 정보 호출하도록 불러오기
        async with httpx.AsyncClient() as client:
            user_info_response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {token_info['access_token']}"}
            )
            user_info_response.raise_for_status()
            user_info = user_info_response.json()
            
            logger.info("dynamo function start")
            #캘린더 리스트 추출
            put_calendar_list(token_info['access_token'], user_info["email"])
            
            logger.info("dynamo function end")
            
            # DB에서 사용자 조회 또는 생성
            user, is_new_user = await get_or_create_user(
                db,
                email=user_info["email"],
                name=user_info.get("name", ""),
                google_id=user_info["id"]
            )
            
            return {
                "success": True,
                "isNewUser": is_new_user,
                "user": {
                    "email": user.email,
                    "name": user.full_name,
                    "picture": user_info.get("picture", "")
                }
            }
        

    except httpx.HTTPError as e:
        logger.error(f"HTTP error during Google API call: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=f"구글 인증 실패: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"내부 서버 오류: {str(e)}"
        )
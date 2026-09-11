from pydantic import BaseModel


class TokenPayload(BaseModel):
    sub: str
    exp: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field
import os

router = APIRouter(prefix="/settings", tags=["settings"])

class LinkedInCookieUpdate(BaseModel):
    """Payload to update the Selenium LinkedIn cookie.
    The value must be the full ``li_at=...`` string.
    """
    cookie: str = Field(..., description="Full li_at cookie value, e.g. 'li_at=abcd1234'")

@router.post("/linkedin_cookie", summary="Update LinkedIn Selenium cookie")
async def update_linkedin_cookie(payload: LinkedInCookieUpdate = Body(...)):
    # Basic validation – LinkedIn li_at cookie usually starts with 'li_at='
    if not payload.cookie.startswith("li_at="):
        raise HTTPException(status_code=400, detail="Cookie must start with 'li_at='")
    # Update .env file – simple in‑place replacement or append if missing
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(env_path):
        raise HTTPException(status_code=500, detail=".env file not found")
    # Read existing lines
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    # Replace or add line
    new_lines = []
    found = False
    for line in lines:
        if line.startswith("LINKEDIN_SELENIUM_COOKIE="):
            new_lines.append(f"LINKEDIN_SELENIUM_COOKIE={payload.cookie}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"LINKEDIN_SELENIUM_COOKIE={payload.cookie}\n")
    # Write back
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    # Also update the running process environment so the dispatcher sees it immediately
    os.environ["LINKEDIN_SELENIUM_COOKIE"] = payload.cookie
    return {"status": "success", "message": "LinkedIn Selenium cookie updated"}


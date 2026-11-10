import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from intake import parse_message
from workflow import Workflow

app = FastAPI(title="Support Agent Control Room", version="0.1.0")


@lru_cache
def database():
    return Workflow()


class SupportRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=1200)


@app.post("/requests")
def submit(payload: SupportRequest):
    result = parse_message(payload.message, os.environ.get("GROQ_API_KEY", "").strip())
    if "action" in result:
        return result
    if result["intent"] == "refund":
        return database().propose(result["order_id"])
    if result["intent"] == "status":
        order = database().lookup_order(result["order_id"])
        if order:
            return {"action": "order_status", "order": order}
    return {"action": "handoff", "reply": "Yêu cầu này cần nhân viên xem xét."}


@app.get("/cases/{case_id}")
def inspect_case(case_id: str):
    result = database().inspect(case_id)
    if not result:
        raise HTTPException(404, "Case not found.")
    return result

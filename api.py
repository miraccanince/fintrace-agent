from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from main import compare_datasets

app = FastAPI()


class Loan(BaseModel):
    loan_id: str | None = None
    outstanding_amount: Any = None
    currency: str | None = None


class CompareRequest(BaseModel):
    system_a: list[Loan]
    system_b: list[Loan]


@app.post("/compare")
def compare(request: CompareRequest) -> list[dict]:
    system_a = [loan.model_dump(exclude_none=True) for loan in request.system_a]
    system_b = [loan.model_dump(exclude_none=True) for loan in request.system_b]
    return compare_datasets(system_a, system_b)

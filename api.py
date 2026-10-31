from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from main import compare_datasets, parse_amount

app = FastAPI()


class Loan(BaseModel):
    loan_id: str | None = None
    outstanding_amount: Any = None
    currency: str | None = None


class CompareRequest(BaseModel):
    system_a: list[Loan]
    system_b: list[Loan]


def _loan_to_dict(loan: Loan) -> dict:
    data = loan.model_dump(exclude_none=True)
    if "outstanding_amount" in data:
        data["outstanding_amount"] = parse_amount(data["outstanding_amount"])
    return data


@app.post("/compare")
def compare(request: CompareRequest) -> list[dict]:
    system_a = [_loan_to_dict(loan) for loan in request.system_a]
    system_b = [_loan_to_dict(loan) for loan in request.system_b]
    results = compare_datasets(system_a, system_b)

    for result in results:
        if isinstance(result.get("difference"), Decimal):
            result["difference"] = str(result["difference"])

    return results

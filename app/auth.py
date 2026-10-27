"""本地演示用的角色凭证；正式部署应接入企业身份系统。"""

import hmac
import json
import os
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError


class Approver(BaseModel):
    actor: str
    role: Literal["technical", "procurement", "commercial"]
    token: str


def configured_approvers() -> list[Approver]:
    raw = os.getenv("QUOTE_APPROVERS_JSON", "").strip()
    if not raw:
        return []
    try:
        values = json.loads(raw)
        approvers = [Approver.model_validate(value) for value in values]
        if not approvers or any(not a.actor.strip() or len(a.token) < 16 for a in approvers):
            raise ValueError("审批人名称不能为空，令牌至少 16 字符")
        if len({a.token for a in approvers}) != len(approvers):
            raise ValueError("审批令牌不能重复")
        return approvers
    except (ValueError, TypeError, ValidationError) as exc:
        raise HTTPException(status_code=503, detail="审批凭证配置无效") from exc


def authorize(authorization: str | None, required_role: str | None = None) -> Approver | None:
    """配置凭证后所有私有接口均要求令牌；审批始终要求配置和角色。"""
    approvers = configured_approvers()
    if not approvers:
        if required_role:
            raise HTTPException(status_code=503, detail="尚未配置审批人员和凭证")
        return None  # 只供本机开发草稿流程使用。
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请提供审批访问令牌")
    token = authorization.removeprefix("Bearer ").strip()
    actor = next((a for a in approvers if hmac.compare_digest(token, a.token)), None)
    if actor is None:
        raise HTTPException(status_code=401, detail="访问令牌无效")
    if required_role and actor.role != required_role:
        raise HTTPException(status_code=403, detail="当前角色无权执行此审批")
    return actor

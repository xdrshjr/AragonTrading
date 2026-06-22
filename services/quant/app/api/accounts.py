"""路由：设置中心 · 实盘账户 REST（CRUD + default + test + test-draft + detail）(spec §4)。

错误体统一 `{error:{code,message}}`：边界错误（404/400/422）经 ``main`` 全局 HTTPException 处理器
包装（P2-ErrEnvelope）；OKX 鉴权 / 业务失败由 ``OKXPrivateError``（子类化 ``OKXError``）经全局
``okx_error_handler`` 统一回 502（P1-ErrHandler）。test 路由不抛而捕获归一为 `{ok:false}`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import repositories as repo
from ..db.session import get_session
from ..okx.accounts import AccountCredentials, fetch_account_detail, probe_account
from ..schemas import (
    OkxAccountDetailOut,
    OkxAccountIn,
    OkxAccountsListOut,
    OkxAccountTestReq,
    OkxAccountTestResult,
    OkxAccountUpdate,
    account_out,
)

router = APIRouter(tags=["accounts"])


def _not_found() -> None:
    raise HTTPException(status_code=404,
                        detail={"code": "ACCOUNT_NOT_FOUND", "message": "账户不存在"})


def _creds_from_row(row: repo.OkxAccount) -> AccountCredentials:
    """用库内明文密钥构造凭证（仅服务层内构造客户端用，绝不出接口）。"""
    return AccountCredentials(api_key=row.api_key, secret_key=row.secret_key,
                              passphrase=row.passphrase)


@router.get("/accounts", response_model=OkxAccountsListOut)
async def list_accounts(session: AsyncSession = Depends(get_session)) -> dict:
    """全量账户（脱敏）+ 当前默认 id。"""
    rows = await repo.list_okx_accounts(session)
    default = next((r for r in rows if r.is_default), None)
    return {"accounts": [account_out(r) for r in rows],
            "defaultId": default.id if default else None}


@router.post("/accounts", status_code=201)
async def create_account(body: OkxAccountIn,
                         session: AsyncSession = Depends(get_session)) -> dict:
    """新增账户；首账户（库内无账户）自动设为默认（添加即用）。"""
    values = repo.OkxAccountValues(
        label=body.label, api_key=body.apiKey, secret_key=body.secretKey,
        passphrase=body.passphrase, ip_whitelist=body.ipWhitelist, permissions=body.permissions)
    is_first = await repo.count_okx_accounts(session) == 0
    row = await repo.create_okx_account(session, values=values, is_default=is_first)
    return account_out(row)


@router.put("/accounts/{account_id}")
async def update_account(account_id: int, body: OkxAccountUpdate,
                         session: AsyncSession = Depends(get_session)) -> dict:
    """编辑账户（secretKey/passphrase 留空=保留原值）；不存在→404。"""
    patch = repo.OkxAccountPatch(
        label=body.label, api_key=body.apiKey, secret_key=body.secretKey,
        passphrase=body.passphrase, ip_whitelist=body.ipWhitelist, permissions=body.permissions)
    row = await repo.update_okx_account(session, account_id, patch=patch)
    if row is None:
        _not_found()
    return account_out(row)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: int,
                         session: AsyncSession = Depends(get_session)) -> Response:
    """删除账户；删默认后自动回落（id 最小者）；不存在→404。"""
    if await repo.delete_okx_account(session, account_id) == "not_found":
        _not_found()
    return Response(status_code=204)


@router.post("/accounts/{account_id}/default", response_model=OkxAccountsListOut)
async def set_default_account(account_id: int,
                             session: AsyncSession = Depends(get_session)) -> dict:
    """设为默认（事务清其余 is_default）；返回最新列表。"""
    if not await repo.set_default_okx_account(session, account_id):
        _not_found()
    return await list_accounts(session)


@router.post("/accounts/{account_id}/test", response_model=OkxAccountTestResult)
async def test_account(account_id: int,
                       session: AsyncSession = Depends(get_session)) -> dict:
    """测存量账户连通性（用库内 secret/passphrase）。"""
    row = await repo.get_okx_account(session, account_id)
    if row is None:
        _not_found()
    return await probe_account(_creds_from_row(row))


@router.post("/accounts/test", response_model=OkxAccountTestResult)
async def test_account_draft(body: OkxAccountTestReq) -> dict:
    """测草稿连通性（首存前；须带显式 apiKey/secretKey/passphrase）。"""
    return await probe_account(AccountCredentials(
        api_key=body.apiKey, secret_key=body.secretKey, passphrase=body.passphrase))


@router.get("/accounts/{account_id}/detail", response_model=OkxAccountDetailOut)
async def account_detail(account_id: int,
                         session: AsyncSession = Depends(get_session)) -> dict:
    """查详情：信息+资产+持仓（真实 OKX，鉴权失败→502 显式错误，绝不假数据）。"""
    row = await repo.get_okx_account(session, account_id)
    if row is None:
        _not_found()
    if not ((row.secret_key or "").strip() and (row.passphrase or "").strip()):
        raise HTTPException(status_code=400,
                            detail={"code": "ACCOUNT_NOT_CONFIGURED",
                                    "message": "请先补全 Secret Key / Passphrase"})
    return await fetch_account_detail(_creds_from_row(row))

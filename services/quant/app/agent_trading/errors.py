"""统一中文错误边界；不传播上游异常字符串或输入秘密。"""

from __future__ import annotations


_MESSAGES = {
    "ACCOUNT_REQUIRED": "请先选择交易账户",
    "ACCOUNT_NOT_FOUND": "交易账户不存在或已删除",
    "ACCOUNT_NOT_CONFIGURED": "交易账户凭据不完整",
    "BINDING_CHANGED": "账户或模式已变化，请重新生成预览",
    "ENVIRONMENT_CHANGED": "交易环境已变化，请核对原订单环境",
    "ACCOUNT_CREDENTIALS_CHANGED": "账户凭据已变化，请核对原订单",
    "TRADING_MODE_REQUIRED": "当前为本地模拟，切换到账户交易后可生成订单",
    "INVALID_TRADING_REQUEST": "交易参数不完整或格式错误，请补充明确指令",
    "INVALID_SIZE": "数量必须是有效正十进制字符串且满足最小张数",
    "PRECISION_UNSUPPORTED": "该数值精度无法安全转换，请调整数量或价格",
    "UNSUPPORTED_ACTION": "本期仅支持 USDT 本位线性合约及普通订单撤销",
    "ACCOUNT_MODE_UNSUPPORTED": "当前账户模式不支持对话交易",
    "POSITION_IDENTITY_UNAVAILABLE": "缺少仓位身份信息，无法安全生成平仓预览",
    "POSITION_CHANGED": "仓位已变化，请重新生成平仓预览",
    "INTENT_NOT_FOUND": "当前会话中不存在该交易意图",
    "ORDER_NOT_FOUND": "未查到目标订单，请核对交易所状态",
    "INTENT_VERSION_CONFLICT": "交易状态已更新，请刷新后查看",
    "INTENT_ALREADY_SUBMITTED": "该交易已提交，不能取消预览",
    "IDENTITY_MISMATCH": "交易所返回的订单身份不匹配",
    "PREVIEW_EXPIRED": "预览已过期，请发送新消息重新生成",
    "PREVIEW_CHANGED": "预览内容不匹配，请刷新后查看",
    "PREVIEW_STALE": "交易条件已变化，请重新生成预览",
    "TOO_MANY_TRADE_PREVIEWS": "有效预览已达上限，请取消旧预览后重试",
    "AUTO_TRADE_OWNS_ACCOUNT": "自动交易仍占用账户，请到托管面板处理",
    "TRADE_INTENT_IN_FLIGHT": "存在未结交易，请先查询其状态",
    "AGENT_TRADING_DISABLED": "对话交易功能已关闭",
    "LIVE_TRADING_DISABLED": "交易执行功能已关闭",
    "TOOL_DISABLED": "该交易工具已关闭",
    "STATE_STORE_UNAVAILABLE": "交易状态暂不可读取，请稍后查询",
    "TRADING_RECOVERY_PENDING": "交易状态恢复尚未完成，请稍后查询",
    "ACCOUNT_READ_FAILED": "账户读取失败，不能据此判断为空仓",
    "ACCOUNT_SNAPSHOT_INCOMPLETE": "账户委托信息不完整，暂不能生成开仓预览",
    "LEVERAGE_UNAVAILABLE": "无法核实当前杠杆，请检查账户设置",
    "ORDER_CAPACITY_UNAVAILABLE": "无法核实当前可开张数",
    "INSUFFICIENT_BALANCE": "可用保证金或可开张数不足",
    "AMBIGUOUS_TRADE": "请明确开平含义、数量单位和必要价格",
}
_NOT_FOUND = {"ACCOUNT_NOT_FOUND", "INTENT_NOT_FOUND", "ORDER_NOT_FOUND"}
_FORBIDDEN = {
    "AGENT_TRADING_DISABLED",
    "LIVE_TRADING_DISABLED",
    "TOOL_DISABLED",
    "TRADING_MODE_REQUIRED",
}
_BAD_REQUEST = {
    "INVALID_SIZE",
    "PRECISION_UNSUPPORTED",
    "UNSUPPORTED_ACTION",
    "ACCOUNT_MODE_UNSUPPORTED",
    "POSITION_IDENTITY_UNAVAILABLE",
    "INVALID_TRADING_REQUEST",
    "AMBIGUOUS_TRADE",
}
_UPSTREAM = {
    "ACCOUNT_READ_FAILED",
    "ACCOUNT_SNAPSHOT_INCOMPLETE",
    "LEVERAGE_UNAVAILABLE",
    "ORDER_CAPACITY_UNAVAILABLE",
}


def status_for(code: str) -> int:
    """返回稳定错误码的 HTTP 状态；未知本地冲突保守为 409。"""
    for codes, status in (
        (_NOT_FOUND, 404),
        (_FORBIDDEN, 403),
        (_BAD_REQUEST, 400),
        (_UPSTREAM, 502),
    ):
        if code in codes:
            return status
    if code == "TOO_MANY_TRADE_PREVIEWS":
        return 429
    if code in {"STATE_STORE_UNAVAILABLE", "TRADING_RECOVERY_PENDING"}:
        return 503
    return 409


class TradingError(Exception):
    """仅含稳定码及受控中文文案的应用错误。"""

    def __init__(self, code, message=None, status_code=None):
        self.code = code
        self.message = message or _MESSAGES.get(
            code, "交易暂无法处理，请核对状态"
        )
        self.status_code = status_code or status_for(code)
        super().__init__(code)

    def as_dict(self):
        """返回可直接用作 REST 或工具结果的安全错误。"""
        return {"error": {"code": self.code, "message": self.message}}

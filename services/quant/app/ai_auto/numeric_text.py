"""保留价格量纲和有效数字的文本格式化。"""
from decimal import Decimal, InvalidOperation


def format_price(value) -> str:
    """将有限价格展开为普通十进制；无效输入返回不可得。"""
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return "不可得"
    if not price.is_finite():
        return "不可得"
    if price.is_zero():
        return "0"
    text = format(price, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text

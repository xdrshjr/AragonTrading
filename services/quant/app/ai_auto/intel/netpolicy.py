"""解析结果三分类 + 合成地址段（ai-trade-silent-degradation-repair 轴 B-1）。纯函数，叶子模块。

**本模块必须是叶子**：`intel/` 包里只有 `search.py` 与 `taxonomy.py` 是叶子，新纯核心不做成叶子
就会与 `service.py` 循环导入（附录 D F-5，本包已为此付过一次代价）。故这里**不得** import
`service` / `runtime` / `arming`。

它要修的根因（§二 R-2）：`reader.resolve_is_public` 把「本机 `getaddrinfo` 的结果是不是公网地址」
当作「这个主机能不能访问」的判据。在透明 TUN / fake-IP 代理下（Clash / sing-box / Surge 一类，
本项目目标用户的标准配置），本机解析出来的地址**根本不是**将要连接的真实地址——它是代理分配的
一个占位符，且必然落在 `198.18.0.0/15`（RFC 2544 基准测试段），而 Python 的
`ipaddress.ip_address('198.18.0.194').is_private` 为 **True**。

本机实测：`coindesk.com → 198.18.0.194`、`coinbase.com → 198.18.0.195`、`binance.com →
198.18.0.196`、`tradingview.com → 198.18.0.197`、**`www.okx.com → 198.18.0.198`**。于是那道闸
在这类机器上对**每一个**域名都判假——生产实测 96 次抓取、`reader_ok = 0`、
`ai_intel_documents` 29 行全部 `status='blocked'`。

> **一道 fail-closed 的闸，如果它的判据在某类环境里恒假，这个功能在该类环境里就是零可用——
> 而「零可用」和「很安全」在日志里长得一模一样。**

修法不是放松闸，而是**换一个与真实连接有因果关系的判据**（连接建立后的对端地址），并把 DNS
预判从「否决权」降为「三分类里的一档」。
"""
from __future__ import annotations

import ipaddress
import logging
from collections.abc import Sequence
from typing import Literal

logger = logging.getLogger(__name__)

# RFC 2544 基准测试段：fake-IP 透明代理分配合成地址的事实标准。
SYNTHETIC_V4: tuple[ipaddress.IPv4Network, ...] = (ipaddress.ip_network("198.18.0.0/15"),)
# Teredo（`2001::/32`，IANA「IETF Protocol Assignments」特殊用途段的一部分）。
#
# **它不是设计文档列出来的，是实施节点在真机上量出来的**（见 spec §实施过程发现的方案缺陷 I-1）：
# 本机 `coinbase.com` 解析出 `['198.18.0.195', '2001::b33c:c110']`——IPv4 是合成段、IPv6 落在
# Teredo。而 `ipaddress.ip_address('2001::b33c:c110').is_private` 为 **True**，于是
# `classify_resolution` 的「混合了真实私有地址即拒」会把它判成 `private`，`auto` 档照样拒。
# 也就是说：只登记 IPv4 那一段的话，**任何带这类 AAAA 记录的域名在这台机器上仍然 0 成功**
# ——正是本轮要消灭的那个形状原封不动地复发一次。
#
# 为什么把它归进 `synthetic` 而不是 `public`：`2001::/32` 是 IPv6-over-IPv4 隧道的公共中转段，
# 既不是任何人的内网，也不该被当成一个可信的公网终点。归 `synthetic` 意味着它在 `auto` 档下
# 走「域名闸 + 出网后对端复核」，在 `strict` 档下**仍然被拒**——两档的语义都仍然成立。
#
# 刻意**不**登记 `fc00::/7`（ULA，Clash-meta 的 IPv6 fake-ip 默认段 `fd00::/18` 在它里面）：
# ULA 是真实可达的内网地址族，把它整段判成合成就是真的放松了 SSRF 防护。用那种配置的用户
# 走 `AI_TRADE_INTEL_READER_SYNTHETIC_CIDRS` 显式登记——一次显式的、写在配置里的放宽，
# 与一次藏在常量表里的放宽不是一回事。
SYNTHETIC_V6: tuple[ipaddress.IPv6Network, ...] = (ipaddress.ip_network("2001::/32"),)

Resolution = Literal["public", "private", "synthetic", "unresolved"]
AddrClass = Literal["public", "private", "synthetic"]

# CGNAT：`ipaddress` 不把它算作 private，但它同样能到达运营商内网（与 `reader._CGNAT` 同源）。
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def parse_cidrs(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """解析逗号分隔的 CIDR 列表（`ai_trade_intel_reader_synthetic_cidrs`）。

    **解析失败的条目跳过并 `warning` 一次，绝不整体失败**：一个打错的 CIDR 不该把整道闸变成
    `unresolved`——那正是本轮要消灭的形状（一个配置手误让整个功能零可用，而日志里看不出区别）。
    """
    out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in str(raw or "").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("ai-trade intel netpolicy: 跳过无法解析的合成地址段 %r", item)
    return tuple(out)


def classify_addr(raw: str,
                  extra_synthetic: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network] = (),
                  ) -> AddrClass:
    """单个地址三分类。

    **`synthetic` 优先于 `private`**（`198.18.0.0/15` 两者都命中，顺序不可换）：如果先判
    private，这道闸就退化回今天的样子，本轴一行代码都不生效。

    无法解析的字面量按 `private` 处理（保守）——一个读不懂的地址不该被当成公网放行。

    Args:
        raw: IP 字面量（IPv6 的 `%scope` 后缀须由调用方先去掉）。
        extra_synthetic: 追加的合成段（用户配置，见 `parse_cidrs`）。

    Returns:
        `public` | `private` | `synthetic`。
    """
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return "private"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    for net in (*SYNTHETIC_V4, *SYNTHETIC_V6, *extra_synthetic):
        if ip.version == net.version and ip in net:
            return "synthetic"
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return "private"
    if ip.version == 4 and ip in _CGNAT:
        return "private"
    return "public"


def is_unroutable_sentinel(raw: str) -> bool:
    """这个「地址」是不是一个**根本连不上的哨兵**（`::` / `0.0.0.0`）。

    **它们必须在整组判定之前被丢掉，而不是被当成「真实私有地址」。**

    实施节点在真机上量到（spec §实施过程发现的方案缺陷 I-3）：某些解析器对 `binance.com` 的
    AAAA 查询回 `::`（未指定地址），于是 `getaddrinfo` 的结果是
    `['198.18.0.196', '::']`——IPv4 是合成段、IPv6 是这个哨兵。`ipaddress` 判 `is_unspecified`
    为真 ⇒ 上面「混合了真实私有地址即拒」那条把整组判成 `private` ⇒ **这个域名被拒，而且时灵
    时不灵**（同一台机器上下一次解析可能就不带 `::` 了）。间歇性的静默拦截比稳定的更难查。

    把它算成「内网」是一个范畴错误：`::` 不是任何人的内网，它根本不是一个可以连接的目的地。
    真要只有它一个，连接本来就会失败，而那是一次**诚实的、归因正确的**网络失败。
    """
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return ip.is_unspecified


def classify_resolution(addrs: Sequence[str],
                        extra_synthetic: Sequence[ipaddress.IPv4Network
                                                  | ipaddress.IPv6Network] = (),
                        ) -> Resolution:
    """整组地址 → 一个结论。**判据顺序不可换**：

    0. 先丢掉连不上的哨兵（`::` / `0.0.0.0`，见 `is_unroutable_sentinel`）；
    1. 空 ⇒ `unresolved`（解析失败是一件独立的事，把它记成 `private_host` 会让「本机 DNS 坏了」
       与「这个域名指向内网」在台账上长得一模一样）；
    2. 任一 `private` ⇒ `private`——**混合了真实私有地址即拒**，绝不因为里面有合成地址而放行
       （这是本轴唯一一处「宁可少抓一篇」的地方，也是 B-4 安全评估成立的前提）；
    3. 全部 `synthetic` ⇒ `synthetic`；
    4. 其余 ⇒ `public`。
    """
    usable = [str(addr) for addr in addrs if not is_unroutable_sentinel(str(addr))]
    if not usable:
        return "unresolved"
    kinds = [classify_addr(addr, extra_synthetic) for addr in usable]
    if any(kind == "private" for kind in kinds):
        return "private"
    if all(kind == "synthetic" for kind in kinds):
        return "synthetic"
    return "public"


def is_synthetic_addr(raw: str,
                      extra_synthetic: Sequence[ipaddress.IPv4Network
                                                | ipaddress.IPv6Network] = ()) -> bool:
    """便捷谓词（对端复核那一段只需要这一个问题的答案）。"""
    return classify_addr(raw, extra_synthetic) == "synthetic"


def normalize_peer(server_addr: object) -> str | None:
    """`httpx` 的 `server_addr` → 可分类的地址串；取不出 ⇒ `None`（**视为未知，不拒**）。

    三条硬性约束，缺任何一条这段代码就会在一类真实机器上把抓取变成 100% 失败：

    1. **`server_addr` 是 `(host, port)` 元组，不是字符串。** 直接喂给 `ipaddress.ip_address`
       会抛 `ValueError`，而这段跑在**已经拿到响应之后**——一个会抛的复核函数等于把「抓到了
       正文」变成「抓取器自己崩了」。
    2. **IPv6 的 `%scope` 后缀要去掉**（沿用 `resolve_is_public` 的既有写法 `split("%", 1)[0]`）。
    3. **取不到即 `None`**（HTTP/2、非 httpcore transport、`MockTransport`、测试替身）——否则
       一次 `httpx` 版本变化会静默关掉整个功能，本仓已在「`websockets` 15 的 `proxy` 默认值」
       上栽过一次同形的跟头。
    """
    if server_addr is None:
        return None
    value = server_addr
    if isinstance(value, (tuple, list)):
        if not value:
            return None
        value = value[0]
    text = str(value or "").strip()
    if not text:
        return None
    return text.split("%", 1)[0] or None

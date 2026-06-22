"""
版权合规模块接口
=================
在上传小说时校验版权，拒绝未授权内容并引导用户到正版渠道。

所需资源:
    - 与起点中文网 / 晋江文学城 等平台的商业合作 API 权限
    - 或接入第三方版权数据库（如中国版权保护中心）

当前状态:
    接口已定义，实现需要签订商务协议后对接平台 API。
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class CopyrightStatus(Enum):
    """版权校验状态"""
    VERIFIED = "verified"               # 版权确认（正版授权）
    UNVERIFIED = "unverified"           # 无法确认（需要用户自证）
    VIOLATION = "violation"             # 明确侵权
    PENDING_REVIEW = "pending_review"   # 需人工审核


@dataclass
class CopyrightResult:
    """版权校验结果"""
    novel_name: str
    author: str
    status: CopyrightStatus
    platform: str = ""                   # 校验平台，如 "qidian"
    evidence: dict | None = None         # 校验证据（平台返回的授权信息）
    message: str = ""                    # 用户可见的提示信息
    suggested_action: str = ""           # 建议操作: "proceed" / "redirect_to_platform" / "block"

    def to_api_response(self) -> dict:
        """转为 API 响应格式，供前端展示"""
        return {
            "novel_name": self.novel_name,
            "author": self.author,
            "status": self.status.value,
            "message": self.message,
            "suggested_action": self.suggested_action,
        }


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------

class CopyrightVerifier(abc.ABC):
    """
    版权校验器。

    设计原则:
        1. 校验不通过时**不删除用户文件**，只返回阻止编译的标记
        2. 所有校验记录留审计日志
        3. 前端根据 suggested_action 展示不同 UI

    TODO (需要商务合作):
        1. 与起点中文网 / 晋江文学城 签订开发者合作协议
        2. 获取平台 API 访问密钥
        3. 在 config.yaml 中配置 copyright.api_key
    """

    @abc.abstractmethod
    async def verify(self, novel_name: str, author: str) -> CopyrightResult:
        """
        校验一部小说的版权状态。

        实现时需:
            1. 调用平台 API 查询 {novel_name} + {author} 是否存在正版授权
            2. 如果平台返回授权信息，返回 VERIFIED + evidence
            3. 如果平台返回无记录，返回 UNVERIFIED（非直接拒绝，需用户自证）
            4. 如果平台明确标记为盗版内容，返回 VIOLATION

        Notes for implementer:
            以起点中文网为例，假设 API 端点:
                GET https://api.qidian.com/copyright/verify
                Params: {"book_name": str, "author": str}
                Response: {"verified": bool, "platform_book_id": str, ...}

            但实际不存在公开的版权校验 API，
            需要与平台商务对接获取授权。
        """
        ...

    @abc.abstractmethod
    async def batch_verify(self, items: list[tuple[str, str]]) -> list[CopyrightResult]:
        """
        批量校验（编译进度页面可用）。
        """
        ...

    @abc.abstractmethod
    async def report_violation(self, result: CopyrightResult) -> str:
        """
        提交侵权举报到平台（当检测到盗版内容时）。

        返回: 举报工单 ID
        """
        ...

    @abc.abstractmethod
    def get_legal_redirect(self, novel_name: str, author: str) -> str | None:
        """
        获取正版阅读链接。

        当校验失败时，前端可展示一个"去正版阅读"按钮跳转到此 URL。

        Returns:
            str: 正版 URL (如 https://book.qidian.com/info/xxx)
            None: 无法确定正版渠道
        """
        ...


# ---------------------------------------------------------------------------
# 默认实现 (所有校验通过)
# ---------------------------------------------------------------------------

class PermissiveVerifier(CopyrightVerifier):
    """
    宽松版权校验器 —— 不校验，默认全部通过。

    在未与平台签约前使用此实现，保证开发流程不阻塞。
    """

    async def verify(self, novel_name: str, author: str) -> CopyrightResult:
        return CopyrightResult(
            novel_name=novel_name,
            author=author,
            status=CopyrightStatus.UNVERIFIED,
            message=f"版权校验服务暂未接入。《{novel_name}》将继续处理，"
                    f"请确保您有合法的阅读权限。",
            suggested_action="proceed",
        )

    async def batch_verify(self, items: list[tuple[str, str]]) -> list[CopyrightResult]:
        return [await self.verify(name, author) for name, author in items]

    async def report_violation(self, result: CopyrightResult) -> str:
        return "N/A"

    def get_legal_redirect(self, novel_name: str, author: str) -> str | None:
        # 尝试拼接已知平台搜索链接（不是 API，只是帮助页）
        return f"https://www.qidian.com/search?kw={novel_name}"


# ---------------------------------------------------------------------------
# 未来实现占位
# ---------------------------------------------------------------------------

class QidianVerifier(CopyrightVerifier):
    """
    起点中文网版权校验器。

    🚫 需要商业合作，当前为接口桩代码。

    接入流程:
        1. 联系起点中文网开放平台 (https://open.qidian.com/)
        2. 注册开发者账号，创建应用
        3. 获取 AppKey + AppSecret
        4. 配置 config.yaml:
            ```yaml
            copyright:
                provider: qidian
                app_key: "${QIDIAN_APP_KEY}"
                app_secret: "${QIDIAN_APP_SECRET}"
            ```
        5. 实现以下三个接口的调用逻辑
    """

    async def verify(self, novel_name: str, author: str) -> CopyrightResult:
        raise NotImplementedError(
            "起点中文网版权校验需要商务合作。\n"
            "请按以下步骤接入:\n"
            "  1. 访问 https://open.qidian.com/ 注册开发者\n"
            "  2. 获取 AppKey 和 AppSecret\n"
            "  3. 配置环境变量 QIDIAN_APP_KEY / QIDIAN_APP_SECRET\n"
            "  4. 将 QidianVerifier 注册为 CopyrightVerifier 的实现\n\n"
            "当前使用 PermissiveVerifier，所有上传默认通过。"
        )

    async def batch_verify(self, items: list[tuple[str, str]]) -> list[CopyrightResult]:
        raise NotImplementedError

    async def report_violation(self, result: CopyrightResult) -> str:
        raise NotImplementedError

    def get_legal_redirect(self, novel_name: str, author: str) -> str | None:
        return f"https://www.qidian.com/search?kw={novel_name}"


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def get_copyright_verifier(config: dict | None = None) -> CopyrightVerifier:
    """
    根据配置返回版权校验器实例。

    未配置特定 provider 时返回 PermissiveVerifier（全部放行）。
    """
    if config is None:
        return PermissiveVerifier()

    provider = config.get("provider", "permissive")
    if provider == "qidian":
        return QidianVerifier()
    # 未来可扩展: jjwxc(晋江), zongheng(纵横), etc.
    return PermissiveVerifier()

"""共享错误类型。

单独放一个模块,避免 registry / card_brief / batch 反向依赖 cli 造成循环导入。
"""

from __future__ import annotations


class ImageFactoryError(Exception):
    """面向用户的可读错误,CLI 捕获后打印 error: 前缀并以退出码 2 结束。"""

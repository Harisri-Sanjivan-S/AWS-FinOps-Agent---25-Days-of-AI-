"""Code-fix executors. Each turns an approved case into a pull request on the demo monorepo, behind the same checks."""

from detective.fixers.base import CodeFixer, FixError, FixResult
from detective.fixers.devin import DevinFixer
from detective.fixers.template import TemplateFixer

__all__ = ["CodeFixer", "DevinFixer", "FixError", "FixResult", "TemplateFixer", "make_fixer"]


def make_fixer(name: str | None = None, **kwargs) -> CodeFixer:
    from detective.config import get_settings
    name = name or get_settings().fixer
    if name == "template":
        return TemplateFixer(**kwargs)
    if name == "devin":
        return DevinFixer(**kwargs)
    raise ValueError(f"Unknown fixer {name!r} (use 'template' or 'devin')")

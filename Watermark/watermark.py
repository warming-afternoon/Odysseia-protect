"""兼容实验命令的入口；正式实现位于 :mod:`src.traceability.watermark`。"""

from src.traceability.watermark import *  # noqa: F403
from src.traceability.watermark import main


if __name__ == "__main__":
    raise SystemExit(main())

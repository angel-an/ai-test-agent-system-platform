"""受控管理命令（rev48，P1 修复配套）。

生产环境授予/撤销超管必须走显式命令（或人工 DB 操作），
禁止启动时自动提升。

用法：
    python -m app.cli grant-superuser <username>
    python -m app.cli revoke-superuser <username>
"""

import argparse
import asyncio

from sqlalchemy import select

from app.config.database import async_session_factory
from app.models.user import User


async def _set_superuser(username: str, value: bool) -> int:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            print(f"[cli] 用户不存在: {username}")
            return 1
        user.is_superuser = value
        await session.commit()
        print(f"[cli] {username}: is_superuser -> {value}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="受控管理命令（超管授予/撤销）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    grant = sub.add_parser("grant-superuser", help="授予超管权限")
    grant.add_argument("username", help="目标用户名")
    revoke = sub.add_parser("revoke-superuser", help="撤销超管权限")
    revoke.add_argument("username", help="目标用户名")

    args = parser.parse_args()
    value = args.cmd == "grant-superuser"
    raise SystemExit(asyncio.run(_set_superuser(args.username, value)))


if __name__ == "__main__":
    main()

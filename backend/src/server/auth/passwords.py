"""密码哈希。只使用 argon2id，明文不入库。"""

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """生成 argon2id 密码哈希。"""

    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码。哈希损坏或算法不符时视为失败。"""

    try:
        return bool(_hasher.verify(password_hash, password))
    except (VerifyMismatchError, VerificationError):
        return False

"""
services/totp_service.py — Time-based One-Time Password (TOTP) & recovery codes helper.
"""
import hashlib
import json
import secrets
import string
import pyotp
import segno


def generate_secret() -> str:
    """Generate a new 32-character Base32 TOTP secret key."""
    return pyotp.random_base32()


def get_provisioning_uri(username: str, secret: str, issuer_name: str = "Admin Panel") -> str:
    """Get otpauth:// URI suitable for scanning into an authenticator app."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=issuer_name)


def generate_qr_code_svg(provisioning_uri: str) -> str:
    """Render a provisioning URI as an inline SVG data URI using Segno."""
    qr = segno.make(provisioning_uri, error='M')
    return qr.to_uri(kind='svg')


def verify_totp(secret: str, code: str, valid_window: int = 1) -> bool:
    """
    Verify a 6-digit TOTP code against a Base32 secret key.
    Allows valid_window (default 1 step = ±30s) to tolerate minor client time drift.
    """
    if not secret or not code:
        return False
    clean_code = str(code).strip().replace(" ", "").replace("-", "")
    if len(clean_code) != 6 or not clean_code.isdigit():
        return False
    try:
        totp = pyotp.TOTP(secret)
        return totp.verify(clean_code, valid_window=valid_window)
    except Exception:
        return False


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def generate_recovery_codes(count: int = 8) -> tuple[list[str], str]:
    """
    Generate plain-text single-use recovery codes and a JSON string of their hashes.
    Returns (plain_codes, hashed_codes_json).
    Format of plain codes: XXXX-XXXX (e.g. 4B8A-9F12)
    """
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace("0", "").replace("O", "").replace("1", "").replace("I", "")  # avoid confusion
    
    plain_codes = []
    hashed_list = []
    
    for _ in range(count):
        part1 = "".join(secrets.choice(alphabet) for _ in range(4))
        part2 = "".join(secrets.choice(alphabet) for _ in range(4))
        code = f"{part1}-{part2}"
        plain_codes.append(code)
        hashed_list.append(_hash_code(code))
        
    return plain_codes, json.dumps(hashed_list)


def verify_and_consume_recovery_code(
    recovery_codes_json: str | None, raw_code: str
) -> tuple[bool, str | None]:
    """
    Check if raw_code matches an unused recovery code in recovery_codes_json.
    If valid, consumes the code and returns (True, updated_json_string).
    Otherwise returns (False, original_json_string).
    """
    if not recovery_codes_json or not raw_code:
        return False, recovery_codes_json
        
    clean_code = raw_code.strip().upper()
    hashed_target = _hash_code(clean_code)
    
    try:
        codes = json.loads(recovery_codes_json)
        if not isinstance(codes, list):
            return False, recovery_codes_json
            
        if hashed_target in codes:
            codes.remove(hashed_target)
            return True, json.dumps(codes)
    except Exception:
        pass
        
    return False, recovery_codes_json

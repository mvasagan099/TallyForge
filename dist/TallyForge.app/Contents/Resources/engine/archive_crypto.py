"""TallyForge AES-256-GCM snapshots. This format is not TallyVault."""
import hashlib
import os
from pathlib import Path
import tempfile

MAGIC = b'TFVAULT2'


def encrypt_snapshot(db, password, output):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ValueError('Install cryptography from requirements-optional.txt to use AES-GCM archives') from exc
    if len(password) < 12:
        raise ValueError('Archive password must have at least 12 characters')
    with tempfile.TemporaryDirectory() as folder:
        source = db.create_local_backup(folder)
        plain = Path(source).read_bytes()
    salt, nonce = os.urandom(16), os.urandom(12)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    header = MAGIC + salt + nonce
    ciphertext = AESGCM(key).encrypt(nonce, plain, header)
    with open(output, 'xb') as target:
        os.chmod(output, 0o600)
        target.write(header + ciphertext)
    return {'archive': str(output), 'format': 'TallyForge AES-256-GCM snapshot', 'native_tallyvault': False}


def decrypt_snapshot(source, password, output):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    raw = Path(source).read_bytes()
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC)+44:
        raise ValueError('Not a TallyForge AES-GCM archive')
    offset = len(MAGIC)
    salt, nonce = raw[offset:offset+16], raw[offset+16:offset+28]
    header = raw[:offset+28]
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    try:
        plain = AESGCM(key).decrypt(nonce, raw[offset+28:], header)
    except InvalidTag as exc:
        raise ValueError('Incorrect password or modified archive') from exc
    with open(output, 'xb') as target:
        os.chmod(output, 0o600)
        target.write(plain)
    return str(output)

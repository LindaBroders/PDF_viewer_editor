"""Digital certificate signatures and timestamping via pyHanko.

Supports creating a self-signed certificate (for personal use / testing) and
applying a cryptographic digital signature to a PDF, optionally with an RFC 3161
timestamp from a Time Stamping Authority.
"""

from __future__ import annotations

import datetime
import os

from .external import CRYPTOGRAPHY, PYHANKO, require


def create_self_signed_cert(
    out_pfx: str, common_name: str, password: str, days_valid: int = 3650
) -> str:
    """Create a self-signed certificate saved as a password-protected PKCS#12 (.pfx).

    Suitable for personal signing and testing. For legally recognized signatures
    you need a certificate from a trusted Certificate Authority instead.
    """
    require(CRYPTOGRAPHY)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days_valid))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    blob = pkcs12.serialize_key_and_certificates(
        name=common_name.encode(),
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
    )
    with open(out_pfx, "wb") as fh:
        fh.write(blob)
    return out_pfx


def sign_pdf(
    input_path: str,
    output_path: str,
    pfx_path: str,
    passphrase: str,
    field_name: str = "Signature1",
    reason: str = "",
    location: str = "",
    timestamp_url: str | None = None,
) -> str:
    """Apply a digital signature to a PDF using a PKCS#12 certificate.

    ``timestamp_url`` may point at an RFC 3161 TSA (e.g.
    ``https://freetsa.org/tsr``) to embed a trusted timestamp.
    """
    require(PYHANKO)
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)
    if not os.path.exists(pfx_path):
        raise FileNotFoundError(pfx_path)

    from pyhanko.sign import signers
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    signer = signers.SimpleSigner.load_pkcs12(
        pfx_file=pfx_path, passphrase=passphrase.encode()
    )
    if signer is None:
        raise RuntimeError("Could not load the certificate (wrong password?).")

    timestamper = None
    if timestamp_url:
        from pyhanko.sign.timestamps import HTTPTimeStamper

        timestamper = HTTPTimeStamper(timestamp_url)

    meta = signers.PdfSignatureMetadata(
        field_name=field_name, reason=reason or None, location=location or None
    )
    pdf_signer = signers.PdfSigner(meta, signer=signer, timestamper=timestamper)

    with open(input_path, "rb") as inf:
        writer = IncrementalPdfFileWriter(inf)
        with open(output_path, "wb") as outf:
            pdf_signer.sign_pdf(writer, output=outf)
    return output_path

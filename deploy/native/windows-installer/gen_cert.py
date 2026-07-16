# Generate a static self-signed TLS cert for the native Windows appliance.
#
#   python gen_cert.py <lan-ip> <cert.pem> <key.pem>
#
# Caddy's `tls internal` on a port-only site (:8443) does NOT present a cert for
# IP/localhost access without SNI, and — running as a LocalSystem service — it
# also fails to install its local CA into the Windows trust store ("The request
# is not supported"). Both leave the TLS handshake broken (ERR_SSL_PROTOCOL_ERROR
# in the browser, SEC_E_INTERNAL_ERROR in schannel). A static self-signed cert
# with SANs for localhost + 127.0.0.1 + the LAN IP sidesteps all of it: the
# handshake completes and the client just gets the normal self-signed warning.
import datetime
import ipaddress
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ip, certp, keyp = sys.argv[1], sys.argv[2], sys.argv[3]

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Kanagatly VMS")])

san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
try:
    addr = ipaddress.ip_address(ip)
    if addr not in (ipaddress.ip_address("127.0.0.1"),):
        san.append(x509.IPAddress(addr))
except ValueError:
    pass  # not an IP (e.g. blank) — localhost/127.0.0.1 still cover local access

now = datetime.datetime.utcnow()
cert = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(days=1))
    .not_valid_after(now + datetime.timedelta(days=3650))
    .add_extension(x509.SubjectAlternativeName(san), critical=False)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .sign(key, hashes.SHA256())
)

with open(keyp, "wb") as f:
    f.write(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
with open(certp, "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print(f"self-signed cert written for {ip} (+localhost,127.0.0.1) -> {certp}")

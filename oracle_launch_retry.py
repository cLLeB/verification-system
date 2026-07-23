"""Keep asking Oracle for an Ampere A1 instance until it says yes.

Ampere capacity in most regions is exhausted almost all the time, and what free
capacity appears is gone within minutes. Sitting in the console clicking Create
is a poor way to catch that; a loop is a good one. This retries LaunchInstance on
a fixed interval, stops the instant it succeeds, and prints the public IP.

Setup (once):

    .\\venv\\Scripts\\pip install oci
    oci setup config          # generates ~/.oci/config + an API keypair

``oci setup config`` asks for your user OCID, tenancy OCID and region, then writes
a public key to ~/.oci/oci_api_key_public.pem — paste that into the console under
Identity -> My profile -> API keys. Everything else this script discovers for you.

Check the setup without launching anything:

    .\\venv\\Scripts\\python oracle_launch_retry.py --check

Then leave it running:

    .\\venv\\Scripts\\python oracle_launch_retry.py

It will try 2 OCPU / 12 GB (the Always Free ceiling since June 2026). Add
``--fallback`` to alternate with 1 OCPU / 6 GB, which fits into fragmented
capacity a larger request cannot.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

try:
    import oci
except ImportError:
    raise SystemExit("pip install oci   (then run: oci setup config)")

# Retry on these; anything else is a real problem and looping would just hide it.
_CAPACITY = ("out of host capacity", "outofhostcapacity", "outofcapacity",
             "insufficient capacity")
_TRANSIENT_STATUS = (429, 500, 503)


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# --- credentials ------------------------------------------------------------
OCI_DIR = os.path.join(os.path.expanduser("~"), ".oci")
KEY_PEM = os.path.join(OCI_DIR, "oci_api_key.pem")
PUB_PEM = os.path.join(OCI_DIR, "oci_api_key_public.pem")
CONFIG = os.path.join(OCI_DIR, "config")


def _fingerprint(pub_der: bytes) -> str:
    """OCI shows the API key fingerprint as colon-separated MD5 of the DER key."""
    import hashlib
    digest = hashlib.md5(pub_der).hexdigest()          # noqa: S324 - OCI's format
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def setup_credentials(force: bool = False) -> int:
    """Generate an API keypair and write ~/.oci/config, replacing `oci setup config`.

    The official CLI is a large separate install whose only job here is this; the
    cryptography library is already a dependency, so we do it directly."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if os.path.exists(CONFIG) and not force:
        raise SystemExit(f"{CONFIG} already exists — pass --setup --force to replace it.")

    print("\nFind these in the Oracle console (top-right avatar):")
    print("  user OCID    : My profile      -> OCID (starts ocid1.user...)")
    print("  tenancy OCID : Tenancy: <name> -> OCID (starts ocid1.tenancy...)\n")
    user = input("user OCID    : ").strip()
    tenancy = input("tenancy OCID : ").strip()
    region = input("region [eu-paris-1]: ").strip() or "eu-paris-1"
    if not user.startswith("ocid1.user") or not tenancy.startswith("ocid1.tenancy"):
        raise SystemExit("those don't look like OCIDs — expected ocid1.user... and "
                         "ocid1.tenancy...")

    os.makedirs(OCI_DIR, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open(KEY_PEM, "wb") as fh:
        fh.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    pub_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    with open(PUB_PEM, "wb") as fh:
        fh.write(pub_pem)
    try:
        os.chmod(KEY_PEM, 0o600)
    except OSError:
        pass

    fp = _fingerprint(pub_der)
    with open(CONFIG, "w", encoding="utf-8") as fh:
        fh.write("[DEFAULT]\n"
                 f"user={user}\n"
                 f"fingerprint={fp}\n"
                 f"tenancy={tenancy}\n"
                 f"region={region}\n"
                 f"key_file={KEY_PEM}\n")

    print(f"\nwrote {CONFIG}")
    print(f"fingerprint: {fp}")
    print("\n" + "=" * 68)
    print("NOW paste this public key into the console:")
    print("  avatar -> My profile -> API keys -> Add API key -> Paste a public key")
    print("=" * 68)
    print(pub_pem.decode())
    print("=" * 68)
    print(f"(also saved at {PUB_PEM})")
    print("\nAfter adding it, the fingerprint shown in the console must match the")
    print("one above. Then run:  python oracle_launch_retry.py --check\n")
    return 0


def load_clients():
    try:
        cfg = oci.config.from_file()
        oci.config.validate_config(cfg)
    except oci.exceptions.ConfigFileNotFound:
        raise SystemExit(
            "\nNo Oracle API credentials yet. Create them with this script:\n"
            "\n    python oracle_launch_retry.py --setup\n"
            "\nIt generates the keypair, writes ~/.oci/config, and prints the public\n"
            "key for you to paste into the console. No extra tooling needed.\n")
    except oci.exceptions.InvalidConfig as exc:
        raise SystemExit(f"\n~/.oci/config is incomplete: {exc}\n"
                         f"Rebuild it with:  python oracle_launch_retry.py --setup --force\n")
    return cfg, oci.core.ComputeClient(cfg), oci.core.VirtualNetworkClient(cfg), \
        oci.identity.IdentityClient(cfg)


def discover(cfg, compute, network, identity, args) -> dict:
    """Everything LaunchInstance needs, resolved from names rather than OCIDs."""
    tenancy = cfg["tenancy"]
    compartment = args.compartment or tenancy

    ads = identity.list_availability_domains(compartment_id=tenancy).data
    ad = next((a for a in ads if args.ad.lower() in a.name.lower()), ads[0]) \
        if args.ad else ads[0]

    vcns = network.list_vcns(compartment_id=compartment).data
    vcn = next((v for v in vcns if v.display_name == args.vcn_name), None)
    if vcn is None:
        raise SystemExit(f"no VCN named {args.vcn_name!r}. Found: "
                         f"{[v.display_name for v in vcns]}")

    subnets = network.list_subnets(compartment_id=compartment, vcn_id=vcn.id).data
    subnet = next((s for s in subnets
                   if "public" in s.display_name.lower() and not s.prohibit_public_ip_on_vnic),
                  None)
    if subnet is None:
        raise SystemExit(f"no public subnet in {args.vcn_name}. Found: "
                         f"{[s.display_name for s in subnets]}")

    images = compute.list_images(
        compartment_id=compartment, operating_system="Canonical Ubuntu",
        operating_system_version=args.os_version, shape=args.shape,
        sort_by="TIMECREATED", sort_order="DESC").data
    if not images:
        raise SystemExit(f"no {args.os_version} image for {args.shape}")

    return {"compartment": compartment, "ad": ad.name, "subnet": subnet.id,
            "subnet_name": subnet.display_name, "image": images[0].id,
            "image_name": images[0].display_name}


def ssh_key(path: str | None) -> str:
    """The public half of the key pair Oracle generated during the wizard."""
    candidates = [path] if path else []
    home = os.path.expanduser("~")
    candidates += [os.path.join(home, "Downloads"), os.path.join(home, ".ssh")]
    for c in candidates:
        if c and os.path.isfile(c):
            return open(c, encoding="utf-8").read().strip()
        if c and os.path.isdir(c):
            pubs = sorted((f for f in os.listdir(c) if f.endswith(".pub")),
                          key=lambda f: os.path.getmtime(os.path.join(c, f)),
                          reverse=True)
            if pubs:
                found = os.path.join(c, pubs[0])
                log(f"using SSH public key {found}")
                return open(found, encoding="utf-8").read().strip()
    raise SystemExit("no SSH public key found — pass --ssh-key <path to .pub>")


def launch(compute, found, key, name, ocpus, memory, shape):
    kwargs = dict(
        compartment_id=found["compartment"],
        availability_domain=found["ad"],
        display_name=name,
        shape=shape,
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=found["image"]),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=found["subnet"], assign_public_ip=True),
        metadata={"ssh_authorized_keys": key},
    )
    # Only FLEX shapes take (and require) a shape_config. Fixed shapes like
    # E2.1.Micro reject it — their CPU/memory is baked into the shape.
    if shape.endswith(".Flex"):
        kwargs["shape_config"] = oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=float(ocpus), memory_in_gbs=float(memory))
    return compute.launch_instance(
        oci.core.models.LaunchInstanceDetails(**kwargs)).data


def public_ip(cfg, compute, network, instance) -> str:
    net = oci.core.ComputeClient(cfg)
    for _ in range(30):
        vnics = net.list_vnic_attachments(compartment_id=instance.compartment_id,
                                          instance_id=instance.id).data
        if vnics and vnics[0].vnic_id:
            v = network.get_vnic(vnics[0].vnic_id).data
            if v.public_ip:
                return v.public_ip
        time.sleep(5)
    return "(not assigned yet — check the console)"


def is_capacity(exc) -> bool:
    text = f"{getattr(exc, 'code', '')} {getattr(exc, 'message', exc)}".lower()
    return any(k in text for k in _CAPACITY)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="instance-verification")
    ap.add_argument("--shape", default="VM.Standard.A1.Flex")
    ap.add_argument("--ocpus", type=int, default=2)
    ap.add_argument("--memory", type=int, default=12)
    ap.add_argument("--fallback", action="store_true",
                    help="alternate with 1 OCPU / 6 GB, which fits smaller gaps")
    ap.add_argument("--interval", type=int, default=150, help="seconds between tries")
    ap.add_argument("--max-hours", type=float, default=12.0)
    ap.add_argument("--vcn-name", default="verify-vcn")
    ap.add_argument("--ad", default="", help="substring of the availability domain")
    ap.add_argument("--compartment", default="", help="defaults to the tenancy root")
    ap.add_argument("--os-version", default="24.04")
    ap.add_argument("--ssh-key", default="", help="path to the .pub key")
    ap.add_argument("--check", action="store_true",
                    help="resolve everything and exit without launching")
    ap.add_argument("--setup", action="store_true",
                    help="generate an API keypair and write ~/.oci/config")
    ap.add_argument("--force", action="store_true", help="with --setup: overwrite")
    a = ap.parse_args()

    if a.setup:
        return setup_credentials(a.force)

    cfg, compute, network, identity = load_clients()
    # A freshly registered API key propagates unevenly — calls can succeed, then
    # 401, then succeed again for several minutes. Tolerate that for a bounded
    # window rather than aborting a 10-hour run on a transient rejection.
    auth_deadline = time.time() + 600
    while True:
        try:
            found = discover(cfg, compute, network, identity, a)
            break
        except oci.exceptions.ServiceError as exc:
            if exc.status == 401 and time.time() < auth_deadline:
                log("401 — key still propagating, retrying in 30s")
                time.sleep(30)
                continue
            if exc.status == 401:
                raise SystemExit(
                    f"\nOracle keeps rejecting the credentials (401).\n"
                    f"\nRegister the public key against your user:\n"
                    f"    https://cloud.oracle.com/identity/domains/my-profile/api-keys\n"
                    f"    Add API key -> Paste a public key -> {PUB_PEM}\n"
                    f"\nThe fingerprint there must equal {cfg.get('fingerprint')},\n"
                    f"and the user OCID in ~/.oci/config must be that same user.\n")
            raise SystemExit(f"\n{exc.status} {exc.code}: {exc.message}\n")
    log(f"region      : {cfg['region']}")
    log(f"availability: {found['ad']}")
    log(f"subnet      : {found['subnet_name']}")
    log(f"image       : {found['image_name']}")
    key = ssh_key(a.ssh_key or None)

    if a.check:
        log("config OK — everything resolved. Drop --check to start retrying.")
        return 0

    sizes = [(a.ocpus, a.memory)]
    if a.fallback and (a.ocpus, a.memory) != (1, 6):
        sizes.append((1, 6))

    deadline = time.time() + a.max_hours * 3600
    attempt = 0
    backoff = 0                                   # grows only when Oracle says 429
    log(f"retrying every {a.interval}s for up to {a.max_hours}h — Ctrl+C to stop")
    while time.time() < deadline:
        ocpus, memory = sizes[attempt % len(sizes)]
        attempt += 1
        try:
            inst = launch(compute, found, key, a.name, ocpus, memory, a.shape)
            log(f"SUCCESS on attempt {attempt}: {ocpus} OCPU / {memory} GB")
            log(f"instance: {inst.id}")
            ip = public_ip(cfg, compute, network, inst)
            print(f"\n  PUBLIC IP: {ip}\n")
            print(f"  ssh -i <your-private-key> ubuntu@{ip}")
            print("  git clone https://github.com/cLLeB/verification-system.git")
            print("  cd verification-system && ./deploy-oracle.sh <your-domain>\n")
            return 0
        except oci.exceptions.ServiceError as exc:
            if exc.status == 429:
                # Being told to slow down. Back off hard rather than keep the
                # cadence — hammering through a throttle is how polite retrying
                # turns into abuse.
                backoff = min(backoff * 2 if backoff else a.interval * 2, 1800)
                retry_after = int(getattr(exc, "headers", {}).get("retry-after", 0) or 0)
                wait = max(backoff, retry_after)
                log(f"attempt {attempt}: RATE LIMITED by Oracle — backing off {wait}s")
                time.sleep(wait)
                continue
            if is_capacity(exc) or exc.status in _TRANSIENT_STATUS:
                backoff = 0                       # capacity errors are not our fault
                log(f"attempt {attempt} ({ocpus}/{memory}): no capacity — waiting")
            else:
                # A real error (bad auth, bad OCID, quota). Looping hides it.
                raise SystemExit(f"\nstopping — this is not a capacity problem:\n"
                                 f"  status {exc.status} {exc.code}: {exc.message}")
        except KeyboardInterrupt:
            log("stopped by user")
            return 1
        time.sleep(a.interval)

    log(f"gave up after {a.max_hours}h. Ampere is heavily contested; try "
        f"--fallback, or use VM.Standard.E2.1.Micro with FACE_ACTIVE_LIVENESS=0.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

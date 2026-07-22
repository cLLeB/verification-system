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


def load_clients():
    try:
        cfg = oci.config.from_file()
        oci.config.validate_config(cfg)
    except oci.exceptions.ConfigFileNotFound:
        raise SystemExit(
            "\nNo Oracle API credentials yet. One-time setup:\n"
            "\n    oci setup config\n"
            "\nIt asks for your user OCID and tenancy OCID (console -> your avatar ->\n"
            "My profile / Tenancy), the region (eu-paris-1), and offers to generate a\n"
            "keypair - say yes. Then upload the public key it writes:\n"
            "\n    console -> avatar -> My profile -> API keys -> Add API key\n"
            "    -> Paste, using ~/.oci/oci_api_key_public.pem\n"
            "\nThen re-run with --check.\n")
    except oci.exceptions.InvalidConfig as exc:
        raise SystemExit(f"\n~/.oci/config is incomplete: {exc}\n"
                         f"Re-run `oci setup config` to rebuild it.\n")
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
    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=found["compartment"],
        availability_domain=found["ad"],
        display_name=name,
        shape=shape,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=float(ocpus), memory_in_gbs=float(memory)),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=found["image"]),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=found["subnet"], assign_public_ip=True),
        metadata={"ssh_authorized_keys": key},
    )
    return compute.launch_instance(details).data


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
    a = ap.parse_args()

    cfg, compute, network, identity = load_clients()
    found = discover(cfg, compute, network, identity, a)
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
            if is_capacity(exc) or exc.status in _TRANSIENT_STATUS:
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

"""Template-store maintenance CLI.

  python manage_templates.py wrap --path face_db [--modality face] [--dry-run]
      Re-write any pre-envelope (FT1/FT2/JSON) rows in envelope form. Reads
      already work without this; wrapping is for uniformity before Phase 1.
      Tenant stores live under <db_path>/tenants/<tenant>[/palm].

  python manage_templates.py protect --path face_db [--modality face] [--dry-run]
      Materialise the PROTECTED (cancelable) form for rows enrolled before
      template protection existed. Matching already projects such rows on the
      fly; this persists the protected copy so exports/sync are uniform.

  python manage_templates.py prune-adaptive --path face_db/palm --modality palm [--dry-run]
      Drop adaptive vectors that have drifted away from their own enrolment
      anchors. Repairs templates widened by the old unbounded adaptation, which
      made the busiest identity match everybody (and refuse their enrolments as
      "duplicate"). Anchors are never touched.

  python manage_templates.py erase-keys --path face_db/tenants/acme --yes
      CRYPTO-ERASE a store's key material. The encrypted templates left behind
      become permanently unreadable. Run only for offboarded tenants.
"""

from __future__ import annotations

import argparse
import sys

from biometric.core import crypto, envelope
from biometric.core.store import TemplateStore


def wrap_store(path: str, modality: str = "face", dry_run: bool = False,
               db_file: str = "faces.db") -> int:
    store = TemplateStore(path, db_file=db_file, modality=modality)
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT user_id, data FROM templates WHERE deleted=0").fetchall()
    wrapped = 0
    for user_id, blob in rows:
        raw = blob
        if store._cipher is not None:
            try:
                raw = store._cipher.decrypt(raw)
            except Exception:
                print(f"  SKIP {user_id}: cannot decrypt (wrong key?)")
                continue
        if envelope.is_envelope(raw):
            continue
        tmpl = store._deserialize(blob)
        if tmpl is None:
            print(f"  SKIP {user_id}: unreadable blob")
            continue
        if not dry_run:
            store._write(tmpl)
        wrapped += 1
    print(f"{'would wrap' if dry_run else 'wrapped'} {wrapped} of {len(rows)} templates")
    return wrapped


def protect_store(path: str, modality: str = "face", dry_run: bool = False,
                  db_file: str = "faces.db") -> int:
    """Bulk-materialise the protected column for pre-protection rows."""
    store = TemplateStore(path, db_file=db_file, modality=modality,
                          protect_templates=True)
    n = store.protect_fill(dry_run=dry_run,
                           progress=lambda done, total: print(f"  {done}/{total}...", flush=True))
    print(f"{'would protect' if dry_run else 'protected'} {n} template row(s) "
          f"(domain {store.protection_tag()})")
    return n


def prune_store(path: str, modality: str = "face", min_anchor_sim: float = 0.0,
                dry_run: bool = False, db_file: str = "faces.db") -> int:
    """Drop adaptive vectors that have drifted away from their own enrolment anchors.

    Repairs templates widened by the old unbounded adaptation (see
    ``biometric.core.store.add_adaptive``). Anchors are never touched, so the worst
    case is that a user reverts to exactly what they enrolled with — but note that a
    user who was only still matching *because* of drift will need re-enrolling.
    """
    if min_anchor_sim <= 0.0:
        from palm.config import load_config as _palm_cfg
        from face.config import load_config as _face_cfg
        cfg = _palm_cfg() if modality == "palm" else _face_cfg()
        min_anchor_sim = cfg.adaptive_min_anchor_sim
    store = TemplateStore(path, db_file=db_file, modality=modality,
                          adaptive_min_anchor_sim=min_anchor_sim)
    changed = store.prune_adaptive(dry_run=dry_run)
    if not changed:
        print(f"nothing to prune (floor {min_anchor_sim}) — no template has drifted")
        return 0
    total = 0
    for user_id, dropped, kept, worst in changed:
        total += dropped
        print(f"  {user_id}: {'would drop' if dry_run else 'dropped'} {dropped} "
              f"adaptive vector(s) (worst {worst:.4f} from its own anchors), kept {kept}")
    print(f"{'would prune' if dry_run else 'pruned'} {total} vector(s) "
          f"across {len(changed)} user(s) at floor {min_anchor_sim}")
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("wrap", help="envelope-wrap legacy template rows")
    w.add_argument("--path", required=True, help="store directory (holds the .db)")
    w.add_argument("--modality", default="face", choices=("face", "palm"))
    w.add_argument("--db-file", default="faces.db")
    w.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("protect", help="materialise protected rows for old enrolments")
    p.add_argument("--path", required=True, help="store directory (holds the .db)")
    p.add_argument("--modality", default="face", choices=("face", "palm"))
    p.add_argument("--db-file", default="faces.db")
    p.add_argument("--dry-run", action="store_true")

    pr = sub.add_parser("prune-adaptive",
                        help="drop adaptive vectors that drifted off their own anchors")
    pr.add_argument("--path", required=True, help="store directory (holds the .db)")
    pr.add_argument("--modality", default="face", choices=("face", "palm"))
    pr.add_argument("--db-file", default="faces.db")
    pr.add_argument("--min-anchor-sim", type=float, default=0.0,
                    help="floor (default: the modality's adaptive_min_anchor_sim)")
    pr.add_argument("--dry-run", action="store_true")

    e = sub.add_parser("erase-keys", help="crypto-erase a store's key material")
    e.add_argument("--path", required=True)
    e.add_argument("--yes", action="store_true",
                   help="confirm: data becomes permanently unreadable")

    args = ap.parse_args(argv)
    if getattr(args, "modality", None) == "palm" and args.db_file == "faces.db":
        args.db_file = "palms.db"            # palm stores keep their own file name
    if args.cmd == "wrap":
        wrap_store(args.path, modality=args.modality, dry_run=args.dry_run,
                   db_file=args.db_file)
        return 0
    if args.cmd == "protect":
        protect_store(args.path, modality=args.modality, dry_run=args.dry_run,
                      db_file=args.db_file)
        return 0
    if args.cmd == "prune-adaptive":
        prune_store(args.path, modality=args.modality,
                    min_anchor_sim=args.min_anchor_sim, dry_run=args.dry_run,
                    db_file=args.db_file)
        return 0
    if args.cmd == "erase-keys":
        if not args.yes:
            print("Refusing without --yes (this permanently destroys access to the data).")
            return 2
        removed = crypto.erase_keys(args.path)
        print(f"removed {removed} key file(s); remaining blobs are unrecoverable")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

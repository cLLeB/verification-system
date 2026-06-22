"""Bulk-enrol a partner's image dataset, efficiently, into a tenant.

Expected layout — one sub-folder per person, images inside:

    dataset/
        alice/   img1.jpg  img2.jpg ...
        bob/     photo.png ...

Each image is run through the face engine once to get its embedding; embeddings
are written to the encrypted store in bulk (one transaction per batch), and the
search index is built once at the end — far faster than one-by-one API enrolment.

    python bulk_enroll.py dataset/                       # into the default store
    python bulk_enroll.py dataset/ --tenant acme         # into tenant 'acme'
    python bulk_enroll.py dataset/ --samples 5           # keep up to 5 per person

Run it while the service is stopped, or restart the service afterwards so it picks
up the new index (a restart only replays the change tail — seconds).
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import time

import cv2

from face import engine as _engine
from face import index as faceindex
from face.config import load_config
from face.errors import FaceError
from face.storage import FaceStore

_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _images(person_dir: str):
    for name in sorted(os.listdir(person_dir)):
        if os.path.splitext(name)[1].lower() in _EXTS:
            yield os.path.join(person_dir, name)


def main() -> None:
    p = argparse.ArgumentParser(description="Bulk-enrol an image dataset")
    p.add_argument("folder", help="dataset root (one sub-folder per person)")
    p.add_argument("--tenant", default=None, help="tenant id (default: the base store)")
    p.add_argument("--samples", type=int, default=None, help="max embeddings kept per person")
    p.add_argument("--batch", type=int, default=2000, help="users per DB transaction")
    args = p.parse_args()

    cfg = load_config()
    if args.samples:
        cfg = dataclasses.replace(cfg, samples_per_user=args.samples)
    if args.tenant:
        cfg = dataclasses.replace(cfg, db_path=os.path.join(cfg.db_path, "tenants", args.tenant))

    print(f"warming engine…", flush=True)
    _engine.warm(cfg)
    store = FaceStore(cfg)

    people = [d for d in sorted(os.listdir(args.folder))
              if os.path.isdir(os.path.join(args.folder, d))]
    print(f"found {len(people):,} people under {args.folder}\n", flush=True)

    t0 = time.perf_counter()
    enrolled = imgs_ok = imgs_fail = 0
    batch = []

    def flush():
        nonlocal batch
        if batch:
            store.add_many(batch)
            batch = []

    for i, person in enumerate(people, 1):
        embs = []
        for path in _images(os.path.join(args.folder, person)):
            img = cv2.imread(path)
            if img is None:
                imgs_fail += 1
                continue
            try:
                embs.append(_engine.detect(img, cfg).embedding)
                imgs_ok += 1
            except FaceError:
                imgs_fail += 1                       # no/again unusable face in this image
        if embs:
            batch.append((person, embs))
            enrolled += 1
        if len(batch) >= args.batch:
            flush()
        if i % 200 == 0:
            print(f"  {i:,}/{len(people):,} people  ({imgs_ok:,} imgs ok, {imgs_fail:,} skipped)",
                  flush=True)
    flush()

    dt = time.perf_counter() - t0
    print(f"\nstored {enrolled:,} people / {imgs_ok:,} images in {dt:,.1f}s "
          f"({imgs_fail:,} images skipped)", flush=True)

    print("building search index…", flush=True)
    faceindex.invalidate(cfg.db_path)
    t = time.perf_counter()
    idx = faceindex.get_index(cfg.db_path, store)
    users, vectors = idx.count()
    print(f"index ready: {users:,} identities, {vectors:,} vectors "
          f"({time.perf_counter()-t:,.1f}s, backend={idx.backend})", flush=True)


if __name__ == "__main__":
    main()

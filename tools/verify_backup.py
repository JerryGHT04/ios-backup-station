#!/usr/bin/env python3
"""Check that every file of an iOS backup can be decrypted, without writing any of them out.

    BACKUP_PASSWORD=... python3 verify_backup.py /path/to/backups/<UDID>
    python3 verify_backup.py /path/to/backups/<UDID> --limit 2000        # only the first files, a quick trial

It opens the keybag with the backup password, decrypts Manifest.db, then decrypts every file stream-wise and checks
that the padding is valid and that the size matches the manifest. Nothing is written except a temporary copy of
Manifest.db (a few hundred MB at most) that is removed at the end. Needs the "cryptography" package.
A size that differs from the manifest is only a warning: databases that the device keeps changing while the backup runs
often end up with another size than the one listed. Damaged data (a failed decryption, bad padding), missing files and
unreadable records are problems.
Exit code 0 = no problems (warnings are allowed), 1 = problems found, 2 = could not start (wrong password, missing files).
"""
import argparse, collections, concurrent.futures, getpass, hashlib, os, plistlib, sqlite3, struct, sys, tempfile, time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap

CHUNK = 4 * 1024 * 1024


def parse_keybag(data):
    attrs, classes, cur = {}, {}, None
    i = 0
    while i + 8 <= len(data):
        tag = data[i:i + 4].decode("ascii", "replace")
        n = struct.unpack(">L", data[i + 4:i + 8])[0]
        val = data[i + 8:i + 8 + n]
        i += 8 + n
        if tag == "UUID":
            if "UUID" not in attrs:
                attrs["UUID"] = val
            else:
                cur = {"UUID": val}
                continue
        elif cur is not None:
            cur[tag] = val
            if tag == "WPKY" and "CLAS" in cur:
                classes[struct.unpack(">L", cur["CLAS"])[0]] = cur
            continue
        else:
            attrs[tag] = val
    return attrs, classes


def unlock(keybag, password):
    attrs, classes = parse_keybag(keybag)
    key = password.encode()
    if "DPSL" in attrs and "DPIC" in attrs:
        key = hashlib.pbkdf2_hmac("sha256", key, attrs["DPSL"], struct.unpack(">L", attrs["DPIC"])[0], 32)
    key = hashlib.pbkdf2_hmac("sha1", key, attrs["SALT"], struct.unpack(">L", attrs["ITER"])[0], 32)
    keys = {}
    for cls, c in classes.items():
        if struct.unpack(">L", c.get("WRAP", b"\0\0\0\0"))[0] & 2:
            try:
                keys[cls] = aes_key_unwrap(key, c["WPKY"])
            except InvalidUnwrap:
                raise SystemExit("The backup password is not correct.")
    if not keys:
        raise SystemExit("The backup password is not correct (no class key could be opened).")
    return keys


def decryptor(key):
    return Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).decryptor()


def decrypt_bytes(key, data):
    d = decryptor(key)
    out = d.update(data) + d.finalize()
    pad = out[-1]
    return out[:-pad] if 1 <= pad <= 16 else out


def archived(blob):
    """The MBFile record in Manifest.db is an NSKeyedArchive: follow the UIDs to the fields that matter."""
    p = plistlib.loads(blob)
    objs = p["$objects"]
    root = objs[p["$top"]["root"].data]
    ek = root.get("EncryptionKey")
    ek = objs[ek.data]["NS.data"] if ek is not None and hasattr(ek, "data") else None
    return root.get("Size"), ek


def verify_file(path, key, size):
    """Returns None when the file is fine, otherwise a short reason."""
    try:
        total, last = 0, b""
        d = decryptor(key)
        with open(path, "rb") as f:
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                out = d.update(buf)
                total += len(out)
                if out:
                    last = out[-16:]
        tail = d.finalize()
        total += len(tail)
        last = (last + tail)[-16:]
    except ValueError as e:                    # not a multiple of the block size, and so on
        return "decrypt error: %s" % e
    except OSError as e:
        return "cannot read: %s" % e.strerror
    if total == 0:
        return "empty although the manifest lists data" if size else None
    pad = last[-1]
    if not 1 <= pad <= 16 or last[-pad:] != bytes([pad]) * pad:
        return "bad padding (wrong key or damaged data)"
    if size is not None and total - pad != size:
        return "warn: size %d, the manifest says %d" % (total - pad, size)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="the backup folder of one device (named after its UDID)")
    ap.add_argument("--limit", type=int, default=0, help="only check this many files (a trial run)")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    folder = a.folder.rstrip("/")
    try:
        with open(os.path.join(folder, "Manifest.plist"), "rb") as f:
            man = plistlib.load(f)
    except OSError as e:
        raise SystemExit("Cannot read Manifest.plist: %s" % e)
    encrypted = bool(man.get("IsEncrypted"))
    keys, tmpdb = {}, None
    t0 = time.time()
    try:
        if encrypted:
            pw = os.environ.get("BACKUP_PASSWORD") or getpass.getpass("Backup password: ")
            keys = unlock(man["BackupKeyBag"], pw)
            mk = man["ManifestKey"]
            cls = struct.unpack("<L", mk[:4])[0]
            if cls not in keys:
                raise SystemExit("No key for the manifest (class %d)." % cls)
            mkey = aes_key_unwrap(keys[cls], mk[4:])
            tmp = tempfile.NamedTemporaryFile(prefix="verify-manifest-", suffix=".db", delete=False)
            tmpdb = tmp.name
            d = decryptor(mkey)
            with open(os.path.join(folder, "Manifest.db"), "rb") as f:
                while True:
                    buf = f.read(CHUNK)
                    if not buf:
                        break
                    tmp.write(d.update(buf))
            tmp.write(d.finalize()); tmp.close()
            dbpath = tmpdb
        else:
            dbpath = os.path.join(folder, "Manifest.db")
        db = sqlite3.connect(dbpath)
        rows = db.execute("SELECT fileID, relativePath, file FROM Files WHERE flags = 1").fetchall()
        db.close()
        print("Manifest opened: %d files to check (%s), %.0f s" % (len(rows), "encrypted" if encrypted else "not encrypted", time.time() - t0), flush=True)
        if a.limit:
            rows = rows[:a.limit]
        jobs = []
        problems = collections.Counter()
        warnings = collections.Counter()
        warn_samples = []
        samples = []
        missing_ok = 0
        for fid, rel, blob in rows:
            try:
                size, ek = archived(blob)
            except Exception as e:
                problems["unreadable manifest record"] += 1
                if len(samples) < 20: samples.append((fid, rel, "unreadable manifest record: %r" % e))
                continue
            path = os.path.join(folder, fid[:2], fid)
            if not os.path.exists(path):
                if size == 0 or size is None:
                    missing_ok += 1                       # an empty file has no data file
                    continue
                problems["missing file"] += 1
                if len(samples) < 20: samples.append((fid, rel, "the file is missing"))
                continue
            if not encrypted:
                real = os.path.getsize(path)
                if size is not None and real != size:
                    warnings["size differs from the manifest"] += 1
                    if len(warn_samples) < 8: warn_samples.append((fid, rel, "size %d, the manifest says %d" % (real, size)))
                continue
            if ek is None:
                problems["no file key"] += 1
                if len(samples) < 20: samples.append((fid, rel, "the manifest has no key for this file"))
                continue
            cls = struct.unpack("<L", ek[:4])[0]
            if cls not in keys:
                problems["class key missing"] += 1
                if len(samples) < 20: samples.append((fid, rel, "no key for protection class %d" % cls))
                continue
            try:
                fkey = aes_key_unwrap(keys[cls], ek[4:])
            except InvalidUnwrap:
                problems["file key cannot be opened"] += 1
                if len(samples) < 20: samples.append((fid, rel, "the file key cannot be opened"))
                continue
            jobs.append((path, fkey, size, fid, rel))
        done = nbytes = 0
        last = time.time()
        total_bytes = sum((j[2] or 0) for j in jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(verify_file, j[0], j[1], j[2]): j for j in jobs}
            for fu in concurrent.futures.as_completed(futs):
                j = futs[fu]
                r = fu.result()
                done += 1; nbytes += j[2] or 0
                if r and r.startswith("warn: "):
                    warnings["size differs from the manifest"] += 1
                    if len(warn_samples) < 8: warn_samples.append((j[3], j[4], r[6:]))
                elif r:
                    problems[r.split(":")[0].split(" ")[0] + "…"] += 1
                    if len(samples) < 20: samples.append((j[3], j[4], r))
                if time.time() - last > 30:
                    last = time.time()
                    rate = nbytes / (last - t0)
                    eta = (total_bytes - nbytes) / rate if rate else 0
                    print("  %d / %d files, %.1f / %.1f GB, %.0f MB/s, about %.0f min left, %d problems" % (
                        done, len(jobs), nbytes / 1e9, total_bytes / 1e9, rate / 1e6, eta / 60, sum(problems.values())), flush=True)
        print("\nChecked %d files (%.1f GB), %d empty files have no data file, %.0f min." % (done, nbytes / 1e9, missing_ok, (time.time() - t0) / 60))
        if warnings:
            print("Warnings: %d  (%s)" % (sum(warnings.values()), ", ".join("%s: %d" % kv for kv in warnings.items())))
            for fid, rel, why in warn_samples:
                print("   ~ %s  %s  (%s)" % (fid[:10], rel[:90], why))
        if problems:
            print("PROBLEMS: %d" % sum(problems.values()))
            for k, v in problems.most_common():
                print("  %s: %d" % (k, v))
            for fid, rel, why in samples:
                print("   - %s  %s  (%s)" % (fid, rel, why))
            return 1
        print("OK: every file decrypts correctly%s." % (" (some sizes differ from the manifest, see the warnings)" if warnings else " and has the size the manifest lists"))
        return 0
    finally:
        if tmpdb and os.path.exists(tmpdb):
            os.remove(tmpdb)


if __name__ == "__main__":
    sys.exit(main())

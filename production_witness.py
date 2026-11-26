"""Separate offline witness; one exclusive durable claim per store/checkpoint.

Operator provisions private directory outside database restore boundary. No
automatic deletion, replacement, recovery or advancement. Trusted filesystem
fixture, not a remote immutable service or protection from its administrator.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from production_pg import Denied
from production_recovery import Anchor,valid

@dataclass(frozen=True)
class Witness:
    directory:Path
    device:int
    inode:int

    @classmethod
    def pin(cls,directory):
        if not isinstance(directory,Path) or not directory.is_absolute() or directory.resolve(strict=True)!=directory:
            raise Denied("witness_path")
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
            raise Denied("witness_permissions")
        return cls(directory,info.st_dev,info.st_ino)

    def claim(self,anchor):
        if type(anchor) is not Anchor or not valid(anchor.store,32) or not valid(anchor.checkpoint,64) or not valid(anchor.candidate,40):
            raise Denied("witness_anchor")
        fd=out=None
        try:
            fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)!=(self.device,self.inode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
                raise Denied("witness_changed")
            # Deliberately excludes candidate: changing versions cannot reuse a checkpoint.
            name=hashlib.sha256((anchor.store+":"+anchor.checkpoint).encode()).hexdigest()+".spent"
            out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            payload=json.dumps({"store":anchor.store,"checkpoint":anchor.checkpoint,
                                "candidate":anchor.candidate,"state":"SPENT"},sort_keys=True).encode()
            if os.write(out,payload)!=len(payload):raise Denied("witness_short_write")
            os.fsync(out)
            os.fsync(fd)
        except OSError as exc:
            raise Denied("witness_unavailable_or_spent") from exc
        finally:
            if out is not None:os.close(out)
            if fd is not None:os.close(fd)

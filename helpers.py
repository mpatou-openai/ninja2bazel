import os
from typing import List


def resolvePath(path: str) -> str:
    cur = 0
    split = path.split(os.path.sep)
    dest: List[str] = []
    for i, p in enumerate(split):
        if len(p) == 0 and i > 0:
            # // case
            continue
        if p == ".":
            continue
        if p == "..":
            if cur > 0:
                cur -= 1
                dest.remove(dest[-1])
        else:
            dest.append(p)
            cur += 1

    return os.path.sep.join(dest)

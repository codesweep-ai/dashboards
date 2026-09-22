"""Parse and compare the version strings dependencies are pinned with.

One comparison serves every ecosystem: semver and its looser relatives (`v7`,
`1.27`, `25.0.4.1+1`), Go pseudo-versions, the date-stamped dev builds this org
publishes, and Fedora NVRs. Anything that does not parse compares as unknown
rather than as older, so a pin nobody can read is never reported as behind.
"""

import re
from datetime import datetime, timezone

_NUM = re.compile(r"^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")

# vX.0.0-yyyymmddhhmmss-abcdef123456, vX.Y.Z-pre.0.yyyymmddhhmmss-…, and
# vX.Y.(Z+1)-0.yyyymmddhhmmss-…: the three forms `go help modules` defines.
_GO_PSEUDO = re.compile(r"^v\d+\.\d+\.\d+-(?:[0-9A-Za-z.-]*\.)?(\d{14})-([0-9a-f]{12})(?:\+incompatible)?$")

# 0.3.1-dev.20260909170256.1638d27: what @codesweep-ai/ui publishes between releases.
_DEV_BUILD = re.compile(r"-dev\.(\d{14})\.([0-9a-f]{7,40})$")

# The prerelease labels that mean "not a release", in tags that carry no semver
# hyphen: rust-v0.153.0-alpha.2, jdk-26+35-ea-beta, 2.0.0rc1.
_PRERELEASE_WORD = re.compile(r"(?i)(alpha|beta|rc\d*|pre|preview|nightly|snapshot|canary|dev|ea)\b")


class Version:
    """A version reduced to numbers plus an optional prerelease."""

    __slots__ = ("raw", "nums", "pre", "stamp", "commit")

    def __init__(self, raw, nums, pre=(), stamp=None, commit=None):
        self.raw = raw
        self.nums = nums
        self.pre = pre
        self.stamp = stamp
        self.commit = commit

    @property
    def major(self):
        return self.nums[0] if self.nums else 0

    @property
    def minor(self):
        return self.nums[1] if len(self.nums) > 1 else 0

    @property
    def patch(self):
        return self.nums[2] if len(self.nums) > 2 else 0

    @property
    def is_prerelease(self):
        return bool(self.pre)

    def key(self):
        # A release outranks its prereleases, so the prerelease part sorts as
        # "less" when present. Identifiers compare numerically when they can.
        nums = self.nums + (0,) * (6 - len(self.nums))
        if not self.pre:
            pre = ((2, 0, ""),)
        else:
            pre = tuple((1, int(p), "") if p.isdigit() else (1, -1, p) for p in self.pre)
        return (nums, pre)

    def __lt__(self, other):
        return self.key() < other.key()

    def __eq__(self, other):
        return isinstance(other, Version) and self.key() == other.key()

    def __hash__(self):
        return hash(self.key())

    def __repr__(self):
        return f"Version({self.raw!r})"


def parse(raw):
    """Return a Version, or None when the string is not a version at all."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = _GO_PSEUDO.match(s)
    if m:
        base = _NUM.match(s)
        nums = tuple(int(n) for n in base.group(1).split("."))
        # The stamp rides in the prerelease, so two pseudo-versions of one base
        # order by when their commits were made.
        return Version(s, nums, ("pseudo", m.group(1)), stamp=_stamp(m.group(1)), commit=m.group(2))
    m = _NUM.match(s)
    if not m:
        return None
    nums = tuple(int(n) for n in m.group(1).split("."))
    pre = tuple(m.group(2).split(".")) if m.group(2) else ()
    v = Version(s, nums, pre)
    d = _DEV_BUILD.search(s)
    if d:
        v.stamp, v.commit = _stamp(d.group(1)), d.group(2)
    return v


def _stamp(digits):
    try:
        return datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pseudo(raw):
    """The (timestamp, commit) a pseudo-version or dev build names, or None.

    Accepts the Go form, the npm form this org publishes (`0.0.0-<stamp>-<sha>`
    and `-dev.<stamp>.<sha>`), and the image tags built the same way.
    """
    v = parse(raw)
    if v and v.commit:
        return v.stamp, v.commit
    m = re.search(r"(?:^|[-.])(\d{14})[-.]([0-9a-f]{7,40})(?:$|-)", str(raw or ""))
    if m:
        return _stamp(m.group(1)), m.group(2)
    return None


def looks_prerelease(tag):
    """True when a tag reads as a prerelease even though semver cannot say so."""
    v = parse(strip_prefix(tag))
    if v is not None and v.pre:
        return True
    return bool(_PRERELEASE_WORD.search(tag or ""))


def strip_prefix(tag, prefix=""):
    """Reduce a tag to its version: `rust-v0.152.1` with prefix `rust-v` is `0.152.1`."""
    s = tag or ""
    if prefix and s.startswith(prefix):
        s = s[len(prefix):]
    elif not prefix:
        m = re.search(r"\d", s)
        s = s[m.start():] if m else s
    return s


def distance(pinned, latest):
    """How far `pinned` trails `latest`: "major", "minor", "patch", or None.

    The components are compared as numbers, so a 0.x minor bump is a minor one
    even though semver lets it break: golang.org/x moves its minor every month.
    A pin with fewer components than the latest (`v7`, `24`) floats inside what
    it names, so only the components it states are compared.
    """
    p, l = parse(pinned), parse(latest)
    if p is None or l is None or not (p < l):
        return None
    width = len(p.nums)
    if l.major != p.major:
        return "major"
    if width < 2:
        return None
    if l.minor != p.minor:
        return "minor"
    if width < 3:
        return None
    return "patch"


def newest(candidates, stable=True):
    """The highest version among `candidates` (strings), ignoring prereleases."""
    best = None
    for c in candidates:
        v = parse(c)
        if v is None or (stable and v.is_prerelease):
            continue
        if best is None or best < v:
            best = v
    return best.raw if best else None


def newest_in_line(candidates, pinned):
    """The highest stable version sharing `pinned`'s major.minor, if any."""
    p = parse(pinned)
    if p is None or len(p.nums) < 2:
        return None
    same = []
    for c in candidates:
        v = parse(c)
        if v is not None and v.nums[:2] == p.nums[:2]:
            same.append(c)
    return newest(same)


def nvr(raw):
    """Split a Fedora version-release (`6.19.10-300.fc44`) into (Version, release)."""
    m = re.match(r"^(?:\d+:)?([0-9][0-9A-Za-z.^~]*)-([0-9A-Za-z._]+)$", raw or "")
    if not m:
        return None, None
    return parse(m.group(1).replace("^", "+").replace("~", "-")), m.group(2)


def cycle_of(raw, width=2):
    """The release cycle a version belongs to: `1.27.0` is `1.27`, `24.19.0` with width 1 is `24`."""
    v = parse(raw)
    if v is None:
        return None
    return ".".join(str(n) for n in v.nums[:width])


# --- npm ranges ------------------------------------------------------------------

_PARTIAL = re.compile(r"^v?(\*|x|X|\d+)(?:\.(\*|x|X|\d+)(?:\.(\*|x|X|\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?)?)?$")


def _bounds(op, text):
    """The comparators one npm range term means, as (operator, Version) pairs, or None when unreadable."""
    m = _PARTIAL.match(text)
    if not m:
        return None
    nums = [None if p is None or p in "*xX" else int(p) for p in m.group(1, 2, 3)]
    pre = m.group(4)
    known = next((i for i, n in enumerate(nums) if n is None), 3)

    def ver(parts, prerelease=None):
        parts = list(parts) + [0] * (3 - len(parts))
        return parse(".".join(str(p) for p in parts) + (f"-{prerelease}" if prerelease else ""))

    def up(i):
        # The first version past everything the first `i` numbers name.
        return ver(nums[:i - 1] + [nums[i - 1] + 1], "0")

    if known == 0:
        return [] if op in ("", "=", ">=", "<=", "~", "^") else [("<", ver([0, 0, 0], "0"))]
    exact = ver(nums[:known], pre)
    if op in ("", "="):
        return [("=", exact)] if known == 3 else [(">=", exact), ("<", up(known))]
    if op == "~":
        return [(">=", exact), ("<", up(min(known, 2)))]
    if op == "^":
        lead = next((i for i, n in enumerate(nums[:known]) if n != 0), known - 1)
        return [(">=", exact), ("<", up(lead + 1 if lead < known else known))]
    if op == ">":
        return [(">", exact)] if known == 3 else [(">=", up(known))]
    if op == "<=":
        return [("<=", exact)] if known == 3 else [("<", up(known))]
    return [(op, exact)]


def satisfies(raw, spec):
    """Whether npm would accept version `raw` for range `spec`: True, False, or None when either is unreadable.

    Reads what package.json and a registry's dependency lists write: `^1.2.3`,
    `~1.2`, `1.x`, `>=1 <2`, `1.2.3 - 2`, and alternatives joined by `||`. A
    prerelease matches only a term naming a prerelease of the same release.
    """
    v = parse(raw)
    if v is None or spec is None:
        return None
    spec = re.sub(r"(<=|>=|<|>|=|~|\^)\s+", r"\1", spec.strip())
    for alt in spec.split("||"):
        alt = alt.strip()
        m = re.fullmatch(r"(\S+)\s+-\s+(\S+)", alt)
        terms = [(">=", m.group(1)), ("<=", m.group(2))] if m else [
            re.match(r"^(<=|>=|<|>|=|~|\^)?(.*)$", t).groups("") for t in alt.split()]
        comps = []
        for op, text in terms:
            got = _bounds(op, text or "*")
            if got is None:
                return None
            comps += got
        named = (parse(text) for _, text in terms)
        if v.is_prerelease and not any(n and n.pre and n.nums[:3] == v.nums[:3] for n in named):
            continue
        if all({"=": v == c, ">": c < v, ">=": not v < c, "<": v < c, "<=": not c < v}[op] for op, c in comps):
            return True
    return False

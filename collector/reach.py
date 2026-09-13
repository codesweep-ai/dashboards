"""Reachability for Go advisories: whether a project's code calls what an advisory names.

An advisory matched by module and version says a vulnerable version is in the
module graph. govulncheck, the Go team's scanner, reads the project's packages
and says whether the vulnerable function is called, its package imported, or
neither. It needs the Go toolchain and the project's full source, and no key.
"""

import json
import os
import shutil
import subprocess

REACH_ORDER = ("not-in-build", "required", "imported", "called")


class Unavailable(Exception):
    pass


def build_tool(dest_dir):
    """Build the govulncheck this repository pins in go.mod, or raise Unavailable."""
    if not shutil.which("go"):
        raise Unavailable("no Go toolchain on PATH")
    out = os.path.join(dest_dir, "govulncheck")
    proc = subprocess.run(["go", "build", "-o", out, "golang.org/x/vuln/cmd/govulncheck"],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise Unavailable("could not build govulncheck: " + (proc.stderr.strip().splitlines() or ["failed"])[-1])
    return out


def scan(tool, path, timeout=900):
    """Run govulncheck over every package in `path`: ({go id: level}, {go id: aliases}).

    The level is how close the project's code comes to the vulnerable symbol:
    called, imported, or only required. An id absent from the result is not in
    what the packages build at all.
    """
    env = dict(os.environ, GOTOOLCHAIN="auto", GOFLAGS="-mod=mod")
    proc = subprocess.run([tool, "-format", "json", "./..."], cwd=path, capture_output=True, text=True,
                          timeout=timeout, env=env)
    if proc.returncode not in (0, 3):
        lines = proc.stderr.strip().splitlines()
        raise Unavailable(lines[-1] if lines else f"govulncheck exited {proc.returncode}")
    levels, aliases, built = {}, {}, set()
    decoder, text, i = json.JSONDecoder(), proc.stdout, 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        obj, i = decoder.raw_decode(text, i)
        if "SBOM" in obj:
            built = {m.get("path") for m in obj["SBOM"].get("modules") or [] if m.get("path")}
        elif "osv" in obj:
            aliases[obj["osv"]["id"]] = set(obj["osv"].get("aliases") or [])
        elif "finding" in obj:
            f = obj["finding"]
            frame = (f.get("trace") or [{}])[0]
            level = "called" if frame.get("function") else "imported" if frame.get("package") else "required"
            prev = levels.get(f["osv"])
            if prev is None or REACH_ORDER.index(level) > REACH_ORDER.index(prev):
                levels[f["osv"]] = level
    return levels, aliases, built


def annotate(project, levels, aliases, built, no_code=False):
    """Mark each Go advisory with how the project's code reaches it, and each module with whether it ships."""
    by_alias = {}
    for gid, al in aliases.items():
        for a in al | {gid}:
            by_alias.setdefault(a, set()).add(gid)
    for d in project["dependencies"]:
        if d["ecosystem"] == "go" and (built or no_code):
            # A module the project's packages do not build into is tooling:
            # a `tool` pin, or something only a second modfile requires.
            d["in_build"] = d["name"] in built
        if not (d["ecosystem"] == "go" or (d["ecosystem"] == "runtime" and d["name"] == "go")):
            continue
        for v in d.get("vulnerabilities") or []:
            ids = {v["id"], *v.get("aliases", [])}
            gids = set().union(*(by_alias.get(x, set()) for x in ids)) | {x for x in ids if x.startswith("GO-")}
            found = [levels[g] for g in gids if g in levels]
            v["reachable"] = max(found, key=REACH_ORDER.index) if found else "not-in-build"
        vulns = d.get("vulnerabilities") or []
        if vulns:
            d["reachability"] = max((v["reachable"] for v in vulns), key=REACH_ORDER.index)

"""Package URLs for records, and the file as a CycloneDX SBOM with VEX.

A purl names a package the same way in every tool that reads one, so a record
with a purl can be matched against another scanner's findings. The SBOM lists
every project's dependencies as components, and every advisory with what the
collector knows about it: whether the code calls it, and whether someone
accepted it and why.
"""

import json
import urllib.parse

CYCLONEDX_SPEC = "1.6"

# Dependabot's dismissal reasons, as CycloneDX analysis states and responses.
_ACCEPTED = {
    "fix_started": ("exploitable", None, ["update"]),
    "inaccurate": ("false_positive", None, None),
    "no_bandwidth": ("in_triage", None, None),
    "not_used": ("not_affected", "code_not_reachable", None),
    "tolerable_risk": ("exploitable", None, ["will_not_fix"]),
}
# govulncheck's reachability, as the same.
_REACHABLE = {
    "not-in-build": ("not_affected", "code_not_present"),
    "required": ("not_affected", "code_not_reachable"),
    "imported": ("not_affected", "code_not_reachable"),
    "called": ("exploitable", None),
}
_SEVERITY = {"moderate": "medium", "unrated": "unknown"}


def _seg(s):
    return urllib.parse.quote(s, safe="")


def purl(d):
    """The package URL that names a record's pinned release, or None where no purl type fits."""
    eco, name, version = d["ecosystem"], d.get("package") or d["name"], d.get("version")
    if eco == "package" and d.get("datasource") == "rpm":
        effective = (d.get("upstream") or {}).get("effective")
        if not effective:
            return None
        qualifiers = f"?distro=fedora-{d['release']}" if d.get("release") else ""
        return f"pkg:rpm/fedora/{_seg(d['name'])}@{_seg(effective)}{qualifiers}"
    if not version or d.get("floating") and not d.get("lag"):
        return None
    at = "@" + _seg(str(version))
    if eco == "go":
        return "pkg:golang/" + "/".join(_seg(p) for p in d["name"].split("/")) + at
    if d.get("datasource") == "npm" and eco in ("npm", "native"):
        return "pkg:npm/" + "/".join(_seg(p) for p in name.lower().split("/")) + at
    if eco == "pypi":
        return f"pkg:pypi/{_seg(name.lower().replace('_', '-'))}{at}"
    if eco == "actions" or (eco == "native" and d.get("datasource") == "github" and d.get("package")):
        parts = (d.get("package") if eco == "native" else name).split("/")
        if len(parts) < 2:
            return None
        sub = "#" + "/".join(parts[2:]) if len(parts) > 2 else ""
        return f"pkg:github/{_seg(parts[0].lower())}/{_seg(parts[1].lower())}{at}{sub}"
    if eco == "image" and d.get("datasource") == "oci" and "/" in name:
        registry, _, path = name.partition("/")
        if "." not in registry and ":" not in registry:
            registry, path = "docker.io", name
        qualifiers = "" if registry == "docker.io" else "?repository_url=" + _seg(registry)
        return "pkg:docker/" + "/".join(_seg(p) for p in path.split("/")) + at + qualifiers
    return None


def cyclonedx(data, site=None):
    """The dependencies file as one CycloneDX document: a component per project, its dependencies inside."""
    components, vulnerabilities = [], {}
    for p in data["projects"]:
        if p.get("error"):
            continue
        children = []
        for d in p["dependencies"]:
            ref = f"{p['name']}:{d['ecosystem']}:{d['name']}@{d.get('version') or ''}:{len(children)}"
            child = {"type": "library", "bom-ref": ref, "name": d.get("label") or d["name"]}
            if d.get("version"):
                child["version"] = str(d["version"])
            if d.get("purl"):
                child["purl"] = d["purl"]
            if (d.get("license") or {}).get("expression"):
                child["licenses"] = [{"expression": d["license"]["expression"]}]
            child["scope"] = "excluded" if d["scope"] in ("dev", "ci", "tool", "toolchain") or d.get("dev") else \
                "optional" if d["scope"] == "optional" else "required"
            children.append(child)
            for v in d.get("vulnerabilities") or []:
                # An analysis in CycloneDX covers every component an entry
                # affects, so records the collector judged differently get
                # entries of their own under the same advisory id.
                analysis = _analysis(d, v)
                key = (v["id"], json.dumps(analysis, sort_keys=True))
                entry = vulnerabilities.setdefault(key, _clean({
                    "bom-ref": f"{v['id']}:{len(vulnerabilities)}",
                    "id": v["id"],
                    "source": {"name": "Bodhi" if v["id"].startswith("FEDORA-") else "OSV", "url": v.get("url")},
                    "ratings": [{"severity": _SEVERITY.get(v["severity"], v["severity"])}],
                    "description": v.get("summary") or None,
                    "published": v.get("published"),
                    "references": [{"id": a, "source": {"name": "alias"}} for a in v.get("aliases") or []] or None,
                    "analysis": analysis,
                    "affects": [],
                }))
                entry["affects"].append({"ref": ref})
        components.append({"type": "application", "bom-ref": f"project:{p['name']}", "name": p["name"],
                           "version": (p.get("repo") or {}).get("sha"),
                           "externalReferences": [{"type": "vcs", "url": (p.get("repo") or {}).get("url")}],
                           "components": children})
    doc = {
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC,
        "version": 1,
        "metadata": {
            "timestamp": data["generated"],
            "tools": {"components": [{"type": "application", "name": "codesweep-ai dashboards collector"}]},
            "component": {"type": "application", "bom-ref": "org", "name": data["org"],
                          **({"externalReferences": [{"type": "website", "url": site}]} if site else {})},
        },
        "components": components,
        "vulnerabilities": list(vulnerabilities.values()),
    }
    return doc


def _analysis(d, v):
    accepted = d.get("accepted")
    if accepted and not accepted.get("lapsed") and accepted.get("finding") in (None, v["id"], *v.get("aliases", []), "vulnerable"):
        state, justification, response = _ACCEPTED.get(accepted["reason"], ("in_triage", None, None))
        return _clean({"state": state, "justification": justification, "response": response,
                       "detail": accepted.get("note")})
    if v.get("reachable") in _REACHABLE:
        state, justification = _REACHABLE[v["reachable"]]
        return _clean({"state": state, "justification": justification, "detail": f"govulncheck: {v['reachable']}"})
    return None


def _clean(obj):
    return {k: v for k, v in obj.items() if v is not None}

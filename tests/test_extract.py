import json
import unittest

from collector import extract

ORG = "codesweep-ai"


def by(deps, name):
    return [d for d in deps if d["name"] == name]


class GoMod(unittest.TestCase):
    TEXT = """module github.com/codesweep-ai/lint

go 1.27.0

require (
\tgithub.com/spf13/cobra v1.10.2
\tgithub.com/codesweep-ai/ledger v0.0.0-20260909183215-bda511aea589 // indirect
\tgolang.org/x/mod v0.39.0 // indirect
)

require golang.org/x/sys v0.47.0

tool (
\tgithub.com/codesweep-ai/ledger/cmd/cs-ledger
)
"""

    def test_modules_scopes_and_toolchain(self):
        deps = extract.go_mod(self.TEXT, "go.mod", ORG)
        self.assertEqual(by(deps, "github.com/spf13/cobra")[0]["scope"], "direct")
        self.assertEqual(by(deps, "golang.org/x/mod")[0]["scope"], "indirect")
        self.assertEqual(by(deps, "golang.org/x/sys")[0]["version"], "v0.47.0")
        ledger = by(deps, "github.com/codesweep-ai/ledger")[0]
        self.assertEqual(ledger["scope"], "tool", "a module providing a tool is a direct dependency")
        self.assertTrue(ledger["internal"])
        go = by(deps, "go")[0]
        self.assertEqual((go["ecosystem"], go["version"], go["sources"][0]["line"]), ("runtime", "1.27.0", 3))

    def test_a_tool_is_named_on_the_source_of_the_longest_module_it_extends(self):
        text = ("module m\n\ngo 1.27.0\n\ntool (\n\tgolang.org/x/tools/cmd/deadcode\n\tgolang.org/x/tools/gopls\n)\n\n"
                "require (\n\tgolang.org/x/tools v0.49.0 // indirect\n\tgolang.org/x/tools/gopls v0.21.0 // indirect\n)\n")
        deps = extract.go_mod(text, "go.mod", ORG)
        self.assertEqual(by(deps, "golang.org/x/tools")[0]["sources"][0]["tools"], ["golang.org/x/tools/cmd/deadcode"])
        self.assertEqual(by(deps, "golang.org/x/tools/gopls")[0]["sources"][0]["tools"], ["golang.org/x/tools/gopls"])


class Files:
    """A repository of these paths and texts, as extract.repository reads one."""

    def __init__(self, files):
        self._files = files

    def files(self):
        return sorted(self._files)

    def read(self, rel):
        return self._files.get(rel)


class Npm(unittest.TestCase):
    def test_a_project_that_installs_through_npmrevs_names_it_on_its_own_packages(self):
        pkg = json.dumps({"dependencies": {"@codesweep-ai/ui": "0.3.1-dev.20260922213837.7d44127", "react": "18.3.1"}})
        with_script = extract.repository(Files({"apps/viewer/package.json": pkg, "scripts/with-npmrevs.sh": "#!/bin/sh\n"}), ORG)
        installer = {d["name"]: d.get("installer") for d in with_script["dependencies"]}
        self.assertEqual(installer, {"@codesweep-ai/ui": "scripts/with-npmrevs.sh", "react": None})
        without = extract.repository(Files({"apps/viewer/package.json": pkg}), ORG)
        self.assertEqual([d.get("installer") for d in without["dependencies"]], [None, None])

    def test_each_installed_package_names_the_declared_dependencies_that_bring_it_in(self):
        pkg = json.dumps({"dependencies": {"a": "^1.0.0", "w": "*"}, "devDependencies": {"b": "^1.0.0"}})
        lock = json.dumps({"packages": {
            "": {},
            "node_modules/a": {"version": "1.0.0", "dependencies": {"c": "^1.0.0"}},
            "node_modules/b": {"version": "1.0.0", "dependencies": {"c": "^2.0.0"}},
            "node_modules/b/node_modules/c": {"version": "2.0.0"},
            "node_modules/c": {"version": "1.0.0", "peerDependencies": {"d": "*"}},
            "node_modules/d": {"version": "1.0.0"},
            "node_modules/w": {"resolved": "packages/w", "link": True},
            "packages/w": {"version": "0.1.0", "dependencies": {"d": "*"}},
        }})
        _, installed = extract.npm(pkg, lock, "package.json", "package-lock.json", ORG)
        via = {(i["name"], i["version"]): i["via"] for i in installed}
        self.assertEqual(via[("c", "1.0.0")], ["a"], "Node resolves b's c to the copy nested under b")
        self.assertEqual(via[("c", "2.0.0")], ["b"])
        self.assertEqual(via[("d", "1.0.0")], ["a", "w"], "through a peer, and through a workspace link")

    def test_lockfile_resolves_ranges_and_lists_what_it_installs(self):
        pkg = json.dumps({
            "dependencies": {"react": "^18.3.1", "@codesweep-ai/ui": "0.3.1-dev.20260909170256.1638d27"},
            "devDependencies": {"vite": "^8.0.14"},
            "optionalDependencies": {"@codesweep-ai/lint-linux-x64": "0.0.0"},
            "engines": {"node": ">=22.13"},
        }, indent=2)
        lock = json.dumps({"packages": {
            "": {},
            "node_modules/react": {"version": "18.3.1"},
            "node_modules/vite": {"version": "8.2.2", "dev": True},
            "node_modules/vite/node_modules/esbuild": {"version": "0.21.5", "dev": True},
        }})
        deps, installed = extract.npm(pkg, lock, "package.json", "package-lock.json", ORG)
        react = by(deps, "react")[0]
        self.assertEqual((react["version"], react["constraint"]), ("18.3.1", "^18.3.1"))
        self.assertEqual(by(deps, "vite")[0]["scope"], "dev")
        self.assertTrue(by(deps, "@codesweep-ai/ui")[0]["internal"])
        self.assertEqual(by(deps, "@codesweep-ai/lint-linux-x64"), [], "a publish placeholder is not a dependency")
        node = by(deps, "node")[0]
        self.assertEqual((node["scope"], node["cycle"]), ("engines", "22"))
        esbuild = [i for i in installed if i["name"] == "esbuild"][0]
        self.assertFalse(esbuild["direct"])

    def test_a_hoisted_package_is_not_direct(self):
        pkg = json.dumps({"dependencies": {"react": "^18.3.1"}})
        lock = json.dumps({"packages": {"": {}, "node_modules/react": {"version": "18.3.1"},
                                        "node_modules/loose-envify": {"version": "1.4.0"}}})
        _, installed = extract.npm(pkg, lock, "package.json", "package-lock.json", ORG)
        flags = {i["name"]: i["direct"] for i in installed}
        self.assertEqual(flags, {"react": True, "loose-envify": False})


class Workflow(unittest.TestCase):
    TEXT = """jobs:
  smoke-macos-arm64:
    runs-on: [self-hosted, macOS, ARM64]
  build:
    # image: not-an-image
    runs-on: ubuntu-26.04
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
        include:
          - os: macos-15-intel
            label: macos-arm64 / self-hosted
    steps:
      - uses: Vampire/setup-wsl@v7
        with:
          distribution: Ubuntu-24.04
      - run: go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
      - uses: actions/checkout@v7
      - uses: codesweep-ai/dashboards/action@4c204c69b8b2b79a101c65520b86944de06942a0
      - uses: ./action
      - uses: actions/setup-node@v6
        with:
          node-version: "22.13"
"""

    def test_actions_runtimes_and_runners(self):
        deps = extract.workflow(self.TEXT, ".github/workflows/ci.yml", ORG)
        checkout = by(deps, "actions/checkout")[0]
        self.assertTrue(checkout["floating"])
        internal = by(deps, "codesweep-ai/dashboards/action")[0]
        self.assertEqual((internal["subpath"], internal["pinned_sha"], internal["internal"]), ("action", True, True))
        self.assertEqual(by(deps, "node")[0]["version"], "22.13")
        runners = sorted(d["version"] for d in deps if d["name"].startswith("runner/") and not d.get("self_hosted"))
        self.assertEqual(runners, ["macos-15-intel", "macos-latest", "ubuntu-26.04", "ubuntu-latest"])
        self.assertFalse(any(d["ecosystem"] == "image" and d["name"].endswith("not-an-image") for d in deps))

    def test_self_hosted_runners_wsl_and_run_steps(self):
        deps = extract.workflow(self.TEXT, ".github/workflows/ci.yml", ORG)
        hosted = by(deps, "runner/self-hosted")
        self.assertEqual(len(hosted), 1, "a matrix display label is not a runner")
        self.assertEqual((hosted[0]["version"], hosted[0]["os"]), ("macOS ARM64", "macos"))
        wsl = by(deps, "wsl/ubuntu")[0]
        self.assertEqual(wsl["version"], "24.04")
        self.assertEqual(by(deps, "github.com/rhysd/actionlint")[0]["version"], "v1.7.12")


class Containerfile(unittest.TestCase):
    TEXT = r"""FROM registry.fedoraproject.org/fedora:44

RUN dnf update -y && dnf install -y \
  curl git gcc-c++ \
  openssl-devel && \
  dnf clean all

ARG NVIM_VERSION=0.12.5
RUN curl -fsSL -o /tmp/nvim.tar.gz \
  "https://github.com/neovim/neovim/releases/download/v${NVIM_VERSION}/nvim-linux-x86_64.tar.gz"

ARG GO_VERSION=1.27.0
RUN curl -fsSL -o /tmp/go.tar.gz "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz"

ARG CODEX_VERSION=0.152.1
RUN base="https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}" && curl "$base/x"

ARG CLAUDE_CODE_VERSION=2.1.258
RUN base=https://downloads.claude.ai/claude-code-releases && curl "$base/$CLAUDE_CODE_VERSION/linux-x64/claude"

# renovate: datasource=github-releases depName=firecracker-microvm/firecracker
ARG FC_VERSION=1.16.0

RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.7/install.sh | bash \
  && bash -c 'nvm install v24.19.0' \
  && pyenv install -s 3.14.7 \
  && GOBIN=/usr/local/bin go install golang.org/x/tools/cmd/deadcode@v0.49.0

ARG AGENTS_REF
FROM ${AGENTS_REF}
"""

    def setUp(self):
        self.deps, self.unplaced = extract.containerfile(self.TEXT, "Containerfile", ORG)

    def test_base_image_and_packages(self):
        fedora = by(self.deps, "registry.fedoraproject.org/fedora")[0]
        self.assertEqual(fedora["version"], "44")
        pkgs = sorted(d["name"] for d in self.deps if d["ecosystem"] == "package")
        self.assertEqual(pkgs, ["curl", "gcc-c++", "git", "openssl-devel"])
        self.assertTrue(all(d["release"] == "44" for d in self.deps if d["ecosystem"] == "package"))

    def test_arg_pins_find_their_upstream_from_the_url(self):
        nvim = by(self.deps, "github.com/neovim/neovim")[0]
        self.assertEqual((nvim["version"], nvim["package"], nvim["tag_prefix"]), ("0.12.5", "neovim/neovim", "v"))
        codex = by(self.deps, "github.com/openai/codex")[0]
        self.assertEqual(codex["tag_prefix"], "rust-v")
        go = [d for d in self.deps if d["ecosystem"] == "runtime" and d["name"] == "go"][0]
        self.assertEqual(go["version"], "1.27.0")

    def test_a_pin_nobody_can_place_is_reported(self):
        self.assertEqual([u[0] for u in self.unplaced], ["CLAUDE_CODE_VERSION"])

    def test_renovate_annotation(self):
        fc = by(self.deps, "github.com/firecracker-microvm/firecracker")[0]
        self.assertEqual((fc["version"], fc["declared"]), ("1.16.0", True))

    def test_installers_in_run(self):
        self.assertEqual(by(self.deps, "github.com/nvm-sh/nvm")[0]["version"], "v0.40.7")
        runtimes = {(d["name"], d["version"]) for d in self.deps if d["ecosystem"] == "runtime"}
        self.assertIn(("node", "24.19.0"), runtimes)
        self.assertIn(("python", "3.14.7"), runtimes)
        self.assertEqual(by(self.deps, "golang.org/x/tools")[0]["version"], "v0.49.0")

    def test_an_arg_with_no_default_is_not_an_image(self):
        self.assertFalse(any(d["ecosystem"] == "image" and "$" in d["name"] for d in self.deps))
        self.assertEqual(len([d for d in self.deps if d["ecosystem"] == "image"]), 1)


class EnvAndManifests(unittest.TestCase):
    def test_env_file_images(self):
        deps = extract.env_file("# c\nAGENTS_REF=ghcr.io/codesweep-ai/sandbox-agents:v0.0.0-20260907215745-bea6812a16c7\n",
                                "image/tiers.env", ORG)
        self.assertTrue(deps[0]["internal"])
        self.assertEqual(deps[0]["variable"], "AGENTS_REF")

    def test_deployment_images(self):
        deps = extract.manifest_yaml("spec:\n  containers:\n    - image: gcr.io/distroless/static-debian12:nonroot\n",
                                     "deploy/vcr.yaml", ORG)
        self.assertEqual((deps[0]["name"], deps[0]["version"]), ("gcr.io/distroless/static-debian12", "nonroot"))


class DeclaredPins(unittest.TestCase):
    def test_match_and_line(self):
        pin = {"path": "internal/fcdisk/build.go", "match": 'DefaultFCVersion = "v?([^"]+)"',
               "datasource": "github", "package": "firecracker-microvm/firecracker",
               "name": "github.com/firecracker-microvm/firecracker"}
        rec = extract.declared_pin(pin, 'package fcdisk\n\nconst DefaultFCVersion = "v1.16.0"\n')
        self.assertEqual((rec["version"], rec["sources"][0]["line"]), ("1.16.0", 3))

    def test_a_pattern_that_no_longer_matches(self):
        self.assertIsNone(extract.declared_pin({"path": "x", "match": "nope=(\\S+)", "datasource": "github"}, "yes=1"))


class Merge(unittest.TestCase):
    def test_same_dependency_same_version_folds_its_sources(self):
        a = extract._dep("image", "runner/ubuntu", "26.04", "ci.yml", 3, scope="ci")
        b = extract._dep("image", "runner/ubuntu", "26.04", "release.yml", 9, scope="ci")
        merged = extract.merge([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["sources"]), 2)


if __name__ == "__main__":
    unittest.main()


class About(unittest.TestCase):
    def test_a_package_url_names_the_upstream(self):
        rec = extract.about_file("about_resource: axe.min.js\nname: axe-core\nversion: 4.10.2\n"
                                 "package_url: pkg:npm/axe-core@4.10.2\nlicense_expression: mpl-2.0\n"
                                 "spdx_license_expression: MPL-2.0\n", "vendor/axe-core/axe.min.js.ABOUT")
        self.assertEqual((rec["ecosystem"], rec["name"], rec["version"], rec["datasource"], rec["scope"], rec["licenses"]),
                         ("native", "axe-core", "4.10.2", "npm", "vendored", ["MPL-2.0"]))

    def test_a_download_url_names_the_upstream(self):
        rec = extract.about_file("name: tini\nversion: 0.19.0\n"
                                 "download_url: https://github.com/krallin/tini/releases/download/v0.19.0/tini\n", "bin/tini.ABOUT")
        self.assertEqual((rec["name"], rec["package"], rec["tag_prefix"]), ("github.com/krallin/tini", "krallin/tini", "v"))

    def test_without_a_version_there_is_no_record(self):
        self.assertIsNone(extract.about_file("name: something\n", "x.ABOUT"))

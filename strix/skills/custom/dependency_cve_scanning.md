---
name: dependency-cve-scanning
description: Supply-chain / SCA playbook — scan repository lockfiles for known dependency CVEs and report them with create_dependency_report (no dynamic PoC required)
---

# Dependency / Supply-Chain CVE Scanning (SCA)

Use this skill on white-box / repository scans to make sure a repository pinning a
**known-vulnerable dependency** is actually reported as a finding, instead of being
discovered and then silently dropped because it cannot be dynamically exploited.

Known-CVE dependency findings are a first-class deliverable. Report each one with
the dedicated `create_dependency_report` tool.

## Why this skill exists

A vulnerable dependency pinned in a lockfile (e.g. `lodash@4.17.4` with a known
prototype-pollution CVE) usually cannot be dynamically PoC'd from the outside —
the vulnerable code path may not even be reachable from a running endpoint. The
normal "no report without a dynamic PoC" rule would suppress it. For these
findings the proof is the **lockfile entry + scanner output + published
advisory**, not an exploit script. This is the one explicit exception to the
dynamic-validation rule, and it exists only for `create_dependency_report`.

## Scan procedure

Run from the repo root and store output in the shared artifact directory used by
the source-aware pass:

```bash
ART=/workspace/.strix-source-aware
mkdir -p "$ART"

# Record the vuln DB age so a stale DB is a visible signal, not a silent clean scan.
trivy version --format json 2>/dev/null | tee "$ART/trivy-version.json"
# inspect .VulnerabilityDB.UpdatedAt / NextUpdate

# Lockfile/manifest -> known-CVE matching. Try a best-effort DB refresh first so a
# sandbox with egress gets the freshest CVEs; if the update fails, fall back to the
# cached DB instead of failing the scan. --offline-scan keeps per-package advisory
# lookups offline.
# --list-all-pkgs includes the package graph (Relationship + DependsOn) needed
# to attribute transitive CVEs to the direct dependency that introduces them.
trivy fs --scanners vuln --timeout 30m --offline-scan --list-all-pkgs \
  --format json --output "$ART/trivy-sca.json" . \
  || trivy fs --scanners vuln --timeout 30m --offline-scan --skip-db-update --list-all-pkgs \
       --format json --output "$ART/trivy-sca.json" . \
  || true
```

If `.VulnerabilityDB.UpdatedAt` is more than a few weeks old (the sandbox had no
egress to refresh it), treat it as a scan limitation and note it in the
`assumptions` of dependency findings — a stale DB that still returns *some* results
will not trip the "zero results is suspicious" heuristic, so its age is the only
staleness signal.

Trivy reads the lockfiles/manifests it finds, including:
`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`,
`requirements.txt`, `Pipfile.lock`, `go.mod`/`go.sum`, `Gemfile.lock`,
`pom.xml`/`gradle.lockfile`, `Cargo.lock`, `composer.lock`, etc.

If trivy returns zero vulnerabilities on a repo with dependencies, treat it as
suspicious: confirm the vuln DB is present (`trivy-version.json`) and that
lockfiles exist.

## Interpreting results

For each entry under `.Results[].Vulnerabilities[]` in `trivy-sca.json`, collect:

- `VulnerabilityID` — the CVE (or GHSA; prefer the CVE if both are present)
- `PkgName` and `InstalledVersion` — the affected package + pinned version
- `FixedVersion` — the version that resolves it
- `Target` — the lockfile path it came from
- `.Results[].Type` (e.g. `npm`, `pip`, `gomod`, `pom`, `gemspec`, `cargo`) — the
  package ecosystem; normalize to the registry name lowercased (`npm`, `pypi`,
  `go`, `maven`, `rubygems`, `cargo`, `composer`, `nuget`, ...)
- `CVSS` — the published advisory base score
- `PrimaryURL` / references — to verify the advisory

Deduplicate by `(CVE, PkgName, InstalledVersion)`. File one
`create_dependency_report` per CVE — do not batch multiple CVEs into one report.

### Attribute transitive CVEs to the direct dependency

With `--list-all-pkgs`, each `.Results[].Packages[]` entry carries `ID`
(`name@version`), `Relationship` (`direct` / `indirect`) and `DependsOn` (the
`ID`s it resolves to). For every vulnerable package that is **indirect**, walk
the `DependsOn` graph backwards to find the `direct` package(s) whose closure
contains it, then pass to `create_dependency_report`:

- `introduced_by` — the direct dependency as `name@version` (e.g.
  `express@4.18.1`). If several direct dependencies pull it in, pick the
  primary one and name the rest in `technical_analysis`.
- `dependency_path` — the shortest resolution chain from that direct
  dependency to the vulnerable package, joined with ` > ` (e.g.
  `express@4.18.1 > body-parser@1.20.0 > qs@6.10.2`).
- Omit both when the vulnerable package is itself a direct dependency.

If the ecosystem's lockfile gives trivy no graph (`DependsOn` absent), derive
the chain from the package manager instead (`npm ls <pkg>`, `pnpm why <pkg>`,
`yarn why <pkg>`, `pipdeptree --reverse -p <pkg>`, `go mod graph`,
`mvn dependency:tree`, ...) — and if that also fails, leave the fields out
rather than guessing.

For transitive findings, `remediation_steps` must be actionable at the
**direct-dependency level**: upgrading the vulnerable package directly is
usually impossible from the app's own manifest. Say which direct dependency to
bump (a version whose closure resolves the fixed version), or how to force the
resolution (npm `overrides` / yarn `resolutions` / pnpm `pnpm.overrides` /
Maven `dependencyManagement` / Gradle resolution strategy / `go mod edit`),
not just "upgrade <vulnerable pkg> to <fixed>".

### Reachability is a confidence modifier, not a gate

Do NOT suppress or downgrade a known CVE just because you could not prove the
vulnerable code path is reachable. Report it, set `advisory_cvss` from the
advisory, and use `assumptions` to note reachability (e.g. "the vulnerable
`template()` API does not appear to be imported in application code, so practical
exploitability is uncertain"). If you *can* show reachability or chain it into a
dynamic exploit, do that and report it as a normal dynamic finding with
`create_vulnerability_report` instead.

## Reporting

Report each confirmed known CVE with the dedicated `create_dependency_report`
tool (NOT `create_vulnerability_report` — that tool is for dynamically validated
findings and rejects empty PoC fields):

- Set `cve` to the verified `CVE-YYYY-NNNNN` id (required). If you only have a
  GHSA, look up the mapped CVE; if there is genuinely no CVE, do not report it
  with this tool.
- There are no PoC fields — `create_dependency_report` does not take
  `poc_description` / `poc_script_code` / `code_locations`. The proof lives in
  `description` and `technical_analysis` (scanner output + advisory).
- **Always fill the structured dependency fields** (they power the dedicated
  dependency-report card; do not leave them only in free-text):
  - `package_name` — `PkgName` (required).
  - `installed_version` — `InstalledVersion` (required).
  - `package_ecosystem` — normalized ecosystem from `.Results[].Type` (lowercased,
    e.g. `npm`, `pypi`, `go`, `maven`, `rubygems`, `cargo`) (required).
  - `fixed_version` — `FixedVersion` (leave empty only if no fix is published).
- Reference the repo-relative `Target` lockfile path in `description` /
  `technical_analysis` (no leading slash) so the finding is traceable.
- Put the concrete proof in `description` / `technical_analysis`: package name,
  installed/affected version, fixed version, lockfile path, and the relevant
  trivy output excerpt.
- **Always set `advisory_cvss` to the published advisory base score (0.0–10.0).**
  Severity is derived *solely* from this number: read it off the advisory (`CVSS`
  in trivy output, or the NVD/GHSA page) and pass the real value. The tool rejects
  a call that omits it, because guessing a score both inflates low CVEs and
  deflates critical ones.
- Set `cwe` to the most specific `CWE-NNN` when the advisory names one.
- Do NOT cap severity at LOW just because there is no dynamic reproduction — use
  the advisory score.
- Use `assumptions` for reachability/exploitability caveats.

Verify the CVE with `web_search` when available before reporting. Never guess or
hallucinate a CVE id.

## Anti-patterns

- Do not report a dependency CVE with `create_vulnerability_report`; use
  `create_dependency_report`.
- Do not report a finding without a verified CVE id.
- Do not batch multiple CVEs into one report.
- Do not omit `advisory_cvss` — the tool rejects it, and it is the single input
  that determines dependency severity.
- Do not silently drop a known CVE because it lacks a dynamic PoC — that is the
  exact failure this skill prevents.
- Do not downgrade advisory severity for lack of dynamic reproduction.

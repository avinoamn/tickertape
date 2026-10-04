# Releasing

How a version of tickertape is cut, what the release workflow does, and how to recover when it fails. For deploying to a cluster see [operations.md](operations.md).

## What is versioned

Four things are released, each with its own [SemVer](https://semver.org/) version:

| Component | Released as | Version lives in | Git tag |
|---|---|---|---|
| `poller` | image `ghcr.io/<owner>/tickertape-poller:X.Y.Z` | `services/poller/VERSION` | `poller-vX.Y.Z` |
| `ner` | image `ghcr.io/<owner>/tickertape-ner:X.Y.Z` | `services/ner/VERSION` | `ner-vX.Y.Z` |
| `laya` | image `ghcr.io/<owner>/tickertape-laya:X.Y.Z` | `services/laya/VERSION` | `laya-vX.Y.Z` |
| `chart` | Helm chart `tickertape-X.Y.Z.tgz` (a GitHub Release asset) | `version` in `charts/tickertape/Chart.yaml` | `chart-vX.Y.Z` |

- **A service version is the version of its code.** The three services change at different rates, so they are released independently: a poller fix does not give ner and laya a new version, and does not restart them.
- **The chart pins one image tag per service** (`poller.image.tag`, `ner.image.tag`, `laya.image.tag` in `values.yaml`). **A deployment is a chart version**: deploying `chart-v0.3.0` deploys exactly the set of images it pins. There is no implicit "latest": a tag is required, and images are never tagged `latest` or moved.
- **The Laya model has its own version again**: the Hugging Face commit in `laya.revision`. Changing it is a values change, released as a new chart version.
- **Pre-releases** such as `ner-v0.2.0-rc.1` follow the same process and are marked as pre-releases on GitHub.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/), and `CHANGELOG.md` records what changed per released component under a heading `## <component> <version> - <date>` ([Keep a Changelog](https://keepachangelog.com/) style). Repository-only changes (CI, docs, tests) are in the git history, not in the changelog.

What counts as a bump: breaking changes (schema, configuration, deployment procedure) are a MAJOR (MINOR while the version is 0.x); new behaviour is a MINOR; fixes are a PATCH. For the chart: changed templates or values keys follow the same rules, and a pure pin bump (a new service image) is a PATCH.

## Releasing a service

1. **Prepare in a pull request** titled `chore(release): ner 0.2.0` (for example):
   - write the new version in `services/ner/VERSION`;
   - add `## ner 0.2.0 - YYYY-MM-DD` to `CHANGELOG.md` with the entries that belong to it.
2. **Merge it** (CI must pass), then **tag that commit on `main`**:
   ```sh
   git switch main && git pull
   git tag -a ner-v0.2.0 -m "ner 0.2.0" && git push origin ner-v0.2.0
   ```
3. The workflow publishes the image and creates a GitHub Release. The first time a service is published, set its GHCR package to public (see below).

## Releasing the chart (what a deployment uses)

A chart release is what makes new service versions deployable. In a release PR titled `chart: release 0.3.0`:

- set the pins in `values.yaml` to the released service versions you want deployed (the images must already be published: release the services first);
- bump `version` in `Chart.yaml` and add `## chart 0.3.0 - YYYY-MM-DD` to the changelog (what changed, including the pin bumps);
- merge it, then tag `chart-v0.3.0` on `main` as above.

One PR can bump a service and the chart together: merge it, push the service tag, wait for the image, then push the chart tag. The chart's check refuses to release while a pinned image does not exist, so the order is enforced.

Then deploy: `make deploy` from the tag's checkout (see [operations.md](operations.md)). Only the services whose pin changed restart.

## What the release workflow does

`.github/workflows/release.yml`, triggered by a `<component>-vX.Y.Z` tag:

1. **Verify**, before anything is pushed: the tag has a known component and valid SemVer; the version in the tree (`VERSION` file or `Chart.yaml`) equals the tag; `CHANGELOG.md` has the section; the tagged commit is on `main`; and the CI workflow succeeded for that exact commit.
2. **For a service tag:** build that image for `linux/amd64` and push it with the version tag and OCI labels (source repository, version, revision, creation time, license), using a layer cache shared with CI; check the image can be pulled **anonymously** (a cluster pulls without credentials); create a GitHub Release with the changelog section.
3. **For a chart tag:** `helm lint`; check that `poller`, `ner` and `laya` images pinned by the chart exist in GHCR and are publicly readable; `helm package`; create a GitHub Release with the changelog section, the list of pinned images and the chart archive.

Permissions are per job and minimal: only the image job writes packages, only the release job writes contents.

### Rehearsal

Running the workflow manually (Actions, "Release", Run workflow, pick a component; or `gh workflow run release.yml -f component=ner`) does the build or the packaging against the current tree, without pushing anything and without creating a release. Use it to check a change to the workflow itself.

## Deploying from GitHub Actions

The `Deploy` workflow (`.github/workflows/deploy.yml`) deploys a released chart from GitHub, started by hand. It is the alternative to running `make deploy` on your own machine, and it ends up in the same place: `helm upgrade --install --atomic` of `charts/tickertape` as the namespace-scoped `deployer` account.

**Using it:** Actions, Deploy, Run workflow. Inputs:

| Input | Meaning |
|---|---|
| `version` | The chart version, for example `0.3.1`. The tag `chart-v0.3.1` must exist, be on `main`, and have a GitHub Release. |
| `dry_run` (default **on**) | A rehearsal: joins the tailnet, authenticates, shows what would change (`kubectl diff`) and does a server-side dry run, and changes nothing. Run it first; run again with it unchecked to deploy. |
| `prepull` (default on) | Pulls the images the chart pins onto the node with throw-away pods before upgrading, so the pause of ner and laya is only the model load. |

The job then waits for a reviewer: it runs in the GitHub Environment `production`, so GitHub asks you to approve ("Review deployments") before anything with credentials starts. After the upgrade it runs a smoke test: rollout status of ner and laya, the pods and the item counts, and an HTTP check of both UIs on their NodePorts. If the upgrade fails, `--atomic` rolls back to the previous revision.

**Why it is safe to have this on a public repository:**

- Only people with write access can start it, it runs only from `main` (the Environment is limited to that branch), and it needs your approval each time. Forks and pull requests never see the Environment's secrets.
- It deploys only a tag that is on `main` and has a release, and the release workflow has already checked that tag's images exist.
- The cluster account is the `deployer` ServiceAccount: it can manage workloads in the `tickertape` namespace and nothing else.
- There is no stored Tailscale credential. The runner proves who it is with a short-lived GitHub identity token (OIDC federation), joins the tailnet as an **ephemeral node tagged `tag:ci`**, and the Tailscale policy lets that tag reach only the cluster API and the UIs' ports.
- The one stored credential is the `deployer` token, which only works inside the tailnet and only inside one namespace.

### One-time setup

Do these in order. Steps 1 and 2 are in the Tailscale admin console, 3 to 5 in GitHub and your terminal.

**1. Tailscale policy** (Access controls, the policy file). Add the tag and a rule that lets it reach only the node's API and UI ports, replacing `<node-tailscale-ip>` with the node's address:

```json
"tagOwners": { "tag:ci": ["autogroup:admin"] },
"grants": [
  { "src": ["tag:ci"], "dst": ["<node-tailscale-ip>"], "ip": ["tcp:6443", "tcp:30002", "tcp:30003", "tcp:30004"] }
]
```

Check whether your policy still has the default "allow everything" rule (`"src": ["*"], "dst": ["*"], "ip": ["*"]`): `*` also matches tagged devices, so the CI node would be able to reach everything. Narrow its `src` to `["autogroup:member"]` (your own devices) unless other tagged devices rely on it, and use the console's policy preview to see what changes before saving.

**2. Tailscale trust credential** (Settings, Trust credentials, Credential, OpenID Connect):

- Issuer: **GitHub Actions**.
- Subject: `repo:<owner>/tickertape:environment:production`
- Scope: `auth_keys` (write), with the tag `tag:ci`.
- Copy the **client ID** and the **audience** it shows.

**3. The GitHub Environment `production`** (Settings, Environments): required reviewer = you, deployment branches limited to `main`. (Already created for this repository; see "Repository settings this relies on".)

**4. Environment secrets** (Settings, Environments, production, Secrets), three of them:

| Secret | Value |
|---|---|
| `TS_OAUTH_CLIENT_ID` | the client ID from step 2 |
| `TS_AUDIENCE` | the audience from step 2 |
| `KUBE_TOKEN` | the `deployer` token |

For the token, pipe it straight from the cluster into GitHub so it never appears on screen or in shell history:

```sh
scripts/kc.sh get secret deployer-token -n tickertape -o jsonpath='{.data.token}' | base64 -d | gh secret set KUBE_TOKEN --env production --repo <owner>/tickertape
```

**5. Environment variables** (not secret, but private to collaborators):

| Variable | Value |
|---|---|
| `KUBE_SERVER` | `https://<node-tailscale-ip>:6443` |
| `KUBE_TLS_SERVER_NAME` | the DNS name in the API certificate (here `computa`): the certificate has no IP address, so the IP is dialled with this name |
| `KUBE_CA_DATA` | the cluster CA certificate, base64: `scripts/kc.sh get secret deployer-token -n tickertape -o jsonpath='{.data.ca\.crt}'` |
| `NODE_HOST` | the node's Tailscale address, for the smoke test's UI checks (optional) |

Then run the workflow once with `dry_run` on. It proves the whole chain (OIDC to Tailscale, the policy, the token, the API) without touching the cluster.

### Rotating and revoking

- **The deployer token** never expires. To rotate: delete the Secret `deployer-token` in `tickertape`, run `make bootstrap` (it recreates the token and your local kubeconfig), and set `KUBE_TOKEN` again.
- **Tailscale access:** delete the trust credential in the Tailscale console (nothing is stored in GitHub to clean up apart from the client ID and audience), or remove the `tag:ci` grant.
- **Stop deploys from GitHub entirely:** delete the Environment's secrets, or disable the Deploy workflow in Actions.

### When it fails

| Where | Likely cause |
|---|---|
| "Join the tailnet" fails | The trust credential's subject does not match `repo:<owner>/tickertape:environment:production`, the scope or tag is wrong, or `TS_OAUTH_CLIENT_ID` / `TS_AUDIENCE` are wrong. |
| "Reach the cluster" fails | The policy does not let `tag:ci` reach the node on 6443 (step 1), or `KUBE_SERVER` is wrong. A brand new node needs up to a minute to be accepted by its peers; the step retries. |
| "x509: certificate is valid for ..." | `KUBE_TLS_SERVER_NAME` is not a name in the API certificate. |
| "Unauthorized" / "forbidden" | `KUBE_TOKEN` is stale (the Secret `deployer-token` was recreated) or belongs to another account. |
| Upgrade times out | The release is rolled back by `--atomic`; read the "What the cluster looks like after a failure" step, and see the troubleshooting table in [operations.md](operations.md). |

## One-time setup

- **Package visibility.** Clusters pull the images anonymously, so each package (`tickertape-poller`, `tickertape-ner`, `tickertape-laya`) must be public. For this repository GHCR made them public on their own, because the packages are linked to the public repository through the image's source label, and the workflow's anonymous pull check confirms it on every release. If a package ever shows up as private (a fork, or a changed setting), the check fails with the fix: GitHub profile, Packages, the package, Package settings, Change visibility, Public; then re-run that job and the release continues.

## Repository settings this relies on

These live in GitHub, not in the repository, so they are listed here:

- **`main` is protected:** pull requests only, the `CI passed` check must be green and the branch up to date, no force pushes or deletion, linear history, conversations resolved, rules apply to admins. Only squash merging is enabled, with branches deleted after merge.
- **Environment `production`:** the Deploy workflow's job runs in it. Required reviewer: the owner; deployment branches: `main` only. It holds the deploy credentials (secrets `TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE`, `KUBE_TOKEN`; variables `KUBE_SERVER`, `KUBE_TLS_SERVER_NAME`, `KUBE_CA_DATA`, `NODE_HOST`), so nothing outside that Environment can use them.
- **Release tags are immutable:** the ruleset "Release tags are immutable" blocks deleting and moving any tag matching `poller-v*`, `ner-v*`, `laya-v*` or `chart-v*`, with no bypass (so a careless `git push -f` cannot move a published version). Creating tags is not restricted.

## When something fails

| Where it failed | What to do |
|---|---|
| **Verify** (tag format, version file, changelog, not on main, CI not green) | Nothing was published. Delete the tag, fix it in a PR, and tag again. The tag ruleset forbids deleting release tags, so disable it for a moment, delete the tag, and re-enable it: `gh api -X PUT repos/<owner>/tickertape/rulesets/<id> -f enforcement=disabled`, then `git push origin :refs/tags/<tag>` and `git tag -d <tag>`, then `-f enforcement=active` (the id is in Settings, Rules, Rulesets). Only do this for a tag whose workflow failed before publishing. |
| **Image build**, or **chart pin check** | Nothing was published for that component. Fix the cause and re-run the failed job, or delete the tag and start over if the fix needs a new commit. |
| **Anonymous pull check** | The package is private: make it public (see "Package visibility") and re-run the job. |
| **After an image was pushed** (release creation failed, or a bug found) | Do not move or reuse the tag: its image exists and may have been pulled. Re-run the failed job if it is only the release step; otherwise fix forward with the next patch version. |

## Rolling back a deployment

A release is immutable, so going back means deploying an older chart: `scripts/helm.sh history tickertape` and `scripts/helm.sh rollback tickertape <revision>`, or deploy the previous `chart-v…` tag.

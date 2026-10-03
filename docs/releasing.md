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

## One-time setup

- **Package visibility.** Clusters pull the images anonymously, so each package (`tickertape-poller`, `tickertape-ner`, `tickertape-laya`) must be public. For this repository GHCR made them public on their own, because the packages are linked to the public repository through the image's source label, and the workflow's anonymous pull check confirms it on every release. If a package ever shows up as private (a fork, or a changed setting), the check fails with the fix: GitHub profile, Packages, the package, Package settings, Change visibility, Public; then re-run that job and the release continues.

## When something fails

| Where it failed | What to do |
|---|---|
| **Verify** (tag format, version file, changelog, not on main, CI not green) | Nothing was published. Delete the tag (`git push origin :refs/tags/<tag>` and `git tag -d <tag>`), fix it in a PR, and tag again. |
| **Image build**, or **chart pin check** | Nothing was published for that component. Fix the cause and re-run the failed job, or delete the tag and start over if the fix needs a new commit. |
| **Anonymous pull check** | The package is private: make it public (see "Package visibility") and re-run the job. |
| **After an image was pushed** (release creation failed, or a bug found) | Do not move or reuse the tag: its image exists and may have been pulled. Re-run the failed job if it is only the release step; otherwise fix forward with the next patch version. |

## Rolling back a deployment

A release is immutable, so going back means deploying an older chart: `scripts/helm.sh history tickertape` and `scripts/helm.sh rollback tickertape <revision>`, or deploy the previous `chart-v…` tag.
